// ============================================================================
// surfaces/BuildCompact.tsx — Compact Build view for compute_financing_rate_tool.
// ----------------------------------------------------------------------------
// Per docs_revamped/03_standards/rendering_density.md §2.2 + §5 the
// compact view is the grid card mounted as a node body inside multi-tool
// query DAG visualizations OR when a multi-primitive Ask handoff places
// this tool in a side-by-side grid.
//
// Design reference: ./mockups/Compact.png.
//
// Mockup conformance: the mockup additionally shows an auxiliary KPI
// strip (5D / 1M / 252D PCTL / HIGH / LOW / OBSERVATIONS) underneath the
// 3 headline KPIs.  Per the BuildCompactShell §2.2 contract the shell
// enforces exactly 3 KPI cells; the extras land in the Extended view's
// 9-cell strip (Option (c) precedent from Batch 1 fdac7d2).  Documented
// in THESIS §"Mockup conformance".
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
  proxyCurveDescriptorFor,
  useFinancingRate,
} from './financingRateShared';

const BuildCompact: React.FC<BuildCompactProps> = ({
  params,
  size = 'small',
  onExpand,
  callMeta,
}) => {
  const proxyCurve = params.proxy_curve ?? 'USD_SOFR_OIS';
  const method = params.method;
  const lookbackDays =
    params.lookback_days != null ? Number(params.lookback_days) : undefined;

  const { data, isLoading, errorMessage } = useFinancingRate({
    proxyCurve,
    method,
    lookbackDays,
  });

  const proxyDesc = proxyCurveDescriptorFor(proxyCurve);
  const cm = data?.current_metrics;

  return (
    <BuildCompactShell
      toolDisplayName="Financing Rate"
      statusPill="SNAPSHOT"
      headerIcon={<Activity size={12} strokeWidth={1.75} className="text-ice-300" />}
      identity={{
        primary: proxyDesc?.bondFamily ?? proxyCurve,
        secondary: `Financing · ${proxyDesc?.shortLabel ?? 'OIS-PROXY'}`,
        flag: proxyDesc?.flag,
      }}
      kpis={
        data
          ? compactKPIs(data)
          : [
              { label: 'FINANCING RATE', value: '—' },
              { label: '1D CHANGE', value: '—' },
              { label: 'Z-SCORE (252D)', value: '—' },
            ]
      }
      chartPoints={(data?.time_series?.rows ?? []).map((r) => ({
        date: r.date,
        value: r.value ?? NaN,
      }))}
      chartUnit="%"
      referenceBands={data ? buildReferenceBands(data) : []}
      caveatText={compactCaveatText(data)}
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
