// ============================================================================
// surfaces/BuildCompact.tsx — Compact Build view for
// calculate_cpi_surprise_tool.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.2 + §5 every new primitive ships a compact
// view; this is CPI surprise's.  Mounted as a node body inside multi-tool
// query DAG visualizations (e.g. "compare the US and EU CPI surprise
// histories" → two compact cards).
//
// All layout / tone / chart / methodology-disclosure chrome lives in the
// shared shell at @/components/shared/build — finance-blind.  This file
// is fetch + descriptor-mapping + shell composition only.  NO controls,
// NO own modal — parameter edits go through the expand affordance
// (rendering_density.md §3.3).
//
// The caveat footer is the shared one-liner from cpiSurpriseShared.ts —
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
  CPI_SURPRISE_COMPACT_CAVEAT,
  buildReferenceBands,
  compactKPIs,
  countryMetaFor,
  sanitiseSurpriseSeries,
  useCpiSurpriseData,
  zScoreLabel,
} from './cpiSurpriseShared';

const BuildCompact: React.FC<BuildCompactProps> = ({
  params,
  size = 'small',
  onExpand,
  callMeta,
}) => {
  const countryParam = (params.country ?? 'US').toUpperCase();

  const { data, isLoading, errorMessage } = useCpiSurpriseData({
    country: countryParam,
    lookbackReleases:
      params.lookback_releases != null && params.lookback_releases !== ''
        ? Number(params.lookback_releases)
        : undefined,
  });

  const cm = data?.current_metrics;
  const country = cm?.country ?? countryParam;
  const meta = countryMetaFor(country);

  return (
    <BuildCompactShell
      toolDisplayName="CPI Surprise"
      statusPill="EVENT SERIES"
      headerIcon={<CalendarClock size={12} strokeWidth={1.75} className="text-ice-300" />}
      identity={{
        primary: meta ? `${meta.country} · ${meta.eventLabel}` : country || '—',
        secondary: 'Surprise',
        flag: meta?.flag,
        subtitle: cm?.period ? `Latest period ${cm.period}` : undefined,
      }}
      kpis={
        data
          ? compactKPIs(data)
          : [
              { label: 'LATEST SURPRISE', value: '—', unit: 'pp' },
              { label: zScoreLabel(undefined), value: '—' },
              { label: 'ACTUAL VS CONS', value: '—', unit: 'pp' },
            ]
      }
      chartPoints={sanitiseSurpriseSeries(
        data?.time_series_surprise?.rows ?? [],
      ).map((r) => ({ date: r.date, value: r.value ?? NaN }))}
      chartUnit="pp"
      referenceBands={data ? buildReferenceBands(data) : []}
      caveatText={CPI_SURPRISE_COMPACT_CAVEAT}
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
