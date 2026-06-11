// ============================================================================
// wirpMeetingPricingShared.ts — Per-tool helpers shared between
// BuildExtended.tsx, BuildCompact.tsx, and the Monitor widget for
// ``calculate_wirp_meeting_pricing_tool``.
// ----------------------------------------------------------------------------
// FINANCE-AWARE per-tool layer.  MEETING-LIST shape — the wire carries a
// LIST of per-meeting WIRP snapshots for ONE central bank (FOMC / ECB /
// BOE / BOJ), NOT a time series.  Every numeric field is Bloomberg WIRP
// INGEST surfaced VERBATIM per ADR 0009 §1 (P12 boundary — NOT recomputed
// from STIR futures or OIS pricing).
//
// The single most important honesty rule in this layer (FP9 — no client-
// side compute): ``cumulative_move_prob_pct`` is Bloomberg's CUMULATIVE
// signed move probability — it CAN exceed ±100 when more than one 25bp
// move is priced (observed live-DB range −360.1 .. 548.0).  NEVER derive
// per-meeting hike / cut / hold probabilities from it client-side; the
// naive ``hike = max(p,0) / cut = max(−p,0) / hold = 100 − |p|`` identity
// is empirically wrong for a cumulative quantity (BOE 2025-05-08 ships
// −104.9 verbatim, which the identity would render as hold = −4.9%).
// This layer formats and tones the RAW value only.
//
// All three surfaces fetch the SAME typed-detail endpoint
// (/api/v1/rates/detail/wirp-meeting-pricing) per rendering_density.md
// §1.1 — the compact view just renders less of the same payload.
// ============================================================================

import { useEffect, useState } from 'react';
import {
  fetchDetailWirpMeetingPricing,
  type WirpMeetingPricingDetailParams,
} from '@/services/ratesApi';
import type {
  WirpMeetingPricingOutput,
  WirpMeetingSnapshot,
} from '@/types/rates';
import type {
  KPIDescriptor,
  MethodologyRow,
  ReferenceChip,
  ValueTone,
} from '@/components/shared/build';

// ---------------------------------------------------------------------------
// Central-bank registry.  Mirrors the YAML-locked ``supported_central_banks``
// set (FOMC / ECB / BOE / BOJ per ADR 0009 §2's region-prefix table).
// Finance-aware metadata (flag, policy-rate label) lives in this per-tool
// layer — the shared shells are finance-blind and the linker / sovereign
// registries don't know central banks.
// ---------------------------------------------------------------------------

export interface CentralBankMeta {
  /** Central-bank code as the wire carries it (e.g. 'FOMC'). */
  code: string;
  /** Country / region flag emoji. */
  flag: string;
  /** Long human-facing institution name. */
  longLabel: string;
  /** What "implied policy rate" resolves to for this CB — disclosure
   *  label for the identity subtitle (per ADR 0009 §2). */
  policyRateLabel: string;
}

const CENTRAL_BANK_REGISTRY: Record<string, CentralBankMeta> = {
  FOMC: {
    code: 'FOMC',
    flag: '🇺🇸',
    longLabel: 'Federal Reserve (FOMC)',
    policyRateLabel: 'Fed funds effective target',
  },
  ECB: {
    code: 'ECB',
    flag: '🇪🇺',
    longLabel: 'European Central Bank',
    policyRateLabel: 'ECB deposit facility rate',
  },
  BOE: {
    code: 'BOE',
    flag: '🇬🇧',
    longLabel: 'Bank of England',
    policyRateLabel: 'BoE Bank Rate',
  },
  BOJ: {
    code: 'BOJ',
    flag: '🇯🇵',
    longLabel: 'Bank of Japan',
    policyRateLabel: 'BoJ policy rate',
  },
};

export function centralBankMetaFor(code: string): CentralBankMeta | null {
  return CENTRAL_BANK_REGISTRY[code] ?? null;
}

/** Central-bank options for the controls strip + Monitor widget. */
export const WIRP_CENTRAL_BANK_OPTIONS: ReadonlyArray<{ value: string; label: string }> =
  Object.values(CENTRAL_BANK_REGISTRY).map((m) => ({
    value: m.code,
    label: `${m.code} · ${m.longLabel}`,
  }));

/** The two selection modes the backend's cohesive PR8 central surface
 *  exposes.  ``next_n_meetings`` (default) vs ``specific_meeting_date``. */
export const WIRP_SELECTION_MODE_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: 'next_n_meetings', label: 'Next N meetings' },
  { value: 'specific_meeting_date', label: 'Specific meeting date' },
];

/** n_meetings options (backend bound [1, 24]; YAML default 6). */
export const WIRP_N_MEETINGS_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: '2', label: 'Next 2' },
  { value: '4', label: 'Next 4' },
  { value: '6', label: 'Next 6 (default)' },
  { value: '8', label: 'Next 8' },
  { value: '12', label: 'Next 12' },
];

/** Desk-canonical caveat string for the compact view footer + Monitor
 *  provenance.  SHORT form of the wire's ``methodology_note`` (which is
 *  the full P5 disclosure and is surfaced verbatim on the extended
 *  methodology card). */
export const WIRP_COMPACT_CAVEAT =
  'Bloomberg WIRP verbatim (not recomputed). Move prob is CUMULATIVE — can exceed ±100; no hike/cut/hold split.';

// ---------------------------------------------------------------------------
// Data hook
// ---------------------------------------------------------------------------

export interface UseWirpMeetingPricingArgs {
  centralBank: string;
  /** Closed mode pair — mirrors the backend Literal so the typed
   *  fetch helper's param union holds without a cast. */
  selectionMode: 'next_n_meetings' | 'specific_meeting_date';
  /** Only sent in 'next_n_meetings' mode; backend forbids it in
   *  'specific_meeting_date' mode (Pydantic model_validator). */
  nMeetings?: number;
  /** Only sent in 'specific_meeting_date' mode (YYYY-MM-DD); backend
   *  forbids it in 'next_n_meetings' mode. */
  meetingDate?: string;
}

export interface UseWirpMeetingPricingResult {
  data: WirpMeetingPricingOutput | null;
  isLoading: boolean;
  errorMessage: string | null;
}

/** Single-source hook used by both Build views.  Fetches the typed-detail
 *  endpoint; re-fetches when any input changes.  Mode-dependent params are
 *  stripped HERE so the backend's mode → required-field invariant never
 *  fires from a stale URL param combination. */
export function useWirpMeetingPricing(
  args: UseWirpMeetingPricingArgs,
): UseWirpMeetingPricingResult {
  const [data, setData] = useState<WirpMeetingPricingOutput | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const isSpecificMode = args.selectionMode === 'specific_meeting_date';
  const params: WirpMeetingPricingDetailParams = {
    central_bank: args.centralBank,
    selection_mode: args.selectionMode,
    // Mode-dependent stripping — the backend's @model_validator rejects
    // n_meetings in specific mode and meeting_date in next-N mode.
    n_meetings: isSpecificMode ? undefined : args.nMeetings,
    meeting_date: isSpecificMode ? args.meetingDate : undefined,
  };

  useEffect(() => {
    if (!args.centralBank) {
      setData(null);
      setErrorMessage(null);
      setIsLoading(false);
      return;
    }
    if (isSpecificMode && !args.meetingDate) {
      // Specific mode without a date yet — wait for the user to supply
      // one rather than round-tripping a guaranteed 422.
      setData(null);
      setErrorMessage(null);
      setIsLoading(false);
      return;
    }
    let cancelled = false;
    setIsLoading(true);
    setErrorMessage(null);
    fetchDetailWirpMeetingPricing(params)
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
  }, [args.centralBank, args.selectionMode, args.nMeetings, args.meetingDate]);

  return { data, isLoading, errorMessage };
}

// ---------------------------------------------------------------------------
// Format helpers — local to this tool.  WIRP fields are Bloomberg
// verbatim; formatting only (FP9 — no derived statistics).
// ---------------------------------------------------------------------------

export function formatRatePct(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '—';
  return `${v.toFixed(2)}%`;
}

/** Cumulative move probability — SIGNED percent, printed verbatim.  The
 *  value can exceed ±100 (more than one 25bp move priced); we do NOT
 *  clamp, normalise, or decompose it. */
export function formatCumMoveProb(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '—';
  const sign = v >= 0 ? '+' : '';
  return `${sign}${v.toFixed(1)}%`;
}

/** Signed count of 25bp moves priced (fractional values are normal). */
export function formatNumMoves(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '—';
  const sign = v >= 0 ? '+' : '';
  return `${sign}${v.toFixed(2)}`;
}

/** WIRP_RATE_CHANGE — implied change vs the current effective rate, in
 *  PERCENTAGE POINTS (native Bloomberg unit per wirp.yml Stage-B). */
export function formatRateChangeNative(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '—';
  const sign = v >= 0 ? '+' : '';
  return `${sign}${v.toFixed(3)}pp`;
}

/** Short meeting-date label, e.g. '2026-06-17' → 'Jun 17 ’26'. */
export function formatMeetingDate(iso: string | null | undefined): string {
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

/** Tone for the signed cumulative move probability / num-moves / rate-
 *  change fields.  Desk convention mirrors the STIR tone semantics:
 *  hike-leaning (positive) = tightening = coral (negative tone); cut-
 *  leaning (negative) = easing = mint (positive tone).  This is a SIGN
 *  read on the verbatim Bloomberg value — no probability decomposition. */
export function moveDirectionTone(v: number | null | undefined): ValueTone {
  if (v == null || Number.isNaN(v) || v === 0) return 'neutral';
  return v > 0 ? 'negative' : 'positive';
}

/** One-word lean caption for the signed cumulative move probability —
 *  direction only (the sign is Bloomberg's), never a probability bucket. */
export function moveDirectionCaption(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v) || v === 0) return '—';
  return v > 0 ? 'Hike-leaning' : 'Cut-leaning';
}

// ---------------------------------------------------------------------------
// KPI descriptor builders
// ---------------------------------------------------------------------------

/** The compact view's THREE canonical headline KPIs per
 *  rendering_density.md §2.2:
 *    1. NEXT MEETING   (date, primary emphasis — "when is the next
 *                       decision?")
 *    2. IMPLIED RATE   (post-meeting implied policy rate, %)
 *    3. CUM MOVE PROB  (signed cumulative %, direction-toned, with the
 *                       hike/cut lean caption)
 *  THESIS Q3 documents why these vs alternatives (num_25bp_moves,
 *  rate_change_native — both surfaced in the extended table instead). */
export function compactKPIs(
  data: WirpMeetingPricingOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  return [
    {
      label: 'NEXT MEETING',
      value: formatMeetingDate(cm.next_meeting_date),
      tone: 'neutral',
      emphasis: 'primary',
      caption: cm.next_as_of_date ? `as of ${cm.next_as_of_date}` : undefined,
    },
    {
      label: 'IMPLIED RATE',
      value:
        cm.next_implied_policy_rate_pct != null
          ? cm.next_implied_policy_rate_pct.toFixed(2)
          : '—',
      unit: '%',
      tone: 'neutral',
    },
    {
      label: 'CUM MOVE PROB',
      value: formatCumMoveProb(cm.next_cumulative_move_prob_pct),
      tone: moveDirectionTone(cm.next_cumulative_move_prob_pct),
      caption: moveDirectionCaption(cm.next_cumulative_move_prob_pct),
    },
  ];
}

/** The extended view's KPI strip — next-up meeting headline read plus
 *  the remaining verbatim WIRP fields and the returned-count audit. */
export function extendedKPIs(
  data: WirpMeetingPricingOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  return [
    {
      label: 'NEXT MEETING',
      value: formatMeetingDate(cm.next_meeting_date),
      tone: 'neutral',
    },
    {
      label: 'IMPLIED RATE',
      value:
        cm.next_implied_policy_rate_pct != null
          ? cm.next_implied_policy_rate_pct.toFixed(2)
          : '—',
      unit: '%',
      tone: 'neutral',
    },
    {
      label: 'CUM MOVE PROB',
      value: formatCumMoveProb(cm.next_cumulative_move_prob_pct),
      tone: moveDirectionTone(cm.next_cumulative_move_prob_pct),
      caption: moveDirectionCaption(cm.next_cumulative_move_prob_pct),
    },
    {
      label: '25BP MOVES PRICED',
      value: formatNumMoves(cm.next_num_25bp_moves_priced),
      tone: moveDirectionTone(cm.next_num_25bp_moves_priced),
    },
    {
      label: 'Δ VS EFFECTIVE',
      value: formatRateChangeNative(cm.next_rate_change_native),
      tone: moveDirectionTone(cm.next_rate_change_native),
      caption: 'percentage points',
    },
    {
      label: 'MEETINGS RETURNED',
      value: `${cm.n_meetings_returned}`,
      tone: 'neutral',
      caption:
        cm.n_meetings_requested != null
          ? `of ${cm.n_meetings_requested} requested`
          : undefined,
    },
  ];
}

// ---------------------------------------------------------------------------
// Meeting-strip helper — scales the per-meeting implied rates into a
// 0..1 band for the extended view's column strip.  Pure layout maths on
// the verbatim values (display scaling, not a statistic).
// ---------------------------------------------------------------------------

export interface MeetingStripCell {
  meeting: WirpMeetingSnapshot;
  /** 0..1 height fraction within the strip's [min, max] rate band;
   *  null when the meeting carries no implied rate. */
  heightFrac: number | null;
}

export function buildMeetingStrip(
  meetings: ReadonlyArray<WirpMeetingSnapshot>,
): ReadonlyArray<MeetingStripCell> {
  const rates = meetings
    .map((m) => m.implied_policy_rate_pct)
    .filter((v): v is number => v != null && !Number.isNaN(v));
  if (rates.length === 0) {
    return meetings.map((meeting) => ({ meeting, heightFrac: null }));
  }
  const min = Math.min(...rates);
  const max = Math.max(...rates);
  const span = max - min;
  return meetings.map((meeting) => {
    const v = meeting.implied_policy_rate_pct;
    if (v == null || Number.isNaN(v)) return { meeting, heightFrac: null };
    // Flat-strip degenerate case: render mid-height columns.
    const frac = span === 0 ? 0.5 : (v - min) / span;
    // Keep a visible floor so the min-rate meeting still renders a bar.
    return { meeting, heightFrac: 0.12 + frac * 0.88 };
  });
}

// ---------------------------------------------------------------------------
// Methodology rows + reference chips.  The P5 disclosure is the wire's
// ``methodology_note`` — surfaced VERBATIM, never a TSX literal.  The
// structural rows around it are anchored to wire fields (selection mode,
// counts, provenance tickers), not re-stated conventions.
// ---------------------------------------------------------------------------

export function buildMethodologyRows(
  data: WirpMeetingPricingOutput,
): ReadonlyArray<MethodologyRow> {
  const cm = data.current_metrics;
  const first = data.meetings[0];
  return [
    {
      label: 'Selection',
      value:
        cm.selection_mode === 'specific_meeting_date'
          ? `specific_meeting_date · ${cm.requested_meeting_date ?? '—'}`
          : `next_n_meetings · ${cm.n_meetings_returned} returned${cm.n_meetings_requested != null ? ` of ${cm.n_meetings_requested} requested` : ''}`,
    },
    {
      label: 'Provenance',
      value: first
        ? `${first.vendor_ticker}${first.bloomberg_ticker_implied_rate ? ` · ${first.bloomberg_ticker_implied_rate}` : ''}`
        : '—',
    },
    {
      label: 'Disclosure',
      value: data.methodology_note,
    },
  ];
}

export function buildReferenceChips(): ReadonlyArray<ReferenceChip> {
  return [
    { label: 'ADR 0009 (WIRP ingest contract)' },
    { label: 'Bloomberg WIRP screen' },
    { label: 'rates_agent/playbooks/wirp.yml' },
  ];
}
