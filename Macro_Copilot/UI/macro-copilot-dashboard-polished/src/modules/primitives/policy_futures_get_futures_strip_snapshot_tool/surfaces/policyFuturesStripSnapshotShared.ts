// ============================================================================
// policyFuturesStripSnapshotShared.ts — Per-tool helpers shared between
// BuildExtended.tsx and BuildCompact.tsx for
// ``policy_futures_get_futures_strip_snapshot_tool``.
// ----------------------------------------------------------------------------
// FINANCE-AWARE per-tool layer.  This module knows that the desk-
// recognised STIR-strip object is the WHOLE STRIP on a SINGLE aligned
// as_of_date ("steep / flat / inverted"), that the per-row level is the
// implied rate in PERCENT (PR14 frozen name), that the 1-day change
// arrives in PERCENT POINTS (×100 for bps), and that the headline
// "strip slope" is a DISPLAY-ONLY front→last subtraction this layer
// performs and labels as such (FP9).  The shared shells do not.
//
// Both BuildExtended.tsx and BuildCompact.tsx fetch the SAME data (per
// rendering_density.md §1.1 — both views consume the same typed-detail
// endpoint; the compact view just renders less).  KPI builders +
// formatting + tone live here in ONE place.
// ============================================================================

import { useEffect, useState } from 'react';
import {
  fetchDetailPolicyFuturesStripSnapshot,
  type PolicyFuturesStripSnapshotDetailParams,
} from '@/services/ratesApi';
import type {
  FuturesStripSnapshotOutput,
  FuturesStripSnapshotRow,
} from '@/types/rates';
import {
  signedFixed,
  toneForChange,
  type KPIDescriptor,
  type MethodologyRow,
  type ReferenceChip,
} from '@/components/shared/build';

// ---------------------------------------------------------------------------
// Curve-family metadata.  Policy-futures families are disjoint from the
// linker / sovereign registries — flag + label live here in the per-tool
// layer (sibling precedent: policy_futures_get_futures_price_level_tool's
// policyFuturesPriceShared.ts keeps its own copy for the same reason).
// ---------------------------------------------------------------------------

export interface PolicyFuturesCurveMeta {
  family: string;
  flag: string;
  /** Short market label for the identity chip (e.g. 'SOFR'). */
  shortLabel: string;
  /** Long human-facing market name (e.g. 'US Fed SOFR strip'). */
  longLabel: string;
  /** 'RFR' (SOFR / SONIA compounded daily) or 'IBOR' (3M Euribor). */
  regime: 'RFR' | 'IBOR';
  /** Master-stem prefix for the strip slots (e.g. 'SFR' → 'SFR1'). */
  stripStemPrefix: string;
}

const CURVE_REGISTRY: Record<string, PolicyFuturesCurveMeta> = {
  SOFR_FUT: {
    family: 'SOFR_FUT',
    flag: '🇺🇸',
    shortLabel: 'SOFR',
    longLabel: 'US Fed SOFR strip',
    regime: 'RFR',
    stripStemPrefix: 'SFR',
  },
  EUR_SHORT_RATE_FUT: {
    family: 'EUR_SHORT_RATE_FUT',
    flag: '🇪🇺',
    shortLabel: 'Euribor',
    longLabel: 'ECB Euribor strip',
    regime: 'IBOR',
    stripStemPrefix: 'ER',
  },
  SONIA_FUT: {
    family: 'SONIA_FUT',
    flag: '🇬🇧',
    shortLabel: 'SONIA',
    longLabel: 'BOE SONIA strip',
    regime: 'RFR',
    stripStemPrefix: 'SFI',
  },
};

export function curveMetaFor(curveFamily: string): PolicyFuturesCurveMeta | null {
  return CURVE_REGISTRY[curveFamily] ?? null;
}

/** Curve-family options for the extended controls strip — the backend
 *  Input is a closed Literal (SOFR_FUT | EUR_SHORT_RATE_FUT | SONIA_FUT);
 *  an unknown family 422s at the Pydantic layer. */
export const POLICY_FUTURES_CURVE_OPTIONS: ReadonlyArray<{ value: string; label: string }> =
  Object.values(CURVE_REGISTRY).map((m) => ({
    value: m.family,
    label: `${m.shortLabel} · ${m.longLabel}`,
  }));

/** Bloomberg price-field overrides exposed by the backend Input.  Omitting
 *  falls through to the YAML's ``default_price_field`` (PX_LAST). */
export const STRIP_PRICE_FIELD_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: 'PX_LAST', label: 'PX_LAST (YAML default)' },
  { value: 'PX_MID', label: 'PX_MID' },
  { value: 'PX_BID', label: 'PX_BID' },
  { value: 'PX_ASK', label: 'PX_ASK' },
];

/** Whites (1-4) / Reds (5-8) segment tag per the V1 universe. */
export function stripSegmentLabel(stripPosition: number): 'WHITES' | 'REDS' | 'GREENS' {
  if (stripPosition <= 4) return 'WHITES';
  if (stripPosition <= 8) return 'REDS';
  return 'GREENS';
}

/** Desk-canonical caveat for the compact view footer.  The FULL P5 /
 *  ADR 0013 disclosure is wire-carried (``methodology_disclosure``) and
 *  surfaces verbatim on the extended methodology card. */
export const STRIP_SNAPSHOT_COMPACT_CAVEAT =
  'Rolling-generic strip; implied rate = 100 − price on inverse-priced strips. Single aligned as_of (intersection of trading days).';

// ---------------------------------------------------------------------------
// Data hook — single source for BOTH views.
// ---------------------------------------------------------------------------

export interface UseStripSnapshotArgs {
  curveFamily: string;
  asOfDate?: string;
  lastPriceFieldName?: string;
  openInterestFieldName?: string;
}

export interface UseStripSnapshotResult {
  data: FuturesStripSnapshotOutput | null;
  isLoading: boolean;
  errorMessage: string | null;
}

/** Fetches the typed-detail endpoint
 *  (/api/v1/rates/detail/policy-futures-strip-snapshot); re-fetches when
 *  any input changes.  Errors are stringified and surfaced through the
 *  views' error slots. */
export function useStripSnapshot(
  args: UseStripSnapshotArgs,
): UseStripSnapshotResult {
  const [data, setData] = useState<FuturesStripSnapshotOutput | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const params: PolicyFuturesStripSnapshotDetailParams = {
    curve_family: args.curveFamily,
    as_of_date: args.asOfDate,
    last_price_field_name: args.lastPriceFieldName,
    open_interest_field_name: args.openInterestFieldName,
  };

  useEffect(() => {
    if (!args.curveFamily) {
      setData(null);
      setErrorMessage(null);
      setIsLoading(false);
      return;
    }
    let cancelled = false;
    setIsLoading(true);
    setErrorMessage(null);
    fetchDetailPolicyFuturesStripSnapshot(params)
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
  }, [
    args.curveFamily,
    args.asOfDate,
    args.lastPriceFieldName,
    args.openInterestFieldName,
  ]);

  return { data, isLoading, errorMessage };
}

// ---------------------------------------------------------------------------
// Row accessors + display-only arithmetic (FP9 — labelled at every
// surface that renders these).
// ---------------------------------------------------------------------------

/** Front (first) row — the ``snapshot`` list ordering matches
 *  ``strip_positions`` exactly per the backend wire contract. */
export function frontRow(
  data: FuturesStripSnapshotOutput,
): FuturesStripSnapshotRow | null {
  return data.snapshot.length > 0 ? data.snapshot[0] : null;
}

/** Last configured row (deepest strip position in the snapshot). */
export function lastRow(
  data: FuturesStripSnapshotOutput,
): FuturesStripSnapshotRow | null {
  return data.snapshot.length > 0
    ? data.snapshot[data.snapshot.length - 1]
    : null;
}

/** DISPLAY-ONLY front→last slope in bps: (last.implied_rate_pct −
 *  front.implied_rate_pct) × 100.  This is a frontend subtraction over
 *  two wire values, NOT a backend quantity — every KPI cell that shows
 *  it carries the "display-only" label (FP9).  Positive = strip slopes
 *  UP front→last (market prices higher rates further out). */
export function stripSlopeBps(data: FuturesStripSnapshotOutput): number | null {
  const front = frontRow(data);
  const last = lastRow(data);
  if (!front || !last || front === last) return null;
  return (last.implied_rate_pct - front.implied_rate_pct) * 100;
}

/** Convert the wire's PERCENT-POINTS 1-day change into bps for display
 *  (unit conversion ×100 — the wire field doc says "NOT bps; ×100 for
 *  bps").  Null-safe. */
export function dailyChangeBps(row: FuturesStripSnapshotRow | null): number | null {
  if (!row || row.daily_change_implied_rate_pct == null) return null;
  if (!Number.isFinite(row.daily_change_implied_rate_pct)) return null;
  return row.daily_change_implied_rate_pct * 100;
}

/** Slope phrasing for captions ("steep / flat / inverted" desk read). */
export function slopeReadLabel(slopeBps: number | null): string {
  if (slopeBps == null) return '—';
  if (slopeBps > 10) return 'Upward-sloping';
  if (slopeBps < -10) return 'Inverted';
  return 'Flat';
}

/** Whole-contract open-interest formatting (counts, never notional). */
export function formatContracts(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return '—';
  const abs = Math.abs(v);
  if (abs >= 1_000_000) return `${(v / 1_000_000).toFixed(2)}M`;
  if (abs >= 10_000) return `${(v / 1_000).toFixed(1)}k`;
  return Math.round(v).toLocaleString('en-US');
}

// ---------------------------------------------------------------------------
// KPI descriptor builders
// ---------------------------------------------------------------------------

/** The compact view's THREE canonical headline KPIs per
 *  rendering_density.md §2.2:
 *
 *    1. FRONT RATE        — front-slot implied rate in PERCENT (primary)
 *    2. FRONT 1D Δ (BPS)  — front-slot implied-rate change, toneForChange
 *                           (positive bps = tightening = coral)
 *    3. SLOPE F→L (BPS)   — display-only front→last subtraction,
 *                           labelled as such in the caption (FP9)
 */
export function compactKPIs(
  data: FuturesStripSnapshotOutput,
): ReadonlyArray<KPIDescriptor> {
  const front = frontRow(data);
  const frontBps = dailyChangeBps(front);
  const slope = stripSlopeBps(data);
  return [
    {
      label: 'FRONT RATE',
      value: front ? signedFixed(front.implied_rate_pct, 2) : '—',
      unit: '%',
      tone: 'neutral',
      emphasis: 'primary',
      subtext: front ? `${front.contract_code} · price ${front.raw_price.toFixed(2)}` : undefined,
    },
    {
      label: 'FRONT 1D Δ (BPS)',
      value: signedFixed(frontBps, 1),
      unit: 'bp',
      // Positive bps = implied rate UP = tightening → coral; matches the
      // sibling policy-futures price-level tone semantics.
      tone: toneForChange(frontBps),
    },
    {
      label: 'SLOPE F→L (BPS)',
      value: signedFixed(slope, 1),
      unit: 'bp',
      tone: 'neutral',
      caption: 'display-only subtraction',
    },
  ];
}

export const COMPACT_KPI_PLACEHOLDERS: ReadonlyArray<KPIDescriptor> = [
  { label: 'FRONT RATE', value: '—' },
  { label: 'FRONT 1D Δ (BPS)', value: '—' },
  { label: 'SLOPE F→L (BPS)', value: '—' },
];

/** The extended view's KPI strip — the three compact headline reads plus
 *  the audit cells (positions echo + z-window population). */
export function extendedKPIs(
  data: FuturesStripSnapshotOutput,
): ReadonlyArray<KPIDescriptor> {
  const front = frontRow(data);
  const last = lastRow(data);
  const frontBps = dailyChangeBps(front);
  const slope = stripSlopeBps(data);
  return [
    {
      label: 'FRONT RATE',
      value: front ? signedFixed(front.implied_rate_pct, 2) : '—',
      unit: '%',
      tone: 'neutral',
      subtext: front ? `${front.contract_code} · price ${front.raw_price.toFixed(2)}` : undefined,
    },
    {
      label: 'FRONT 1D Δ (BPS)',
      value: signedFixed(frontBps, 1),
      unit: 'bp',
      tone: toneForChange(frontBps),
    },
    {
      label: 'STRIP SLOPE F→L (BPS)',
      value: signedFixed(slope, 1),
      unit: 'bp',
      tone: 'neutral',
      subtext:
        front && last ? `${front.contract_code} → ${last.contract_code}` : undefined,
      caption: `${slopeReadLabel(slope)} · display-only subtraction`,
    },
    {
      label: 'POSITIONS',
      value: `${data.strip_positions.length}`,
      tone: 'neutral',
      subtext: data.strip_positions.length > 0
        ? `${data.strip_positions[0]}–${data.strip_positions[data.strip_positions.length - 1]}`
        : undefined,
    },
    {
      label: 'OBSERVATIONS',
      value: `${data.observation_count}`,
      tone: 'neutral',
      subtext: 'aligned days in z window',
    },
  ];
}

export const EXTENDED_KPI_PLACEHOLDERS: ReadonlyArray<KPIDescriptor> = [
  { label: 'FRONT RATE', value: '—' },
  { label: 'FRONT 1D Δ (BPS)', value: '—' },
  { label: 'STRIP SLOPE F→L (BPS)', value: '—' },
  { label: 'POSITIONS', value: '—' },
  { label: 'OBSERVATIONS', value: '—' },
];

// ---------------------------------------------------------------------------
// Methodology rows + reference chips (P5 threading — the output-level
// disclosure surfaces VERBATIM from the wire, never a hardcoded literal).
// ---------------------------------------------------------------------------

export function buildMethodologyRows(
  data: FuturesStripSnapshotOutput,
  effectivePriceField: string,
  effectiveOiField: string,
): ReadonlyArray<MethodologyRow> {
  const meta = curveMetaFor(data.curve_family);
  const rule = data.inverse_priced
    ? 'Inverse-priced — implied_rate_pct = 100 − raw_price.'
    : 'Direct-priced — implied_rate_pct = raw_price.';
  return [
    {
      label: 'Anchor date',
      value: `${data.as_of_date} — intersection of trading days across ALL configured strip positions (no fresh-vs-stale row mixing)`,
    },
    {
      label: 'Strip universe',
      value: `${meta ? `${meta.longLabel} · ` : ''}positions ${data.strip_positions.join(', ')}`,
    },
    {
      label: 'Quote convention',
      value: `${data.quote_units} · ${rule}`,
    },
    {
      label: 'Short-rate regime',
      value:
        data.short_rate_regime === 'RFR'
          ? 'RFR — compounded daily risk-free rate (SOFR / SONIA)'
          : data.short_rate_regime === 'IBOR'
            ? 'IBOR — unsecured 3M term IBOR (Euribor)'
            : data.short_rate_regime,
    },
    {
      label: 'Fields',
      value: `${effectivePriceField} (price) · ${effectiveOiField} (open interest)`,
    },
    {
      label: 'Z-score model',
      value: '252d rolling window per leg on the IMPLIED-RATE series (YAML-locked)',
    },
    {
      label: 'Strip slope',
      value:
        'DISPLAY-ONLY front→last implied-rate subtraction performed by this surface (×100 to bps); not a backend quantity.',
    },
    {
      label: 'Disclosure',
      value: data.methodology_disclosure || '—',
    },
  ];
}

export function buildReferenceChips(): ReadonlyArray<ReferenceChip> {
  return [
    { label: 'ADR 0013 (policy_futures domain)' },
    { label: 'CME SOFR futures contract spec' },
    { label: 'ICE Euribor / SONIA futures contract spec' },
    { label: 'Catalog v2.1 PR14 (implied_rate_pct frozen)' },
    { label: 'Catalog PR4 (strip-aware primitive, not 8 outright reads)' },
  ];
}
