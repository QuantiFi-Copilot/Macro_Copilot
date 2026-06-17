// ============================================================================
// surfaces/BuildExtended.tsx — Extended Build view for compute_financing_rate_tool.
// ----------------------------------------------------------------------------
// Per docs_revamped/03_standards/rendering_density.md §2.1 + §5 the
// extended view is the full canvas mounted when this tool is the sole
// focus of a single-tool query (Library "Open in Build", single-tool Ask
// handoff, direct ?context= deep-link) OR by the click-to-expand modal
// infrastructure when invoked from a compact card in a multi-tool DAG.
//
// Design reference: ./mockups/Extended.png (committed alongside this
// module).
//
// ARCHITECTURAL NOTE: consumes the SYNTHESIZED detail response built in
// /api/v1/rates/detail/financing-rate (route-side synthesis, Option (a)).
// Structurally identical to other snapshot tools above the typed-detail
// boundary.  See THESIS.md "Backend shape note".
// ============================================================================

import { useNavigate } from 'react-router-dom';
import { useRequestFocusedMode } from '@/components/build/lib/focusedMode';
import {
  BuildExtendedShell,
  asOfDateControl,
  percentileLabel,
  regimeForZScore,
  signedFixed,
  toneForZScore,
  toneTextClass,
  type BuildExtendedProps,
  type ControlDescriptor,
  type TopRightCard,
} from '@/components/shared/build';
import {
  buildMethodologyRows,
  buildReferenceBands,
  buildReferenceChips,
  buildStretchContext,
  extendedKPIs,
  PROXY_CURVE_REGISTRY,
  proxyCurveDescriptorFor,
  useFinancingRate,
} from './financingRateShared';

const PROXY_CURVE_OPTIONS = PROXY_CURVE_REGISTRY.map((p) => ({
  value: p.value,
  label: p.label,
}));

const LOOKBACK_OPTIONS = [
  { value: '126', label: '126d · ~6M' },
  { value: '252', label: '252d · 1Y' },
  { value: '504', label: '504d · 2Y' },
  { value: '1260', label: '1260d · 5Y' },
];

const METHOD_OPTIONS = [
  { value: 'overnight_index_proxy', label: 'overnight_index_proxy' },
];

const DEFAULTS = {
  method: 'overnight_index_proxy',
  lookback_days: '252',
};

const BuildExtended: React.FC<BuildExtendedProps> = ({
  toolName,
  params,
  onParamsChange,
}) => {
  const navigate = useNavigate();
  useRequestFocusedMode(true);

  const proxyCurve = params.proxy_curve ?? 'USD_SOFR_OIS';
  const method = params.method || DEFAULTS.method;
  const lookbackDays = params.lookback_days ?? DEFAULTS.lookback_days;

  const { data, isLoading, errorMessage } = useFinancingRate({
    proxyCurve,
    method,
    lookbackDays: Number(lookbackDays),
    asOfDate: params.as_of_date,
  });

  const pushParams = (nextParams: Record<string, string>) => {
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
    pushParams({ ...params, [name]: value });
  };

  const handleReset = () => {
    pushParams({
      proxy_curve: proxyCurve,
      ...DEFAULTS,
    });
  };

  const controls: ReadonlyArray<ControlDescriptor> = [
    {
      name: 'method',
      label: 'Method',
      kind: 'enum',
      value: method,
      options: METHOD_OPTIONS,
    },
    {
      name: 'proxy_curve',
      label: 'Proxy Curve',
      kind: 'enum',
      value: proxyCurve,
      options: PROXY_CURVE_OPTIONS,
    },
    {
      name: 'lookback_days',
      label: 'Lookback',
      kind: 'enum',
      value: lookbackDays,
      options: LOOKBACK_OPTIONS,
    },
    asOfDateControl(params.as_of_date),
  ];

  const cm = data?.current_metrics;
  const proxyDesc = proxyCurveDescriptorFor(proxyCurve);
  const zRegime = regimeForZScore(cm?.z_score);
  const pBucket = cm?.percentile_252d != null
    ? cm.percentile_252d >= 80 ? 'High' : cm.percentile_252d <= 20 ? 'Low' : 'Normal'
    : 'Normal';

  const topRightCards: ReadonlyArray<TopRightCard> = [
    {
      key: 'zscore',
      node: (
        <div className="card flex h-full flex-col gap-2 px-4 py-3">
          <span className="kicker text-fg-muted">Z-SCORE (252D)</span>
          <div
            className={`text-[26px] font-medium leading-none ${toneTextClass(toneForZScore(cm?.z_score))}`}
          >
            {signedFixed(cm?.z_score ?? null, 2)}
          </div>
          <span className={`text-[11.5px] ${toneTextClass(toneForZScore(cm?.z_score))}`}>
            {zRegime}
          </span>
        </div>
      ),
    },
    {
      key: 'percentile',
      node: (
        <div className="card flex h-full flex-col gap-2 px-4 py-3">
          <span className="kicker text-fg-muted">PERCENTILE (252D)</span>
          <div className="text-[26px] font-medium leading-none text-fg-primary">
            {percentileLabel(cm?.percentile_252d ?? null)}
          </div>
          <span className="text-[11.5px] text-fg-secondary">{pBucket}</span>
        </div>
      ),
    },
    {
      key: 'method',
      node: (
        <div className="card flex h-full flex-col gap-2 px-4 py-3">
          <span className="kicker text-fg-muted">METHOD</span>
          <div className="text-[15px] font-medium leading-tight text-fg-primary">
            {method}
          </div>
          <span className="text-[11.5px] text-fg-secondary">
            {proxyDesc?.shortLabel ?? proxyCurve}
          </span>
        </div>
      ),
    },
  ];

  const identityPrimary = proxyDesc?.bondFamily ?? proxyCurve;
  const identitySubtitle = proxyDesc
    ? `${proxyDesc.label} · Overnight-index financing proxy`
    : 'OIS-implied financing-rate proxy';

  return (
    <BuildExtendedShell
      category={{
        name: 'FINANCING RATE',
        tags: ['SNAPSHOT', 'DETERMINISTIC'],
      }}
      identity={{
        primary: identityPrimary,
        secondary: 'Financing',
        flag: proxyDesc?.flag,
        subtitle: identitySubtitle,
        asOfDate: cm?.as_of_date,
        meta: `${lookbackDays}d window · ${proxyCurve}`,
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
                as_of_date: '',
                proxy_curve: proxyCurve,
                method,
                financing_rate_pct: NaN,
                daily_change_bps: null,
                weekly_change_bps: null,
                monthly_change_bps: null,
                z_score: null,
                high_252d_pct: null,
                low_252d_pct: null,
                percentile_252d: null,
                observation_count: 0,
                n_observations: 0,
              },
              time_series: { series_name: '', units: 'percent', description: '', rows: [] },
              methodology_disclosure: '',
            })
      }
      chartPoints={(data?.time_series?.rows ?? []).map((r) => ({
        date: r.date,
        value: r.value ?? NaN,
      }))}
      chartUnit="%"
      chartValueDecimals={3}
      referenceBands={data ? buildReferenceBands(data) : []}
      stretchContext={data ? buildStretchContext(data) : undefined}
      methodology={data ? buildMethodologyRows(data) : []}
      methodologyReferences={buildReferenceChips()}
      lineage={{
        toolName,
        version: 'v1',
        kind: 'Deterministic snapshot (route-side synthesis)',
        providers: ['TimescaleDB', 'macro_data.v_market_data_daily_enriched'],
        asOf: cm?.as_of_date,
        freshness: 'fresh',
      }}
      isLoading={isLoading}
      errorMessage={errorMessage ?? undefined}
    />
  );
};

export default BuildExtended;
