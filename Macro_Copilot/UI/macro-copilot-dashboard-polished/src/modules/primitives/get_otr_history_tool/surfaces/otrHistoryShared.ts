// ============================================================================
// otrHistoryShared.ts — Per-tool helpers shared between BuildExtended.tsx
// and BuildCompact.tsx for ``get_otr_history_tool``.
// ----------------------------------------------------------------------------
// FINANCE-AWARE per-tool layer.  CATEGORICAL SCD2 TIMELINE shape — the wire
// carries the on-the-run transition log for ONE (country, tenor) sovereign
// cash-bond slot: a current-OTR identity snapshot (CUSIP / ISIN /
// vendor_ticker / maturity_date, all nullable per P6 honest absence) plus
// the chronological list of SCD2 windows intersecting the lookback.  There
// is NO numeric time series anywhere in the payload.
//
// The honesty rules this layer carries (FP9 — no client-side compute):
//   * Every field is a pure-INGEST read of macro_data.otr_history (ADR
//     0003) surfaced VERBATIM — no derived statistics (no "days on the
//     run", no roll-frequency estimates, no auction-date inference).
//   * The resolver is FORWARD-ONLY with detection-date precision (TD #27):
//     ``effective_from`` is the first CONFIRMED observation of the bond as
//     OTR, not the auction/issue date.  The wire's ``methodology_note``
//     carries the full P5 disclosure and is threaded verbatim onto the
//     extended methodology card — never re-stated as a TSX literal.
//   * Honest absence (P6): all snapshot identity fields are nullable; an
//     empty transitions list means "no resolver-observed windows", which
//     both views render explicitly rather than papering over.
//
// Both Build views fetch the SAME typed-detail endpoint
// (/api/v1/rates/detail/otr-history) per rendering_density.md §1.1 — the
// compact view just renders less of the same payload.
//
// Sibling categorical-shape precedent:
// ../../calculate_wirp_meeting_pricing_tool/surfaces/wirpMeetingPricingShared.ts.
// ============================================================================

import { useEffect, useState } from 'react';
import {
  fetchDetailOtrHistory,
  type OtrHistoryDetailParams,
} from '@/services/ratesApi';
import type {
  OtrHistoryCurrentMetrics,
  OtrHistoryOutput,
  OtrHistoryTransitionRow,
} from '@/types/rates';
import type {
  KPIDescriptor,
  MethodologyRow,
  ReferenceChip,
} from '@/components/shared/build';

// ---------------------------------------------------------------------------
// Country registry.  Mirrors the ISO-3166-alpha-2 codes the backend's
// canonicalisation validator documents (schemas.py — 'US', 'DE', 'GB',
// 'JP', 'FR', 'IT', 'ES' are the resolver-covered G7+periphery set the
// surface exposes).  Finance-aware metadata (flag, issuer label) lives in
// this per-tool layer — the shared shells are finance-blind and the
// curve-family caveat registry doesn't know bare ISO country codes.
// ---------------------------------------------------------------------------

export interface OtrCountryMeta {
  /** ISO-3166-alpha-2 country code as the wire carries it (e.g. 'US'). */
  code: string;
  /** Country flag emoji. */
  flag: string;
  /** Issuer / instrument family label (e.g. 'US Treasury'). */
  issuerLabel: string;
}

const OTR_COUNTRY_REGISTRY: Record<string, OtrCountryMeta> = {
  US: { code: 'US', flag: '🇺🇸', issuerLabel: 'US Treasury' },
  DE: { code: 'DE', flag: '🇩🇪', issuerLabel: 'German Bund' },
  GB: { code: 'GB', flag: '🇬🇧', issuerLabel: 'UK Gilt' },
  JP: { code: 'JP', flag: '🇯🇵', issuerLabel: 'Japanese JGB' },
  FR: { code: 'FR', flag: '🇫🇷', issuerLabel: 'French OAT' },
  IT: { code: 'IT', flag: '🇮🇹', issuerLabel: 'Italian BTP' },
  ES: { code: 'ES', flag: '🇪🇸', issuerLabel: 'Spanish Bono' },
};

export function otrCountryMetaFor(code: string): OtrCountryMeta | null {
  return OTR_COUNTRY_REGISTRY[code] ?? null;
}

/** Country options for the extended controls strip. */
export const OTR_COUNTRY_OPTIONS: ReadonlyArray<{ value: string; label: string }> =
  Object.values(OTR_COUNTRY_REGISTRY).map((m) => ({
    value: m.code,
    label: `${m.code} · ${m.issuerLabel}`,
  }));

/** Canonical slot tenors per sovereign_cash_bonds.yml (integer-Y form —
 *  the backend's tenor_canonicalisation convention). */
export const OTR_TENOR_OPTIONS: ReadonlyArray<{ value: string; label: string }> =
  ['2Y', '3Y', '5Y', '7Y', '10Y', '20Y', '30Y'].map((t) => ({
    value: t,
    label: t,
  }));

/** Lookback options — the single central methodology knob (PR8).
 *  Backend Pydantic bounds are [30, 3650]; YAML default 365. */
export const OTR_LOOKBACK_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: '90', label: '90d' },
  { value: '180', label: '180d' },
  { value: '365', label: '1Y (default)' },
  { value: '730', label: '2Y' },
  { value: '1825', label: '5Y' },
  { value: '3650', label: '10Y' },
];

/** Desk-canonical caveat string for the compact footer.  SHORT form of
 *  the wire's ``methodology_note`` (which carries the full TD #27 P5
 *  disclosure and is surfaced verbatim on the extended methodology
 *  card).  Forward-only + detection-date precision is the one thing a
 *  reader must know before trusting a roll date. */
export const OTR_HISTORY_COMPACT_CAVEAT =
  'Forward-only resolver log; effective dates are detection dates (first confirmed observation), not auction dates.';

// ---------------------------------------------------------------------------
// Data hook
// ---------------------------------------------------------------------------

export interface UseOtrHistoryArgs {
  country: string;
  tenor: string;
  lookbackDays?: number;
  /** As-of trade date (YYYY-MM-DD).  Undefined/empty → latest live data. */
  asOfDate?: string;
}

export interface UseOtrHistoryResult {
  data: OtrHistoryOutput | null;
  isLoading: boolean;
  errorMessage: string | null;
}

/** Single-source hook used by both Build views.  Fetches the typed-detail
 *  endpoint; re-fetches when any input changes. */
export function useOtrHistoryData(args: UseOtrHistoryArgs): UseOtrHistoryResult {
  const [data, setData] = useState<OtrHistoryOutput | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const params: OtrHistoryDetailParams = {
    country: args.country,
    tenor: args.tenor,
    lookback_days: args.lookbackDays,
    as_of_date: args.asOfDate || undefined,
  };

  useEffect(() => {
    if (!args.country || !args.tenor) {
      setData(null);
      setErrorMessage(null);
      setIsLoading(false);
      return;
    }
    let cancelled = false;
    setIsLoading(true);
    setErrorMessage(null);
    fetchDetailOtrHistory(params)
      .then((p) => {
        if (cancelled) return;
        setData(p);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setErrorMessage(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [args.country, args.tenor, args.lookbackDays, args.asOfDate]);

  return { data, isLoading, errorMessage };
}

// ---------------------------------------------------------------------------
// Display ordering — the wire is chronological ASCENDING by
// effective_from (schemas.py contract); the desk read is "what rolled
// most recently?", so BOTH views display DESCENDING.  Pure re-ordering
// of verbatim rows (FP9 — display sorting, not a statistic); the open
// window (effective_to == null) naturally sorts first.
// ---------------------------------------------------------------------------

export function sortTransitionsForDisplay(
  rows: ReadonlyArray<OtrHistoryTransitionRow>,
): OtrHistoryTransitionRow[] {
  return [...rows].sort((a, b) =>
    b.effective_from.localeCompare(a.effective_from),
  );
}

// ---------------------------------------------------------------------------
// Format helpers — local to this tool.  Formatting only (FP9 — no
// derived statistics; dates and identifiers render verbatim).
// ---------------------------------------------------------------------------

/** Short date label, e.g. '2026-05-15' → 'May 15 ’26'.  Falls back to
 *  the raw string on parse failure (never fabricates). */
export function formatShortDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  try {
    const d = new Date(`${iso}T00:00:00Z`);
    if (Number.isNaN(d.getTime())) return iso;
    const month = d.toLocaleString('en-US', { month: 'short', timeZone: 'UTC' });
    const day = d.getUTCDate();
    const year = String(d.getUTCFullYear()).slice(2);
    return `${month} ${day} ’${year}`;
  } catch {
    return iso;
  }
}

/** SCD2 window-end label — ``effective_to`` is null while the window is
 *  still open, which renders as the literal word 'current' (the wire's
 *  None-preservation contract exists exactly so consumers can recognise
 *  the open window; we surface it, not today's date). */
export function formatWindowEnd(effectiveTo: string | null | undefined): string {
  return effectiveTo ?? 'current';
}

/** The preferred display identifier for an OTR bond, in desk order:
 *  CUSIP (US convention) → ISIN (ex-US convention) → vendor_ticker.
 *  Returns the identifier KIND alongside the value so KPI captions can
 *  say which field they're showing (honest labelling, not guessing). */
export interface OtrIdentifier {
  value: string;
  kind: 'CUSIP' | 'ISIN' | 'ticker';
}

export function preferredIdentifier(row: {
  cusip?: string | null;
  isin?: string | null;
  vendor_ticker?: string | null;
}): OtrIdentifier | null {
  if (row.cusip) return { value: row.cusip, kind: 'CUSIP' };
  if (row.isin) return { value: row.isin, kind: 'ISIN' };
  if (row.vendor_ticker) return { value: row.vendor_ticker, kind: 'ticker' };
  return null;
}

// ---------------------------------------------------------------------------
// KPI descriptor builders
// ---------------------------------------------------------------------------

/** The compact view's THREE canonical headline KPIs per
 *  rendering_density.md §2.2:
 *    1. CURRENT OTR    (CUSIP-or-ticker identity, primary emphasis —
 *                       "which bond IS the benchmark right now?")
 *    2. TRANSITIONS    (count of SCD2 windows in the lookback — "how
 *                       often has this slot rolled?")
 *    3. CURRENT SINCE  (effective_from of the open window — "when did
 *                       the current benchmark take over?")
 *  THESIS Q3 documents why these vs alternatives (maturity_date and the
 *  ISIN/ticker columns live in the extended table instead).  All values
 *  are neutral-toned — a categorical identity log has no sign
 *  convention to colour. */
export function compactKPIs(
  data: OtrHistoryOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  const id = preferredIdentifier(cm);
  return [
    {
      label: 'CURRENT OTR',
      value: id ? id.value : '—',
      tone: 'neutral',
      emphasis: 'primary',
      caption: id ? id.kind : 'no open window',
    },
    {
      label: 'TRANSITIONS',
      value: `${cm.transition_count_in_window}`,
      tone: 'neutral',
      caption: `${cm.lookback_days}d window`,
    },
    {
      label: 'CURRENT SINCE',
      value: formatShortDate(cm.current_effective_from),
      tone: 'neutral',
      caption: cm.current_effective_from ? 'detection date' : undefined,
    },
  ];
}

/** The extended view's KPI strip — the compact triple plus the current
 *  bond's maturity (the slot's forward horizon).  Everything verbatim
 *  from the snapshot; honest '—' on every absent field (P6). */
export function extendedKPIs(
  data: OtrHistoryOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  const id = preferredIdentifier(cm);
  return [
    {
      label: 'CURRENT OTR',
      value: id ? id.value : '—',
      tone: 'neutral',
      caption: id ? id.kind : 'no open window',
    },
    {
      label: 'TRANSITIONS',
      value: `${cm.transition_count_in_window}`,
      tone: 'neutral',
      caption: `${cm.lookback_days}d window`,
    },
    {
      label: 'CURRENT SINCE',
      value: formatShortDate(cm.current_effective_from),
      tone: 'neutral',
      caption: cm.current_effective_from ? 'detection date' : undefined,
    },
    {
      label: 'MATURITY',
      value: formatShortDate(cm.maturity_date),
      tone: 'neutral',
      caption: cm.maturity_date ? 'current OTR bond' : undefined,
    },
  ];
}

// ---------------------------------------------------------------------------
// Methodology rows + reference chips.  The P5 disclosure is the wire's
// ``methodology_note`` — surfaced VERBATIM, never a TSX literal.  The
// structural rows around it are anchored to wire fields (slot, window,
// snapshot identity), not re-stated conventions.
// ---------------------------------------------------------------------------

export function buildMethodologyRows(
  data: OtrHistoryOutput,
): ReadonlyArray<MethodologyRow> {
  const cm: OtrHistoryCurrentMetrics = data.current_metrics;
  const id = preferredIdentifier(cm);
  return [
    {
      label: 'Slot',
      value: `${cm.country} ${cm.tenor} · sovereign cash-bond on-the-run`,
    },
    {
      label: 'Window',
      value: `${cm.lookback_days} calendar days · as of ${cm.as_of_date} · ${cm.transition_count_in_window} transition${cm.transition_count_in_window === 1 ? '' : 's'}`,
    },
    {
      label: 'Snapshot identity',
      value: id
        ? `${id.kind} ${id.value}${cm.vendor_ticker && id.kind !== 'ticker' ? ` · ${cm.vendor_ticker}` : ''}`
        : 'No OTR window currently open for this slot (honest absence).',
    },
    {
      label: 'Disclosure',
      value: data.methodology_note,
    },
  ];
}

export function buildReferenceChips(): ReadonlyArray<ReferenceChip> {
  return [
    { label: 'ADR 0003 (otr_history substrate)' },
    { label: 'ADR 0007 (OTR resolver)' },
    { label: 'TD #27 (forward-only / detection-date)' },
    { label: 'sovereign_cash_bonds.yml' },
  ];
}
