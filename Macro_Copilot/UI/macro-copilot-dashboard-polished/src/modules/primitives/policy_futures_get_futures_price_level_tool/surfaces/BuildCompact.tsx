// ============================================================================
// surfaces/BuildCompact.tsx — Compact Build view for
// policy_futures_get_futures_price_level_tool.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.2 + §5 every new primitive ships a compact
// view; this is the policy-futures strip-position price level's.  Mounted
// as a node body inside multi-tool query DAG visualizations (e.g.
// *"compare SFR1 vs SFR2 vs ER1 implied rates"*).
//
// Design reference: ./mockups/Compact.png.
//
// Per the Option-(c) precedent from breakeven_butterfly this file uses
// the SHELL-STANDARD density (3 KPIs + single identity line).  The mockup
// shows a denser layout (5D / 1M / percentile / 252d high / 252d low /
// observations as a secondary row); promoting that into the compact view
// would require extending BuildCompactShell with a ``secondaryKpis`` slot,
// touching 10+ shipped compact views.  See THESIS.md → Mockup conformance
// for the deferral note.  The full 9-cell KPI strip remains available in
// the extended view (one tap on the expand affordance).
// ============================================================================

import { Activity } from 'lucide-react';
import {
  BuildCompactShell,
  type BuildCompactProps,
} from '@/components/shared/build';
import {
  POLICY_FUTURES_PRICE_COMPACT_CAVEAT,
  buildReferenceBands,
  compactKPIs,
  curveMetaFor,
  sanitiseImpliedRateSeries,
  stripPackLabel,
  stripStemLabel,
  usePolicyFuturesPrice,
} from './policyFuturesPriceShared';

const BuildCompact: React.FC<BuildCompactProps> = ({
  params,
  size = 'small',
  onExpand,
  callMeta,
}) => {
  // Strip position arrives as a string on the URL/context bus; coerce
  // once at the boundary.  Default to '1' (front) so the fetch can
  // proceed even when the multi-tool DAG omits the param.
  const curveFamilyParam = params.curve_family ?? '';
  const stripPositionRaw = params.strip_position ?? '';
  const stripPositionNum =
    stripPositionRaw && Number(stripPositionRaw) ? Number(stripPositionRaw) : 0;

  const { data, isLoading, errorMessage } = usePolicyFuturesPrice({
    curveFamily: curveFamilyParam,
    stripPosition: stripPositionNum,
    lookbackDays:
      params.lookback_days != null && params.lookback_days !== ''
        ? Number(params.lookback_days)
        : undefined,
    asOfDate: params.as_of_date || undefined,
    fieldName: params.field_name || undefined,
  });

  const cm = data?.current_metrics;
  const curveFamily = cm?.curve_family ?? curveFamilyParam;
  const stripPosition = cm?.strip_position ?? stripPositionNum;
  const meta = curveMetaFor(curveFamily);
  const stem = stripPosition > 0 ? stripStemLabel(curveFamily, stripPosition) : '—';
  const packLabel = stripPosition > 0 ? stripPackLabel(curveFamily, stripPosition) : '';

  return (
    <BuildCompactShell
      toolDisplayName="Policy Futures Price Level"
      statusPill="SNAPSHOT"
      headerIcon={<Activity size={12} strokeWidth={1.75} className="text-ice-300" />}
      identity={{
        primary: stem,
        secondary: packLabel,
        flag: meta?.flag,
      }}
      kpis={
        data
          ? compactKPIs(data)
          : [
              { label: 'IMPLIED RATE (PERCENT)', value: '—' },
              { label: '1D CHANGE (BPS)', value: '—' },
              { label: 'Z-SCORE (252D)', value: '—' },
            ]
      }
      chartPoints={sanitiseImpliedRateSeries(data?.time_series ?? []).map(
        (r) => ({ date: r.date, value: r.value ?? NaN }),
      )}
      chartUnit="%"
      referenceBands={data ? buildReferenceBands(data) : []}
      caveatText={POLICY_FUTURES_PRICE_COMPACT_CAVEAT}
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
