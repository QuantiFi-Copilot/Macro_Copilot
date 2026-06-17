// ============================================================================
// surfaces/BuildExtended.tsx — Extended Build view for
// policy_futures_get_futures_price_level_tool.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.1 + §5 every new primitive ships an extended
// view; this is the policy-futures strip-position price level's full
// canvas.  Mounted by VirtualPrimitiveCanvas for single-tool queries OR
// by the click-to-expand modal from a compact card in a multi-tool DAG.
//
// Design reference: ./mockups/Extended.png.  Mockup-faithful design
// choices:
//   - Controls: Curve Family / Strip Position / Lookback / Field / As-of.
//     No z-score override controls — the backend schema doesn't expose
//     them; surfacing them would silently shadow the YAML (same rule
//     applied for the sibling breakeven_butterfly module).
//   - 9-cell KPI strip with the IMPLIED-RATE-axis primary level + price
//     + period change in bps + z-score + percentile + 252d high/low/mid
//     + observation count.
//   - Top-right cards: Z-score / Percentile / Strip-context (carries
//     the per-strip regime + master-stem label so the desk sees the
//     RFR vs IBOR disclosure inline without opening the methodology
//     drawer).
//   - The full P5 / ADR 0013 disclosure surfaces in the methodology
//     card via the backend's ``methodology_disclosure`` field (NOT a
//     hardcoded TS literal).
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
  POLICY_FUTURES_CURVE_OPTIONS,
  POLICY_FUTURES_PRICE_COMPACT_CAVEAT,
  STRIP_POSITION_OPTIONS,
  buildMethodologyRows,
  buildReferenceBands,
  buildReferenceChips,
  buildStretchContext,
  curveMetaFor,
  extendedKPIs,
  sanitiseImpliedRateSeries,
  stripPackLabel,
  stripPositionOrdinal,
  stripSegmentLabel,
  stripStemLabel,
  usePolicyFuturesPrice,
} from './policyFuturesPriceShared';

const LOOKBACK_OPTIONS = [
  { value: '90', label: '90d' },
  { value: '180', label: '180d' },
  { value: '365', label: '365d' },
  { value: '730', label: '2Y' },
  { value: '1825', label: '5Y' },
];

const FIELD_OPTIONS = [
  { value: 'PX_LAST', label: 'PX_LAST' },
  { value: 'PX_MID', label: 'PX_MID' },
  { value: 'PX_BID', label: 'PX_BID' },
  { value: 'PX_ASK', label: 'PX_ASK' },
];

const DEFAULTS = {
  lookback_days: '365',
  field_name: 'PX_LAST',
};

const BuildExtended: React.FC<BuildExtendedProps> = ({
  toolName,
  params,
  onParamsChange,
}) => {
  const navigate = useNavigate();
  useRequestFocusedMode(true);

  // ----- Resolve effective params -----
  const curveFamily = params.curve_family ?? 'SOFR_FUT';
  const stripPositionStr = params.strip_position ?? '1';
  const stripPosition = Number(stripPositionStr) || 1;
  const lookbackDays = params.lookback_days ?? DEFAULTS.lookback_days;
  const fieldName = params.field_name || DEFAULTS.field_name;
  const asOfDate = params.as_of_date || undefined;

  const { data, isLoading, errorMessage } = usePolicyFuturesPrice({
    curveFamily,
    stripPosition,
    lookbackDays: Number(lookbackDays),
    asOfDate,
    fieldName,
  });

  // ----- URL update on control change -----
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
    const nextParams = { ...params, [name]: value };
    pushParams(nextParams);
  };

  const handleReset = () => {
    pushParams({
      curve_family: curveFamily,
      strip_position: stripPositionStr,
      ...DEFAULTS,
    });
  };

  // ----- Controls -----
  const controls: ReadonlyArray<ControlDescriptor> = [
    {
      name: 'curve_family',
      label: 'Curve Family',
      kind: 'enum',
      value: curveFamily,
      options: POLICY_FUTURES_CURVE_OPTIONS,
    },
    {
      name: 'strip_position',
      label: 'Strip Position',
      kind: 'enum',
      value: stripPositionStr,
      options: STRIP_POSITION_OPTIONS,
    },
    {
      name: 'lookback_days',
      label: 'Lookback',
      kind: 'enum',
      value: lookbackDays,
      options: LOOKBACK_OPTIONS,
    },
    {
      name: 'field_name',
      label: 'Field',
      kind: 'enum',
      value: fieldName,
      options: FIELD_OPTIONS,
    },
    asOfDateControl(params.as_of_date),
  ];

  // ----- Top-right cards: Z-score / Percentile / Strip context -----
  const cm = data?.current_metrics;
  const meta = curveMetaFor(curveFamily);
  const z = cm?.z_score_implied_rate;
  const zRegime = regimeForZScore(z);
  const pBucket =
    cm?.percentile_252d != null
      ? cm.percentile_252d >= 80
        ? 'High'
        : cm.percentile_252d <= 20
          ? 'Low'
          : 'Normal'
      : 'Normal';

  const stem = stripStemLabel(curveFamily, stripPosition);
  const packLabel = stripPackLabel(curveFamily, stripPosition);
  const segment = stripSegmentLabel(stripPosition);
  const ordinal = stripPositionOrdinal(stripPosition);

  const topRightCards: ReadonlyArray<TopRightCard> = [
    {
      key: 'zscore',
      node: (
        <div className="card flex h-full flex-col gap-2 px-4 py-3">
          <span className="kicker text-fg-muted">Z-SCORE (252D)</span>
          <div
            className={`text-[26px] font-medium leading-none ${toneTextClass(toneForZScore(z))}`}
          >
            {signedFixed(z ?? null, 2)}
          </div>
          <span className={`text-[11.5px] ${toneTextClass(toneForZScore(z))}`}>
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
      key: 'strip-context',
      node: (
        <div className="card flex h-full flex-col gap-1.5 px-4 py-3">
          <span className="kicker text-fg-muted">STRIP CONTEXT</span>
          <div className="text-[15px] font-medium leading-tight text-fg-primary">
            {stem} · {segment}
          </div>
          <span className="text-[11px] leading-snug text-fg-muted">
            {cm?.short_rate_regime === 'IBOR'
              ? 'Unsecured 3M term IBOR (Euribor) — disclosure-only label.'
              : 'Compounded daily RFR (SOFR / SONIA) — disclosure-only label.'}
          </span>
        </div>
      ),
    },
  ];

  // ----- Identity row.  IdentityBlock inserts a `·` between primary and
  //       secondary spans; pass BARE strings — do NOT prepend '· '.
  const identityPrimary = stem;
  const identitySecondary = packLabel;
  const identitySubtitle = meta
    ? `${meta.longLabel} · ${ordinal}`
    : `Strip position ${stripPosition}`;

  return (
    <BuildExtendedShell
      category={{
        name: 'POLICY FUTURES PRICE LEVEL',
        tags: ['SNAPSHOT', 'DETERMINISTIC'],
      }}
      identity={{
        primary: identityPrimary,
        secondary: identitySecondary,
        flag: meta?.flag,
        subtitle: identitySubtitle,
        asOfDate: cm?.as_of_date,
        meta: `${lookbackDays}d window · ${fieldName}`,
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
                curve_family: curveFamily,
                strip_position: stripPosition,
                contract_code: stem,
                underlying_contract_code: null,
                security_name: null,
                expiry_date: null,
                contract_size: null,
                tick_size: null,
                tick_value: null,
                inverse_priced: true,
                short_rate_regime: meta?.regime ?? 'RFR',
                quote_units: '100 - rate',
                raw_price: NaN,
                implied_rate_pct: NaN,
                daily_change_raw_price: null,
                daily_change_implied_rate_pct: null,
                z_score_implied_rate: null,
                high_252d_implied_rate_pct: null,
                low_252d_implied_rate_pct: null,
                mid_252d_implied_rate_pct: null,
                high_252d_raw_price: null,
                low_252d_raw_price: null,
                mid_252d_raw_price: null,
                percentile_252d: null,
                observation_count: 0,
              },
              time_series: [],
              methodology_disclosure: '',
            })
      }
      chartPoints={sanitiseImpliedRateSeries(data?.time_series ?? []).map(
        (r) => ({ date: r.date, value: r.value ?? NaN }),
      )}
      chartUnit="%"
      chartValueDecimals={2}
      referenceBands={data ? buildReferenceBands(data) : []}
      stretchContext={data ? buildStretchContext(data) : undefined}
      methodology={data ? buildMethodologyRows(data, fieldName) : []}
      methodologyReferences={buildReferenceChips()}
      lineage={{
        toolName,
        version: 'v1',
        kind: 'Deterministic snapshot (rolling-generic strip read)',
        providers: [
          'TimescaleDB',
          'macro_data.v_market_data_daily_enriched',
          POLICY_FUTURES_PRICE_COMPACT_CAVEAT,
        ],
        asOf: cm?.as_of_date,
        freshness: 'fresh',
      }}
      isLoading={isLoading}
      errorMessage={errorMessage ?? undefined}
    />
  );
};

export default BuildExtended;
