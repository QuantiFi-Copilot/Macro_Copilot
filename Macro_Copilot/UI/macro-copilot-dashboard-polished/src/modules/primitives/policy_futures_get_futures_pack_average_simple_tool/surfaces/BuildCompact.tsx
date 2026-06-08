// ============================================================================
// surfaces/BuildCompact.tsx — Compact Build view for
// policy_futures_get_futures_pack_average_simple_tool.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.2 + §5 every new primitive ships a compact
// view; this is the same-curve pack-average's.  Mounted as a node body
// inside multi-tool query DAG visualizations (e.g. *"compare SFR whites
// vs ER whites vs SFI whites front-year pack averages"*).
//
// Design reference: ./mockups/Compact.png.  Mockup-faithful identity row:
//   primary:   "SFR · WHITES · 4-quarter mean"   (market + pack + descriptor)
//   secondary: "Implied 4.38% · Price 95.62"      (subtitle currently
//                                                  ELIDED to the level
//                                                  KPI; the implied/price
//                                                  pair is read directly
//                                                  from the primary KPI
//                                                  cell)
//
// Per the Option-(c) precedent (Batch 1 fdac7d2 +
// policy_futures_get_futures_price_level_tool d8e8233 + tool 19 fcc5381 +
// tool 20 be2595b + tool 21 ad4a6fa) this file uses the SHELL-STANDARD
// density (3 KPIs + single identity line + sparkline + caveat + expand).
// The mockup's denser layout — the dual primary-line + secondary KPI
// row (5D / 252D PCTL / 252D HIGH / 252D LOW / OBSERVATIONS) — is
// preserved visually in spirit (the primary 3 KPIs carry the load-
// bearing reads); promoting the secondary row into the compact view
// would require extending BuildCompactShell with a ``secondaryKpis``
// slot, touching 10+ shipped compact views.  See THESIS.md → Mockup
// conformance for the deferral rationale.
// ============================================================================

import { BarChart3 } from 'lucide-react';
import {
  BuildCompactShell,
  type BuildCompactProps,
} from '@/components/shared/build';
import {
  buildReferenceBands,
  compactCaveatFor,
  compactKPIs,
  curveMetaFor,
  sanitisePackSeries,
  usePolicyFuturesPackAverage,
} from './futuresPackAverageSimpleShared';

const BuildCompact: React.FC<BuildCompactProps> = ({
  params,
  size = 'small',
  onExpand,
  callMeta,
}) => {
  const curveFamilyParam = params.curve_family ?? '';
  const packParam = params.pack ?? '';

  const { data, isLoading, errorMessage } = usePolicyFuturesPackAverage({
    curveFamily: curveFamilyParam,
    pack: packParam,
    lookbackDays:
      params.lookback_days != null && params.lookback_days !== ''
        ? Number(params.lookback_days)
        : undefined,
    asOfDate: params.as_of_date || undefined,
    fieldName: params.field_name || undefined,
  });

  const cm = data?.current_metrics;
  const curveFamily = cm?.curve_family ?? curveFamilyParam;
  const pack = cm?.pack ?? packParam;
  const meta = curveMetaFor(curveFamily);

  // Mockup-faithful identity row: "SFR · WHITES · 4-quarter mean".
  const packDisplay = pack ? pack.toUpperCase() : '—';
  const primary = meta
    ? `${meta.shortLabel} · ${packDisplay} · 4-quarter mean`
    : `${curveFamily || '—'} · ${packDisplay} · 4-quarter mean`;
  const secondary = 'Pack Average (Implied Rate)';

  return (
    <BuildCompactShell
      toolDisplayName="Policy Futures Pack Average"
      statusPill="SNAPSHOT"
      headerIcon={<BarChart3 size={12} strokeWidth={1.75} className="text-ice-300" />}
      identity={{
        primary,
        secondary,
        flag: meta?.flag,
      }}
      kpis={
        data
          ? compactKPIs(data)
          : [
              { label: 'PACK IMPLIED RATE', value: '—', unit: '%' },
              { label: '1D CHANGE', value: '—', unit: 'bp' },
              { label: 'Z-SCORE (252D)', value: '—' },
            ]
      }
      chartPoints={sanitisePackSeries(data?.time_series ?? []).map((r) => ({
        date: r.date,
        value: r.value ?? NaN,
      }))}
      chartUnit="%"
      referenceBands={data ? buildReferenceBands(data) : []}
      caveatText={compactCaveatFor(curveFamily)}
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
