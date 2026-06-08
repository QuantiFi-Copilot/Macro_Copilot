// ============================================================================
// surfaces/BuildExtended.tsx — Extended Build view for the bond_futures
// variant of ``get_futures_price_level_tool``.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.1 + §5 every new primitive ships an extended
// view; this is the bond-futures rolling-generic price level's full canvas.
// Mounted by VirtualPrimitiveCanvas for single-tool queries OR by the
// click-to-expand modal from a compact card in a multi-tool DAG.
//
// Design reference: ./mockups/Extended.png.  Mockup-faithful choices:
//   - Controls: Curve Family / Contract Code / Lookback / Field (no
//     z-score overrides — the backend schema doesn't expose them per
//     ADR 0013 V1; surfacing them would silently shadow the YAML).
//   - 9-cell KPI strip on the PRICE axis: PRICE, 1D / 5D / 1M CHANGE
//     in quote_units (raw subtractions; NOT bps), Z-SCORE, PERCENTILE,
//     252D HIGH / LOW, OBSERVATIONS.
//   - Top-right cards: Z-score / Percentile / Contract context (carries
//     the per-contract quote_units + notation disclosure inline so the
//     desk sees points-vs-percent-of-par + 32nds-vs-decimal at a glance).
//   - Full P5 / ADR 0013 disclosure surfaces in the methodology card via
//     the backend's ``methodology_disclosure`` field (NOT a hardcoded TS
//     literal — wire-honesty per PR10 / P5).
// ============================================================================

import { useNavigate } from 'react-router-dom';
import { useRequestFocusedMode } from '@/components/build/lib/focusedMode';
import {
  BuildExtendedShell,
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
  BOND_FUTURES_CONTRACT_OPTIONS,
  BOND_FUTURES_CURVE_OPTIONS,
  BOND_FUTURES_PRICE_COMPACT_CAVEAT,
  buildMethodologyRows,
  buildReferenceBands,
  buildReferenceChips,
  buildStretchContext,
  contractMetaFor,
  extendedKPIs,
  quoteUnitsLabel,
  sanitisePriceSeries,
  useBondFuturesPrice,
} from './bondFuturesPriceShared';

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
  const curveFamily = params.curve_family ?? 'UST_FUT';
  const contractCode = params.contract_code ?? 'TY1';
  const lookbackDays = params.lookback_days ?? DEFAULTS.lookback_days;
  const fieldName = params.field_name || DEFAULTS.field_name;

  const { data, isLoading, errorMessage } = useBondFuturesPrice({
    curveFamily,
    contractCode,
    lookbackDays: Number(lookbackDays),
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
      contract_code: contractCode,
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
      options: BOND_FUTURES_CURVE_OPTIONS,
    },
    {
      name: 'contract_code',
      label: 'Contract',
      kind: 'enum',
      value: contractCode,
      options: BOND_FUTURES_CONTRACT_OPTIONS,
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
  ];

  // ----- Top-right cards: Z-score / Percentile / Contract context -----
  const cm = data?.current_metrics;
  const meta = contractMetaFor(contractCode);
  const z = cm?.z_score;
  const zRegime = regimeForZScore(z);
  const pBucket =
    cm?.percentile_252d != null
      ? cm.percentile_252d >= 80
        ? 'High'
        : cm.percentile_252d <= 20
          ? 'Low'
          : 'Normal'
      : 'Normal';

  const wireUnits = quoteUnitsLabel(cm?.quote_units ?? null, meta);
  const contractTagline = meta?.longLabel ?? contractCode;

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
      key: 'contract-context',
      node: (
        <div className="card flex h-full flex-col gap-1.5 px-4 py-3">
          <span className="kicker text-fg-muted">CONTRACT CONTEXT</span>
          <div className="text-[15px] font-medium leading-tight text-fg-primary">
            {contractCode} · {wireUnits}
          </div>
          <span className="text-[11px] leading-snug text-fg-muted">
            {meta?.notation === 'thirty_seconds'
              ? '32nds notation (CBOT). V1 monitors-only — CTD analytics deferred.'
              : 'Decimal notation. V1 monitors-only — CTD analytics deferred.'}
          </span>
        </div>
      ),
    },
  ];

  // ----- Identity row.  IdentityBlock inserts a `·` between primary and
  //       secondary; pass BARE strings — do NOT prepend '· '.
  const identityPrimary = contractCode;
  const identitySecondary = contractTagline;
  const identitySubtitle = meta
    ? `${meta.shortLabel} ${meta.tenor} · ${meta.curveFamily}`
    : `${cm?.tenor ?? ''} · ${curveFamily}`;

  return (
    <BuildExtendedShell
      category={{
        name: 'BOND FUTURES PRICE LEVEL',
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
                contract_code: contractCode,
                tenor: meta?.tenor ?? '',
                quote_units: meta?.quoteUnits ?? 'points',
                contract_size: null,
                expiry_date: null,
                security_name: null,
                current_price: NaN,
                daily_change_price: null,
                weekly_change_price: null,
                monthly_change_price: null,
                z_score: null,
                high_252d_price: null,
                low_252d_price: null,
                percentile_252d: null,
                observation_count: 0,
              },
              time_series: [],
              methodology_disclosure: '',
            })
      }
      chartPoints={sanitisePriceSeries(data?.time_series ?? []).map((r) => ({
        date: r.date,
        value: r.value ?? NaN,
      }))}
      chartUnit={wireUnits}
      chartValueDecimals={meta?.notation === 'thirty_seconds' ? 4 : (meta?.decimals ?? 2)}
      referenceBands={data ? buildReferenceBands(data) : []}
      stretchContext={data ? buildStretchContext(data) : undefined}
      methodology={data ? buildMethodologyRows(data, fieldName) : []}
      methodologyReferences={buildReferenceChips()}
      lineage={{
        toolName,
        version: 'v1',
        kind: 'Deterministic snapshot (rolling-generic bond-futures price)',
        providers: [
          'TimescaleDB',
          'macro_data.v_market_data_daily_enriched',
          BOND_FUTURES_PRICE_COMPACT_CAVEAT,
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
