// ============================================================================
// surfaces/BuildCompact.tsx — Compact Build view for
// calculate_nfp_surprise_tool.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.2 + §5 every new primitive ships a compact
// view; this is NFP surprise's.  Mounted as a node body inside multi-tool
// query DAG visualizations (e.g. "NFP surprise vs the 2s10s move" → two
// compact cards).
//
// All layout / tone / chart / methodology-disclosure chrome lives in the
// shared shell at @/components/shared/build — finance-blind.  This file
// is fetch + descriptor-mapping + shell composition only.  NO controls,
// NO own modal — parameter edits go through the expand affordance
// (rendering_density.md §3.3).
//
// The caveat footer is the shared one-liner from nfpSurpriseShared.ts —
// defined ONCE so the compact footer and the extended EVENT card cannot
// drift.  The full wire disclosure (``data.methodology_note``) renders on
// the extended methodology card.
// ============================================================================

import { CalendarClock } from 'lucide-react';
import {
  BuildCompactShell,
  type BuildCompactProps,
} from '@/components/shared/build';
import {
  NFP_COUNTRY,
  NFP_EVENT_LABEL,
  NFP_FLAG,
  NFP_SURPRISE_COMPACT_CAVEAT,
  buildReferenceBands,
  compactKPIs,
  sanitiseSurpriseSeriesKJobs,
  useNfpSurpriseData,
  zScoreLabel,
} from './nfpSurpriseShared';

const BuildCompact: React.FC<BuildCompactProps> = ({
  params,
  size = 'small',
  onExpand,
  callMeta,
}) => {
  const { data, isLoading, errorMessage } = useNfpSurpriseData({
    lookbackReleases:
      params.lookback_releases != null && params.lookback_releases !== ''
        ? Number(params.lookback_releases)
        : undefined,
  });

  const cm = data?.current_metrics;

  return (
    <BuildCompactShell
      toolDisplayName="NFP Surprise"
      statusPill="EVENT SERIES"
      headerIcon={<CalendarClock size={12} strokeWidth={1.75} className="text-ice-300" />}
      identity={{
        primary: `${NFP_COUNTRY} · ${NFP_EVENT_LABEL}`,
        secondary: 'Surprise',
        flag: NFP_FLAG,
        subtitle: cm?.period ? `Latest period ${cm.period}` : undefined,
      }}
      kpis={
        data
          ? compactKPIs(data)
          : [
              { label: 'LATEST SURPRISE', value: '—', unit: 'k' },
              { label: zScoreLabel(undefined), value: '—' },
              { label: 'ACTUAL VS CONS', value: '—', unit: 'k' },
            ]
      }
      chartPoints={sanitiseSurpriseSeriesKJobs(
        data?.time_series_surprise?.rows ?? [],
      ).map((r) => ({ date: r.date, value: r.value ?? NaN }))}
      chartUnit="k"
      referenceBands={data ? buildReferenceBands(data) : []}
      caveatText={NFP_SURPRISE_COMPACT_CAVEAT}
      asOf={cm?.release_date}
      freshness="fresh"
      onExpand={onExpand}
      size={size}
      isLoading={isLoading}
      errorMessage={errorMessage ?? undefined}
      callMeta={callMeta}
    />
  );
};

export default BuildCompact;
