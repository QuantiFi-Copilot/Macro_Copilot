// ============================================================================
// surfaces/BuildCompact.tsx — Compact Build view for get_yield_levels_tool.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.2 + §5 every primitive ships a compact view;
// this is the sovereign yield-level grid card.  Mounted as a node body
// inside multi-tool query DAGs OR when a multi-primitive Ask handoff places
// this tool in a side-by-side grid.  Design reference: ./mockups/Compact.png.
//
// Slot contract: no controls strip, no own modal (the shared onExpand
// prop opens the extended view).  3 headline KPIs (YIELD / 1D CHANGE /
// Z-SCORE), sparkline with z-score envelope bands, methodology surfaced
// via the footer caveat text per rendering_density.md §2.2.
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
  sanitiseTimeSeries,
  sovereignFamilyFor,
  useYieldLevel,
} from './yieldLevelShared';

const BuildCompact: React.FC<BuildCompactProps> = ({
  params,
  size = 'small',
  onExpand,
  callMeta,
}) => {
  const { data, isLoading, errorMessage } = useYieldLevel({
    curveFamily: params.curve_family ?? '',
    tenor: params.tenor ?? '',
    lookbackDays:
      params.lookback_days != null ? Number(params.lookback_days) : undefined,
    fieldName: params.field_name || undefined,
  });

  const fallbackCurve = params.curve_family ?? '—';
  const fallbackTenor = params.tenor ?? '';
  const cm = data?.current_metrics;
  const curveFamily = cm?.curve_family ?? fallbackCurve;
  const tenor = cm?.tenor ?? fallbackTenor;
  const familyMeta = sovereignFamilyFor(curveFamily);

  return (
    <BuildCompactShell
      toolDisplayName="Yield Level"
      statusPill="SNAPSHOT"
      headerIcon={<Activity size={12} strokeWidth={1.75} className="text-ice-300" />}
      identity={{
        primary: familyMeta?.shortLabel ?? curveFamily,
        secondary: tenor,
        flag: familyMeta?.flag,
      }}
      kpis={
        data
          ? compactKPIs(data)
          : [
              { label: 'YIELD', value: '—', unit: '%' },
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
