// ============================================================================
// surfaces/BuildCompact.tsx — Compact Build view for get_ois_rate_level_tool.
// ----------------------------------------------------------------------------
// Per docs_revamped/03_standards/rendering_density.md §2.2 + §5 every new
// primitive ships a compact view; this is OIS rate_level's.  Mounted as a
// node body inside multi-tool query DAG visualizations OR when a multi-
// primitive Ask handoff places this tool in a side-by-side grid.
//
// Design reference: ./mockups/Compact.png (committed alongside this module).
//
// Slot contract: this file is a thin fetch + descriptor-mapping + shell
// composition wrapper.  All layout, tone semantics, methodology
// disclosure, and chart rendering live in the shared shells at
// @/components/shared/build/ — finance-blind, reusable across tools.
// ============================================================================

import { Activity } from 'lucide-react';
import {
  BuildCompactShell,
  type BuildCompactProps,
} from '@/components/shared/build';
import {
  buildReferenceBands,
  compactCaveatText,
  compactKPIs,
  oisFamilyFor,
  sanitiseTimeSeries,
  useOisRateLevel,
} from './oisRateLevelShared';

const BuildCompact: React.FC<BuildCompactProps> = ({
  params,
  size = 'small',
  onExpand,
  callMeta,
}) => {
  const { data, isLoading, errorMessage } = useOisRateLevel({
    curveFamily: params.curve_family ?? '',
    tenor: params.tenor ?? '',
    lookbackDays:
      params.lookback_days != null ? Number(params.lookback_days) : undefined,
    fieldName: params.field_name || undefined,
  });

  // Pre-data identity (before fetch returns) — pulled from params so the
  // header renders something useful during the loading state.
  const fallbackCurve = params.curve_family ?? '—';
  const fallbackTenor = params.tenor ?? '';
  const cm = data?.current_metrics;
  const curveFamily = cm?.curve_family ?? fallbackCurve;
  const tenor = cm?.tenor ?? fallbackTenor;
  const meta = oisFamilyFor(curveFamily);

  // Identity mirrors the mockup: "SOFR · 2Y" with flag.  Primary line is
  // the overnight-index short name (SOFR / ESTR / SONIA / TONA / AONIA /
  // CORRA); secondary line is the tenor.
  const primary = meta?.indexShort ?? curveFamily;

  return (
    <BuildCompactShell
      toolDisplayName="OIS Rate Level"
      statusPill="SNAPSHOT"
      headerIcon={<Activity size={12} strokeWidth={1.75} className="text-ice-300" />}
      identity={{
        primary,
        secondary: tenor,
        flag: meta?.flag,
      }}
      kpis={
        data
          ? compactKPIs(data)
          : [
              { label: 'RATE', value: '—', unit: '%' },
              { label: '1D CHANGE', value: '—', unit: 'bp' },
              { label: 'Z-SCORE (252D)', value: '—' },
            ]
      }
      chartPoints={sanitiseTimeSeries(data?.time_series?.rows ?? []).map((r) => ({
        date: r.date,
        value: r.value ?? NaN,
      }))}
      chartUnit="%"
      referenceBands={data ? buildReferenceBands(data) : []}
      caveatText={compactCaveatText(curveFamily)}
      asOf={cm?.as_of_date}
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
