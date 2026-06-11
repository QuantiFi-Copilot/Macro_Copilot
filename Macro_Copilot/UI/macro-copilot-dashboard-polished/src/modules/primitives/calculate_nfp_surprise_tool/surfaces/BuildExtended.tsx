// ============================================================================
// surfaces/BuildExtended.tsx — Extended Build view for
// calculate_nfp_surprise_tool.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.1 + §5 every new primitive ships an extended
// view; this is NFP surprise's full canvas.  Mounted by
// VirtualPrimitiveCanvas for single-tool queries OR by the click-to-expand
// modal from a compact card.  Design source: the polished shared shells
// (mockups are captured from the rendered UI by the integrator).
//
// Event-release design choices:
//   - Single-country single-event primitive — country (US) and event_type
//     (nfp) are BOTH wire-locked; the ONLY backend control is
//     lookback_releases (display window — NOT a methodology knob; the
//     rolling z window is YAML-locked at 24 releases).
//   - The series is SPARSE (per-release, ~monthly release-date index, not
//     daily).  MainChart's NaN-dropping line rendering is the accepted
//     shared-shell reading of a sparse series — no bespoke chart chrome.
//   - "Chart series" control toggles the main chart between the surprise
//     series (k jobs, converted from the canonical raw-count wire units)
//     and the rolling z-score series (σ).  FRONTEND-ONLY view state — the
//     data hook ignores it; both series ride the same payload.
//   - Methodology card threads ``data.methodology_note`` (TOP-LEVEL on the
//     Output — ADR 0008 §2 P12 disclosure + the PRIOR-MONTH-REVISIONS
//     caveat + TD #28b scope limit) verbatim.
// ============================================================================

import { useNavigate } from 'react-router-dom';
import { useRequestFocusedMode } from '@/components/build/lib/focusedMode';
import {
  BuildExtendedShell,
  regimeForZScore,
  signedFixed,
  toneForZScore,
  toneTextClass,
  type BuildExtendedProps,
  type ControlDescriptor,
  type TopRightCard,
} from '@/components/shared/build';
import {
  CHART_SERIES_OPTIONS,
  NFP_COUNTRY,
  NFP_EVENT_LABEL,
  NFP_FLAG,
  NFP_LONG_LABEL,
  NFP_SURPRISE_COMPACT_CAVEAT,
  buildMethodologyRows,
  buildReferenceBands,
  buildReferenceChips,
  buildStretchContext,
  buildZScoreReferenceBands,
  extendedKPIs,
  sanitiseSurpriseSeriesKJobs,
  sanitiseZScoreSeries,
  useNfpSurpriseData,
  zScoreLabel,
  type SurpriseChartSeries,
} from './nfpSurpriseShared';

// Display-window options in NUMBER OF RELEASES (~monthly cadence), not
// calendar days — the wire knob is ``lookback_releases`` [4, 200].
const LOOKBACK_RELEASES_OPTIONS = [
  { value: '12', label: '12 rel (~1Y)' },
  { value: '24', label: '24 rel (~2Y)' },
  { value: '36', label: '36 rel (~3Y)' },
  { value: '60', label: '60 rel (~5Y)' },
  { value: '120', label: '120 rel (~10Y)' },
];

const DEFAULTS = {
  lookback_releases: '24',
  chart_series: 'surprise',
};

const BuildExtended: React.FC<BuildExtendedProps> = ({
  toolName,
  params,
  onParamsChange,
}) => {
  const navigate = useNavigate();
  useRequestFocusedMode(true);

  // ----- Resolve effective params -----
  const lookbackReleases = params.lookback_releases ?? DEFAULTS.lookback_releases;
  // ``chart_series`` is frontend-only view state — never sent to the API.
  const chartSeries: SurpriseChartSeries =
    params.chart_series === 'zscore' ? 'zscore' : 'surprise';

  const { data, isLoading, errorMessage } = useNfpSurpriseData({
    lookbackReleases: Number(lookbackReleases),
  });

  // ----- URL update on control change -----
  const pushParams = (nextParams: Record<string, string>) => {
    // Stage D — when mounted inside the multi-tool DAG expand-to-modal,
    // edits stay local (onParamsChange) instead of navigating the global
    // URL, which would replace the multi-tool context behind the modal.
    if (onParamsChange) {
      onParamsChange(nextParams);
      return;
    }
    const nextCtx = encodeURIComponent(
      JSON.stringify({
        tools: [{ tool: toolName, params: nextParams }],
        tool_count: 1,
      }),
    );
    navigate(`/workspace?context=${nextCtx}`, { replace: true });
  };

  const handleControlChange = (name: string, value: string) => {
    const nextParams = { ...params };
    nextParams[name] = value;
    pushParams(nextParams);
  };

  const handleReset = () => {
    pushParams({ ...DEFAULTS });
  };

  // ----- Controls (lookback_releases is the ONLY backend knob) -----
  const controls: ReadonlyArray<ControlDescriptor> = [
    {
      name: 'lookback_releases',
      label: 'Lookback',
      kind: 'enum',
      value: lookbackReleases,
      options: LOOKBACK_RELEASES_OPTIONS,
    },
    {
      name: 'chart_series',
      label: 'Chart series',
      kind: 'enum',
      value: chartSeries,
      options: CHART_SERIES_OPTIONS,
    },
  ];

  // ----- Top-right cards: Z-score / Latest print / Event caveat -----
  const cm = data?.current_metrics;
  const zRegime = regimeForZScore(cm?.current_z_score);
  const topRightCards: ReadonlyArray<TopRightCard> = [
    {
      key: 'zscore',
      node: (
        <div className="card flex h-full flex-col gap-2 px-4 py-3">
          <span className="kicker text-fg-muted">
            {zScoreLabel(cm?.release_z_window_releases)}
          </span>
          <div
            className={`text-[26px] font-medium leading-none ${toneTextClass(toneForZScore(cm?.current_z_score))}`}
          >
            {signedFixed(cm?.current_z_score ?? null, 2)}
          </div>
          <span className={`text-[11.5px] ${toneTextClass(toneForZScore(cm?.current_z_score))}`}>
            {zRegime}
          </span>
        </div>
      ),
    },
    {
      key: 'latest_print',
      node: (
        <div className="card flex h-full flex-col gap-2 px-4 py-3">
          <span className="kicker text-fg-muted">LATEST PRINT</span>
          <div className="text-[26px] font-medium leading-none text-fg-primary">
            {signedFixed(cm?.current_actual_k_jobs ?? null, 0)}
            <span className="text-[14px] opacity-80">k</span>
          </div>
          <span className="text-[11.5px] text-fg-secondary">
            cons {signedFixed(cm?.current_consensus_median_k_jobs ?? null, 0)}k
            {cm?.period ? ` · ${cm.period}` : ''}
          </span>
        </div>
      ),
    },
    {
      key: 'event',
      node: (
        <div className="card flex h-full flex-col gap-1.5 px-4 py-3">
          <span className="kicker text-fg-muted">EVENT</span>
          <div className="text-[15px] font-medium leading-tight text-fg-primary">
            {`${NFP_COUNTRY} · ${NFP_EVENT_LABEL} (wire-locked)`}
          </div>
          <span className="text-[11px] leading-snug text-fg-muted">
            {NFP_SURPRISE_COMPACT_CAVEAT}
          </span>
        </div>
      ),
    },
  ];

  // ----- Chart series selection (frontend-only toggle) -----
  const chartPoints = (
    chartSeries === 'zscore'
      ? sanitiseZScoreSeries(data?.time_series_zscore?.rows ?? [])
      : sanitiseSurpriseSeriesKJobs(data?.time_series_surprise?.rows ?? [])
  ).map((r) => ({ date: r.date, value: r.value ?? NaN }));
  const chartUnit = chartSeries === 'zscore' ? 'σ' : 'k';
  const referenceBands = data
    ? chartSeries === 'zscore'
      ? buildZScoreReferenceBands()
      : buildReferenceBands(data)
    : [];

  return (
    <BuildExtendedShell
      category={{
        name: 'NFP SURPRISE',
        tags: ['EVENT SERIES', 'DETERMINISTIC', 'MONTHLY CADENCE'],
      }}
      identity={{
        primary: `${NFP_COUNTRY} ${NFP_EVENT_LABEL}`,
        secondary: 'Surprise',
        flag: NFP_FLAG,
        subtitle: `${NFP_LONG_LABEL} release · per-release surprise = actual − consensus median (thousands of jobs)`,
        asOfDate: cm?.release_date,
        meta: `${lookbackReleases} releases · z over ${cm?.release_z_window_releases ?? 24} releases${cm?.period ? ` · period ${cm.period}` : ''}`,
      }}
      topRightCards={topRightCards}
      controls={controls}
      onControlChange={handleControlChange}
      onResetControls={handleReset}
      kpis={
        data
          ? extendedKPIs(data)
          : extendedKPIs({
              current_metrics: {
                release_date: '',
                country: NFP_COUNTRY,
                event_type: 'nfp',
                period: null,
                surprise_label: '',
                current_surprise_k_jobs: null,
                current_z_score: null,
                release_z_window_releases: 24,
                current_actual_k_jobs: null,
                current_consensus_median_k_jobs: null,
                current_prior_k_jobs: null,
                observation_count: 0,
              },
              time_series: [],
              time_series_surprise: {
                series_name: '',
                units: 'count',
                description: '',
                rows: [],
              },
              time_series_zscore: {
                series_name: '',
                units: 'z_score',
                description: '',
                rows: [],
              },
              methodology_note: '',
            })
      }
      chartPoints={chartPoints}
      chartUnit={chartUnit}
      chartValueDecimals={0}
      referenceBands={referenceBands}
      stretchContext={data ? buildStretchContext(data) : undefined}
      methodology={
        data
          ? buildMethodologyRows(data, Number(lookbackReleases))
          : []
      }
      methodologyReferences={buildReferenceChips()}
      lineage={{
        toolName,
        version: 'v1',
        kind: 'Deterministic event-release series (actual − consensus_median)',
        providers: ['TimescaleDB', 'macro_data.event_calendar'],
        asOf: cm?.release_date,
        freshness: 'fresh',
      }}
      isLoading={isLoading}
      errorMessage={errorMessage ?? undefined}
    />
  );
};

export default BuildExtended;
