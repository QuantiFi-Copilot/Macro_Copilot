// ============================================================================
// cpiSurpriseShared.ts — Per-tool helpers shared between BuildExtended.tsx
// and BuildCompact.tsx for ``calculate_cpi_surprise_tool``.
// ----------------------------------------------------------------------------
// FINANCE-AWARE per-tool layer (mirror of otrOfrSpreadShared.ts).  This
// module knows what a "CPI surprise" means — specifically that it is the
// per-release ``actual − consensus_median`` in PERCENTAGE POINTS of
// YoY CPI (NOT an index level, NOT a MoM print), that the series index
// is the RELEASE DATE (sparse — ~monthly, not daily), and that the
// rolling z-score window is counted in RELEASES (YAML-locked at 24),
// not calendar days.  The shared shells do not know any of this.
//
// WIRE-HONESTY: the disclosure prose lives on the TOP-LEVEL Output
// (``data.methodology_note``) — the ADR 0008 §2 P12 surprise-identity
// disclosure + the TD #28b release-date-window scope limit.  The
// extended methodology card's Disclosure row reads it VERBATIM —
// NEVER hardcoded prose (P5).  This mirrors the otr_ofr_spread /
// financing-rate top-level-note pattern.
//
// Both BuildExtended.tsx and BuildCompact.tsx fetch the SAME data (per
// rendering_density.md §1.1 — both views consume the same typed-detail
// endpoint; the compact view just renders less).  The headline KPIs +
// formatting + tone logic live here, in ONE place.
// ============================================================================

import { useEffect, useState } from 'react';
import {
  fetchDetailCpiSurprise,
  type CpiSurpriseDetailParams,
} from '@/services/ratesApi';
import type { CpiSurpriseOutput } from '@/types/rates';
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
// Country registry.  The supported set is YAML-locked on the backend via
// the ``cpi_event_type_for_<country>`` conventions (US / UK / JP / EU);
// US/UK/JP resolve to event_type='cpi_yoy', EU to 'hicp_yoy'.  Keyed by
// the uppercase code the event-calendar stores.  Finance-aware → lives
// in this per-tool layer, NOT the shared registry.
// ---------------------------------------------------------------------------

export interface CpiCountryMeta {
  /** Country / region code as stored in macro_data.event_calendar. */
  country: string;
  /** Long desk label used in subtitles + the methodology card. */
  long: string;
  /** Desk label for the release being surprised on (EU = HICP). */
  eventLabel: string;
  /** Country / region flag emoji for the compact header + identity row. */
  flag: string;
}

const COUNTRY_REGISTRY: Record<string, CpiCountryMeta> = {
  US: { country: 'US', long: 'United States', eventLabel: 'CPI YoY', flag: '🇺🇸' },
  UK: { country: 'UK', long: 'United Kingdom', eventLabel: 'CPI YoY', flag: '🇬🇧' },
  JP: { country: 'JP', long: 'Japan', eventLabel: 'CPI YoY', flag: '🇯🇵' },
  EU: { country: 'EU', long: 'Eurozone', eventLabel: 'HICP YoY', flag: '🇪🇺' },
};

/** Resolve the country meta.  Returns null for an unknown code. */
export function countryMetaFor(country: string): CpiCountryMeta | null {
  return COUNTRY_REGISTRY[country.toUpperCase()] ?? null;
}

/** Ordered country options exposed on the controls strip.  Mirrors the
 *  YAML-locked supported set in
 *  ``rates_agent/inflation_swaps/tools/cpi_surprise/config.yaml``. */
export const COUNTRY_OPTIONS: ReadonlyArray<{ value: string; label: string }> =
  Object.values(COUNTRY_REGISTRY).map((m) => ({
    value: m.country,
    label: `${m.country} · ${m.eventLabel}`,
  }));

/** The desk-canonical one-line caveat for this tool — surfaced in the
 *  compact footer + the extended EVENT top-right card.  Defined ONCE
 *  here so the two surfaces cannot drift. */
export const CPI_SURPRISE_COMPACT_CAVEAT =
  'Surprise vs consensus median, pct-pts of YoY CPI; z over 24 releases.';

// ---------------------------------------------------------------------------
// Chart-series selector — FRONTEND-ONLY view state (not a backend param).
// The extended view exposes a "Chart series" control that switches the
// main chart between the surprise series (pp) and the rolling z-score
// series (σ).  The data hook ignores the key; both series ride the same
// payload (rendering_density.md §2.2 — the typed-detail endpoint returns
// the same payload to both views).
// ---------------------------------------------------------------------------

export type SurpriseChartSeries = 'surprise' | 'zscore';

export const CHART_SERIES_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: 'surprise', label: 'Surprise (pp)' },
  { value: 'zscore', label: 'Rolling z-score' },
];

// ---------------------------------------------------------------------------
// Data hook
// ---------------------------------------------------------------------------

export interface UseCpiSurpriseArgs {
  country: string;
  lookbackReleases?: number;
}

export interface UseCpiSurpriseResult {
  data: CpiSurpriseOutput | null;
  isLoading: boolean;
  errorMessage: string | null;
}

/** Single-source hook used by both views.  Fetches the typed-detail
 *  endpoint; re-fetches when any input changes. */
export function useCpiSurpriseData(args: UseCpiSurpriseArgs): UseCpiSurpriseResult {
  const [data, setData] = useState<CpiSurpriseOutput | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const params: CpiSurpriseDetailParams = {
    country: args.country,
    lookback_releases: args.lookbackReleases,
  };

  useEffect(() => {
    if (!args.country) {
      setData(null);
      setErrorMessage(null);
      setIsLoading(false);
      return;
    }
    let cancelled = false;
    setIsLoading(true);
    setErrorMessage(null);
    fetchDetailCpiSurprise(params)
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
  }, [args.country, args.lookbackReleases]);

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
 *    1. LATEST SURPRISE     (signed pp, toneForChange — hotter-than-
 *                            expected print → coral, the fixed-income
 *                            "yields up" read; primary emphasis)
 *    2. Z-SCORE (24 REL)    (signed value + regime caption, toneForZScore)
 *    3. ACTUAL VS CONS      (actual pp + consensus subtext — the print
 *                            itself, so the surprise is auditable)
 *  THESIS Q3 documents why these vs alternatives. */
export function compactKPIs(
  data: CpiSurpriseOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  return [
    {
      label: 'LATEST SURPRISE',
      value: signedFixed(cm.current_surprise_pct, 2),
      unit: 'pp',
      tone: toneForChange(cm.current_surprise_pct),
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
      value: signedFixed(cm.current_actual_pct, 1),
      unit: 'pp',
      tone: 'neutral',
      subtext: `cons ${signedFixed(cm.current_consensus_median_pct, 1)}`,
    },
  ];
}

/** The extended view's FULL KPI strip — the latest print decomposed
 *  end-to-end (surprise / z / actual / consensus / prior / count) so
 *  the desk can audit ``actual − consensus_median`` without a second
 *  tool call. */
export function extendedKPIs(
  data: CpiSurpriseOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  return [
    {
      label: 'LATEST SURPRISE',
      value: signedFixed(cm.current_surprise_pct, 2),
      unit: 'pp',
      tone: toneForChange(cm.current_surprise_pct),
    },
    {
      label: zScoreLabel(cm.release_z_window_releases),
      value: signedFixed(cm.current_z_score, 2),
      tone: toneForZScore(cm.current_z_score),
      caption: regimeForZScore(cm.current_z_score),
    },
    {
      label: 'ACTUAL',
      value: signedFixed(cm.current_actual_pct, 1),
      unit: 'pp',
      tone: 'neutral',
    },
    {
      label: 'CONSENSUS (MEDIAN)',
      value: signedFixed(cm.current_consensus_median_pct, 1),
      unit: 'pp',
      tone: 'neutral',
    },
    {
      label: 'PRIOR',
      value: signedFixed(cm.current_prior_pct, 1),
      unit: 'pp',
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
// index, not daily trade dates).  MainChart / MiniChart drop NaN points
// and connect the remainder, which is the accepted line rendering of a
// sparse series under the shared-shell contract.
// ---------------------------------------------------------------------------

// Sanity bound for CPI YoY surprises (percentage points).  Across the
// supported countries surprises live within a few tenths of a pp;
// stress prints reach ~±1pp.  Anything beyond ±5pp is almost certainly
// an event-calendar ingest artifact (e.g. a level mistakenly stored as
// a surprise).  Defensive frontend safety net (mirror of the sibling
// sanitisers); the real fix lives in the data pipeline.
const CPI_SURPRISE_SANITY_MIN_PP = -5;
const CPI_SURPRISE_SANITY_MAX_PP = 5;

// Rolling z-scores beyond |8| on a 24-release window are numerically
// implausible — treat as artifact.
const Z_SCORE_SANITY_ABS = 8;

export function sanitiseSurpriseSeries(
  rows: ReadonlyArray<{ date: string; value: number | null }>,
): Array<{ date: string; value: number | null }> {
  return rows.map((r) => ({
    date: r.date,
    value:
      r.value == null
        || Number.isNaN(r.value)
        || r.value < CPI_SURPRISE_SANITY_MIN_PP
        || r.value > CPI_SURPRISE_SANITY_MAX_PP
        ? null
        : r.value,
  }));
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

/** ±2σ / ±1.5σ envelope on the DISPLAYED surprise series (pp).  Window
 *  stats over the displayed slice — same construction as the sibling
 *  modules' band builders. */
export function buildReferenceBands(
  data: CpiSurpriseOutput,
): ReadonlyArray<ReferenceBand> {
  const rawRows = data.time_series_surprise?.rows ?? [];
  const sanitised = sanitiseSurpriseSeries(rawRows);
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
  data: CpiSurpriseOutput,
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
  const direction = z > 0 ? 'hot (actual above consensus)' : 'soft (actual below consensus)';

  if (regime === 'Extreme') {
    return (
      `The latest print is an extreme surprise vs the trailing release `
      + `window — ${direction}.  Surprises this stretched are the canonical `
      + `trigger for re-pricing the front end; check whether the surprise `
      + `direction has been persistent across recent releases (band drift) `
      + `or is a one-off.`
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
 *  identity disclosure + TD #28b scope limit threaded from the wire,
 *  NEVER hardcoded here (P5). */
export function buildMethodologyRows(
  data: CpiSurpriseOutput,
  effectiveLookbackReleases: number,
): ReadonlyArray<MethodologyRow> {
  const cm = data.current_metrics;
  const meta = countryMetaFor(cm.country);
  return [
    {
      label: 'Surprise identity',
      value: 'surprise_pct = actual − consensus_median (pct-pts of YoY CPI; wire-frozen V1)',
    },
    {
      label: 'Event',
      value: meta
        ? `${meta.long} · ${meta.eventLabel} (event_type=${cm.event_type})`
        : `${cm.country} · event_type=${cm.event_type}`,
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
    { label: 'Bloomberg consensus survey' },
  ];
}
