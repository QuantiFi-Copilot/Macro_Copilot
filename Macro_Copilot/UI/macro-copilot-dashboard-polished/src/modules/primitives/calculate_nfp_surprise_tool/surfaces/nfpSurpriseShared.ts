// ============================================================================
// nfpSurpriseShared.ts — Per-tool helpers shared between BuildExtended.tsx
// and BuildCompact.tsx for ``calculate_nfp_surprise_tool``.
// ----------------------------------------------------------------------------
// FINANCE-AWARE per-tool layer (mirror of cpiSurpriseShared.ts).  This
// module knows what an "NFP surprise" means — specifically that it is the
// per-release ``actual − consensus_median`` in THOUSANDS OF JOBS (the
// desk-quote convention: +50 means +50k jobs), that the primitive is
// single-country single-event (US / nfp BOTH YAML-locked — the ONLY
// LLM-facing input is the display window), that the series index is the
// RELEASE DATE (sparse — ~monthly), and that the rolling z-score window
// is counted in RELEASES (YAML-locked at 24), not calendar days.
//
// UNIT HONESTY (Codex P1 fix from the PR #188 review): the canonical
// ``time_series_surprise`` is emitted in RAW JOB COUNTS (units=COUNT;
// value = surprise_k_jobs × 1000) so the closed-enum COUNT label stays
// semantically honest.  The chart helpers below convert raw counts back
// to the desk-quote k-jobs convention (÷1000) for display — a
// presentation-unit conversion only, NEVER an analytic recompute.
//
// WIRE-HONESTY: the disclosure prose lives on the TOP-LEVEL Output
// (``data.methodology_note``) — the ADR 0008 §2 P12 surprise-identity
// disclosure + the PRIOR-MONTH-REVISIONS caveat + the TD #28b scope
// limit.  The extended methodology card's Disclosure row reads it
// VERBATIM — NEVER hardcoded prose (P5).
//
// Both BuildExtended.tsx and BuildCompact.tsx fetch the SAME data (per
// rendering_density.md §1.1 — both views consume the same typed-detail
// endpoint; the compact view just renders less).  The headline KPIs +
// formatting + tone logic live here, in ONE place.
// ============================================================================

import { useEffect, useState } from 'react';
import {
  fetchDetailNfpSurprise,
  type NfpSurpriseDetailParams,
} from '@/services/ratesApi';
import type { NfpSurpriseOutput } from '@/types/rates';
import {
  regimeForZScore,
  signedFixed,
  toneForChange,
  toneForZScore,
  type KPIDescriptor,
  type MethodologyRow,
  type ReferenceBand,
  type ReferenceChip,
  type StretchContext,
} from '@/components/shared/build';

// ---------------------------------------------------------------------------
// Event identity — wire-locked.  NFP is US-only (country='US',
// event_type='nfp', Bloomberg 'NFP TCH Index'); there is no instrument
// selector.  Kept as constants so the surfaces render the same identity
// chip the methodology card cites.
// ---------------------------------------------------------------------------

export const NFP_COUNTRY = 'US';
export const NFP_FLAG = '🇺🇸';
export const NFP_EVENT_LABEL = 'NFP';
export const NFP_LONG_LABEL = 'US nonfarm payrolls';

/** The desk-canonical one-line caveat for this tool — surfaced in the
 *  compact footer + the extended EVENT top-right card.  Defined ONCE
 *  here so the two surfaces cannot drift. */
export const NFP_SURPRISE_COMPACT_CAVEAT =
  'Surprise vs consensus median, k jobs; z over 24 releases; as-released priors (revisions excluded).';

// ---------------------------------------------------------------------------
// Chart-series selector — FRONTEND-ONLY view state (not a backend param).
// The extended view exposes a "Chart series" control that switches the
// main chart between the surprise series (k jobs) and the rolling
// z-score series (σ).  The data hook ignores the key; both series ride
// the same payload (rendering_density.md §2.2).
// ---------------------------------------------------------------------------

export type SurpriseChartSeries = 'surprise' | 'zscore';

export const CHART_SERIES_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: 'surprise', label: 'Surprise (k jobs)' },
  { value: 'zscore', label: 'Rolling z-score' },
];

// ---------------------------------------------------------------------------
// Data hook
// ---------------------------------------------------------------------------

export interface UseNfpSurpriseArgs {
  lookbackReleases?: number;
}

export interface UseNfpSurpriseResult {
  data: NfpSurpriseOutput | null;
  isLoading: boolean;
  errorMessage: string | null;
}

/** Single-source hook used by both views.  Fetches the typed-detail
 *  endpoint; re-fetches when the display window changes.  No instrument
 *  selector — US NFP is wire-locked. */
export function useNfpSurpriseData(args: UseNfpSurpriseArgs): UseNfpSurpriseResult {
  const [data, setData] = useState<NfpSurpriseOutput | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const params: NfpSurpriseDetailParams = {
    lookback_releases: args.lookbackReleases,
  };

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    setErrorMessage(null);
    fetchDetailNfpSurprise(params)
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
  }, [args.lookbackReleases]);

  return { data, isLoading, errorMessage };
}

// ---------------------------------------------------------------------------
// KPI descriptor builders
// ---------------------------------------------------------------------------

/** Label for the z-score KPI — window counted in RELEASES (echoed from
 *  the wire's ``release_z_window_releases``; YAML-locked at 24). */
export function zScoreLabel(windowReleases: number | undefined): string {
  return `Z-SCORE (${windowReleases ?? 24} REL)`;
}

/** The compact view's THREE shell-standard headline KPIs per
 *  rendering_density.md §2.2:
 *    1. LATEST SURPRISE     (signed k jobs, toneForChange — hotter-than-
 *                            expected payrolls → coral, the fixed-income
 *                            "yields up" read; primary emphasis)
 *    2. Z-SCORE (24 REL)    (signed value + regime caption, toneForZScore)
 *    3. ACTUAL VS CONS      (actual k jobs + consensus subtext — the
 *                            print itself, so the surprise is auditable)
 *  THESIS Q3 documents why these vs alternatives. */
export function compactKPIs(
  data: NfpSurpriseOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  return [
    {
      label: 'LATEST SURPRISE',
      value: signedFixed(cm.current_surprise_k_jobs, 0),
      unit: 'k',
      tone: toneForChange(cm.current_surprise_k_jobs),
      emphasis: 'primary',
    },
    {
      label: zScoreLabel(cm.release_z_window_releases),
      value: signedFixed(cm.current_z_score, 2),
      tone: toneForZScore(cm.current_z_score),
      caption: regimeForZScore(cm.current_z_score),
    },
    {
      label: 'ACTUAL VS CONS',
      value: signedFixed(cm.current_actual_k_jobs, 0),
      unit: 'k',
      tone: 'neutral',
      subtext: `cons ${signedFixed(cm.current_consensus_median_k_jobs, 0)}k`,
    },
  ];
}

/** The extended view's FULL KPI strip — the latest print decomposed
 *  end-to-end (surprise / z / actual / consensus / prior / count) so
 *  the desk can audit ``actual − consensus_median`` without a second
 *  tool call.  Prior is AS-RELEASED (revisions excluded — see the
 *  methodology_note disclosure). */
export function extendedKPIs(
  data: NfpSurpriseOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  return [
    {
      label: 'LATEST SURPRISE',
      value: signedFixed(cm.current_surprise_k_jobs, 0),
      unit: 'k',
      tone: toneForChange(cm.current_surprise_k_jobs),
    },
    {
      label: zScoreLabel(cm.release_z_window_releases),
      value: signedFixed(cm.current_z_score, 2),
      tone: toneForZScore(cm.current_z_score),
      caption: regimeForZScore(cm.current_z_score),
    },
    {
      label: 'ACTUAL',
      value: signedFixed(cm.current_actual_k_jobs, 0),
      unit: 'k',
      tone: 'neutral',
    },
    {
      label: 'CONSENSUS (MEDIAN)',
      value: signedFixed(cm.current_consensus_median_k_jobs, 0),
      unit: 'k',
      tone: 'neutral',
    },
    {
      label: 'PRIOR (AS-RELEASED)',
      value: signedFixed(cm.current_prior_k_jobs, 0),
      unit: 'k',
      tone: 'neutral',
    },
    {
      label: 'RELEASES SHOWN',
      value: `${cm.observation_count}`,
      tone: 'neutral',
      caption: cm.period ?? undefined,
    },
  ];
}

// ---------------------------------------------------------------------------
// Series sanitisers — per-release series, SPARSE (~monthly release-date
// index).  The canonical surprise series arrives in RAW JOB COUNTS
// (units=COUNT; value = surprise_k_jobs × 1000); the chart helpers
// convert to the desk-quote k-jobs convention.  MainChart / MiniChart
// drop NaN points and connect the remainder — the accepted line
// rendering of a sparse series under the shared-shell contract.
// ---------------------------------------------------------------------------

// Sanity bound for NFP surprises (THOUSANDS of jobs).  Outside the
// COVID dislocation prints (|surprise| up to ~2,000k) the series lives
// within a few hundred k; anything beyond ±25,000k is almost certainly
// an event-calendar ingest artifact.  Defensive frontend safety net
// (mirror of the sibling sanitisers); the real fix lives in the data
// pipeline.
const NFP_SURPRISE_SANITY_MIN_K = -25_000;
const NFP_SURPRISE_SANITY_MAX_K = 25_000;

// Rolling z-scores beyond |8| on a 24-release window are numerically
// implausible — treat as artifact.
const Z_SCORE_SANITY_ABS = 8;

/** Convert the canonical RAW-JOB-COUNT surprise series to the desk-quote
 *  k-jobs convention (÷1000) and null out implausible values.
 *  Presentation-unit conversion only — the parity-tested identity is
 *  ``canonical = bespoke surprise_k_jobs × 1000``. */
export function sanitiseSurpriseSeriesKJobs(
  rows: ReadonlyArray<{ date: string; value: number | null }>,
): Array<{ date: string; value: number | null }> {
  return rows.map((r) => {
    if (r.value == null || Number.isNaN(r.value)) {
      return { date: r.date, value: null };
    }
    const kJobs = r.value / 1000;
    return {
      date: r.date,
      value:
        kJobs < NFP_SURPRISE_SANITY_MIN_K || kJobs > NFP_SURPRISE_SANITY_MAX_K
          ? null
          : kJobs,
    };
  });
}

export function sanitiseZScoreSeries(
  rows: ReadonlyArray<{ date: string; value: number | null }>,
): Array<{ date: string; value: number | null }> {
  return rows.map((r) => ({
    date: r.date,
    value:
      r.value == null
        || Number.isNaN(r.value)
        || Math.abs(r.value) > Z_SCORE_SANITY_ABS
        ? null
        : r.value,
  }));
}

// ---------------------------------------------------------------------------
// Reference-band builders
// ---------------------------------------------------------------------------

/** ±2σ / ±1.5σ envelope on the DISPLAYED surprise series (k jobs).
 *  Window stats over the displayed slice — same construction as the
 *  sibling modules' band builders. */
export function buildReferenceBands(
  data: NfpSurpriseOutput,
): ReadonlyArray<ReferenceBand> {
  const rawRows = data.time_series_surprise?.rows ?? [];
  const sanitised = sanitiseSurpriseSeriesKJobs(rawRows);
  const values = sanitised
    .map((r) => r.value)
    .filter((v): v is number => v != null && !Number.isNaN(v));
  if (values.length < 10) return [];

  const mean = values.reduce((a, b) => a + b, 0) / values.length;
  const variance =
    values.reduce((acc, v) => acc + (v - mean) ** 2, 0) / (values.length - 1);
  const std = Math.sqrt(variance);
  if (!Number.isFinite(std) || std === 0) return [];

  return [
    { value: mean + 2 * std, label: '+2σ', tone: 'extreme', style: 'dashed' },
    { value: mean + 1.5 * std, label: '+1.5σ', tone: 'elevated', style: 'dashed' },
    { value: mean - 1.5 * std, label: '-1.5σ', tone: 'elevated', style: 'dashed' },
    { value: mean - 2 * std, label: '-2σ', tone: 'positive', style: 'dashed' },
  ];
}

/** Fixed regime thresholds for the Z-SCORE chart series — the y-units
 *  ARE σ, so the bands sit at the closed-family thresholds (±1.5 amber,
 *  ±2.0 coral/mint) rather than window stats. */
export function buildZScoreReferenceBands(): ReadonlyArray<ReferenceBand> {
  return [
    { value: 2, label: '+2σ', tone: 'extreme', style: 'dashed' },
    { value: 1.5, label: '+1.5σ', tone: 'elevated', style: 'dashed' },
    { value: -1.5, label: '-1.5σ', tone: 'elevated', style: 'dashed' },
    { value: -2, label: '-2σ', tone: 'positive', style: 'dashed' },
  ];
}

// ---------------------------------------------------------------------------
// Stretch-context builder
// ---------------------------------------------------------------------------

export function buildStretchContext(
  data: NfpSurpriseOutput,
): StretchContext | undefined {
  const cm = data.current_metrics;
  if (cm.current_z_score == null) return undefined;

  const regime = regimeForZScore(cm.current_z_score);
  return {
    zScoreRegime: {
      value: cm.current_z_score,
      regime,
      bands: { amber: 1.5, coral: 2.0 },
    },
    interpretation: interpretationFor(regime, cm.current_z_score),
  };
}

function interpretationFor(
  regime: 'Normal' | 'Elevated' | 'Extreme',
  z: number | null | undefined,
): string {
  if (z == null) return 'Insufficient realised releases to characterise stretch.';
  const direction =
    z > 0 ? 'hot (payrolls above consensus)' : 'soft (payrolls below consensus)';

  if (regime === 'Extreme') {
    return (
      `The latest print is an extreme surprise vs the trailing release `
      + `window — ${direction}.  NFP surprises this stretched are the `
      + `canonical front-end repricing trigger (2s/5s/10s).  Remember the `
      + `surprise is vs the AS-RELEASED consensus; prior-month revisions `
      + `are NOT in this series.`
    );
  }
  if (regime === 'Elevated') {
    return (
      `The latest print is an elevated surprise vs the trailing release `
      + `window — ${direction}.  Worth a look at the release timeline for `
      + `directional persistence.`
    );
  }
  return (
    `The latest print is within the normal surprise range of the trailing `
    + `release window; consensus tracked the release well.`
  );
}

// ---------------------------------------------------------------------------
// Methodology rows + references
// ---------------------------------------------------------------------------

/** Methodology rows for the Extended view.  The Disclosure row reads
 *  ``data.methodology_note`` VERBATIM — the ADR 0008 §2 P12 surprise-
 *  identity disclosure + the PRIOR-MONTH-REVISIONS caveat + TD #28b
 *  scope limit threaded from the wire, NEVER hardcoded here (P5). */
export function buildMethodologyRows(
  data: NfpSurpriseOutput,
  effectiveLookbackReleases: number,
): ReadonlyArray<MethodologyRow> {
  const cm = data.current_metrics;
  return [
    {
      label: 'Surprise identity',
      value: 'surprise_k_jobs = actual − consensus_median (thousands of jobs; wire-frozen V1)',
    },
    {
      label: 'Event',
      value: `${NFP_LONG_LABEL} (country=${cm.country}, event_type=${cm.event_type} — BOTH wire-locked; no instrument selector)`,
    },
    {
      label: 'Z-score model',
      value:
        `Rolling window of ${cm.release_z_window_releases} RELEASES (not calendar days); `
        + 'YAML-locked — independent of the display window.',
    },
    {
      label: 'Display window',
      value:
        `${effectiveLookbackReleases} releases requested · ${cm.observation_count} realised `
        + 'releases shown (display window only — not a methodology choice).',
    },
    {
      label: 'Series index',
      value:
        'Release date (YYYY-MM-DD), NOT trade date — sparse ~monthly cadence; '
        + 'each row carries the reference period.',
    },
    {
      label: 'Units',
      value:
        'Desk-quote convention: thousands of jobs (+50 = +50k).  The canonical '
        + 'wire series is raw job counts (×1000) so units=COUNT stays honest; '
        + 'the chart converts back to k jobs for display.',
    },
    {
      label: 'Latest release',
      value: `${cm.release_date}${cm.period ? ` · period ${cm.period}` : ''}`,
    },
    {
      label: 'Disclosure',
      value: data.methodology_note,
    },
  ];
}

export function buildReferenceChips(): ReadonlyArray<ReferenceChip> {
  return [
    { label: 'ADR 0004 event-calendar substrate' },
    { label: 'ADR 0008 economic-releases playbook' },
    { label: 'TD #28b release-date window' },
    { label: 'Bloomberg NFP TCH Index' },
  ];
}
