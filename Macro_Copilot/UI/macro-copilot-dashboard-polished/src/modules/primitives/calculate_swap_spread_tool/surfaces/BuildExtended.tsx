// ============================================================================
// surfaces/BuildExtended.tsx — Extended Build view for
// calculate_swap_spread_tool.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.1 + §5 every new primitive ships an extended
// view; this is the cross-domain swap-spread's full canvas.  Mounted by
// VirtualPrimitiveCanvas for single-tool queries OR by the click-to-expand
// modal from a compact card.  Design reference: ./mockups/Extended.png.
//
// Mockup-faithful design choices:
//   - Title "USD 10Y SWAP SPREAD" with per-leg subtitle "UST 10Y yield −
//     SOFR 10Y OIS rate".
//   - SINGLE "Sovereign Leg" dropdown that uniquely picks the canonical OIS
//     counterparty by currency (a swap spread is desk-meaningful only within
//     a single currency — the backend schema rejects cross-currency pairs).
//   - bps-scale KPI strip + per-leg PERCENT decomposition (SOVEREIGN YIELD /
//     OIS RATE) so spread = sovereign_yield − ois_rate is auditable on screen.
//   - Top-right cards: Z-score / Percentile / Sign-Convention card (with the
//     par-leg OIS approximation caveat surfaced inline — methodology MUST
//     be reachable from the headline).
//   - Methodology card carries the load-bearing par-leg OIS approximation
//     disclosure.  TODO(PR10): when the backend ships
//     ``current_metrics.methodology_label`` the "Disclosure" row switches to
//     consume the wire — see ./swapSpreadShared.ts for the marker.
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
  SWAP_SPREAD_COMPACT_CAVEAT,
  SWAP_SPREAD_PAIR_OPTIONS,
  SWAP_SPREAD_TENOR_OPTIONS_BY_PAIR,
  buildMethodologyRows,
  buildReferenceBands,
  buildReferenceChips,
  buildStretchContext,
  extendedKPIs,
  pairForSovereign,
  sanitiseSpreadSeries,
  useSwapSpread,
} from './swapSpreadShared';

const LOOKBACK_OPTIONS = [
  { value: '90', label: '90d' },
  { value: '180', label: '180d' },
  { value: '365', label: '365d' },
  { value: '730', label: '2Y' },
  { value: '1825', label: '5Y' },
];

const SOVEREIGN_FIELD_OPTIONS = [
  { value: 'YLD_YTM_MID', label: 'YLD_YTM_MID' },
  { value: 'YLD_YTM_BID', label: 'YLD_YTM_BID' },
  { value: 'YLD_YTM_ASK', label: 'YLD_YTM_ASK' },
];

const OIS_FIELD_OPTIONS = [
  { value: 'PX_LAST', label: 'PX_LAST' },
  { value: 'PX_MID', label: 'PX_MID' },
  { value: 'PX_BID', label: 'PX_BID' },
  { value: 'PX_ASK', label: 'PX_ASK' },
];

const DEFAULTS = {
  lookback_days: '365',
  sovereign_field_name: 'YLD_YTM_MID',
  ois_field_name: 'PX_LAST',
};

const BuildExtended: React.FC<BuildExtendedProps> = ({
  toolName,
  params,
  onParamsChange,
}) => {
  const navigate = useNavigate();
  useRequestFocusedMode(true);

  // ----- Resolve effective params -----
  const sovereignFamily = params.sovereign_curve_family ?? 'UST';
  const oisFamily = params.ois_curve_family ?? 'USD_SOFR_OIS';
  const tenor = params.tenor ?? '10Y';
  const lookbackDays = params.lookback_days ?? DEFAULTS.lookback_days;
  const sovereignField = params.sovereign_field_name || DEFAULTS.sovereign_field_name;
  const oisField = params.ois_field_name || DEFAULTS.ois_field_name;

  const { data, isLoading, errorMessage } = useSwapSpread({
    sovereignCurveFamily: sovereignFamily,
    oisCurveFamily: oisFamily,
    tenor,
    lookbackDays: Number(lookbackDays),
    sovereignFieldName: sovereignField,
    oisFieldName: oisField,
    asOfDate: params.as_of_date,
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
    const nextParams = { ...params };
    if (name === 'pair') {
      // The single Sovereign-Leg control expands to BOTH legs.  The sovereign
      // family uniquely determines the canonical OIS counterparty by currency.
      const meta = pairForSovereign(value);
      nextParams.sovereign_curve_family = value;
      if (meta) nextParams.ois_curve_family = meta.oisFamily;
      const tenors = SWAP_SPREAD_TENOR_OPTIONS_BY_PAIR[value] ?? [];
      if (
        tenors.length > 0
        && !tenors.some((t) => t.value === nextParams.tenor)
      ) {
        nextParams.tenor = tenors[0].value;
      }
    } else {
      nextParams[name] = value;
    }
    pushParams(nextParams);
  };

  const handleReset = () => {
    pushParams({
      sovereign_curve_family: sovereignFamily,
      ois_curve_family: oisFamily,
      tenor,
      ...DEFAULTS,
    });
  };

  // ----- Controls -----
  const tenorOptions =
    SWAP_SPREAD_TENOR_OPTIONS_BY_PAIR[sovereignFamily]
    ?? SWAP_SPREAD_TENOR_OPTIONS_BY_PAIR.UST;
  const controls: ReadonlyArray<ControlDescriptor> = [
    {
      name: 'pair',
      label: 'Sovereign Leg',
      kind: 'enum',
      value: sovereignFamily,
      options: SWAP_SPREAD_PAIR_OPTIONS,
    },
    {
      name: 'tenor',
      label: 'Tenor',
      kind: 'enum',
      value: tenor,
      options: tenorOptions,
    },
    {
      name: 'lookback_days',
      label: 'Lookback',
      kind: 'enum',
      value: lookbackDays,
      options: LOOKBACK_OPTIONS,
    },
    {
      name: 'sovereign_field_name',
      label: 'Sovereign Field',
      kind: 'enum',
      value: sovereignField,
      options: SOVEREIGN_FIELD_OPTIONS,
    },
    {
      name: 'ois_field_name',
      label: 'OIS Field',
      kind: 'enum',
      value: oisField,
      options: OIS_FIELD_OPTIONS,
    },
    asOfDateControl(params.as_of_date),
  ];

  // ----- Top-right cards: Z-score / Percentile / Pair -----
  const cm = data?.current_metrics;
  const zRegime = regimeForZScore(cm?.current_z_score);
  const pBucket =
    cm?.percentile_252d != null
      ? cm.percentile_252d >= 80
        ? 'High'
        : cm.percentile_252d <= 20
          ? 'Low'
          : 'Normal'
      : 'Normal';
  const pair = pairForSovereign(sovereignFamily);
  const topRightCards: ReadonlyArray<TopRightCard> = [
    {
      key: 'zscore',
      node: (
        <div className="card flex h-full flex-col gap-2 px-4 py-3">
          <span className="kicker text-fg-muted">Z-SCORE (252D)</span>
          <div
            className={`text-[26px] font-medium leading-none ${toneTextClass(toneForZScore(cm?.current_z_score))}`}
          >
            {signedFixed(cm?.current_z_score ?? null, 2)}
          </div>
          <span className={`text-[11.5px] ${toneTextClass(toneForZScore(cm?.current_z_score))}`}>
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
      key: 'pair',
      node: (
        <div className="card flex h-full flex-col gap-1.5 px-4 py-3">
          <span className="kicker text-fg-muted">SIGN CONVENTION</span>
          <div className="text-[15px] font-medium leading-tight text-fg-primary">
            {pair
              ? `${pair.country} · ${pair.sovereignShort} / ${pair.oisShort}`
              : `${sovereignFamily} / ${oisFamily}`}
          </div>
          <span className="text-[11px] leading-snug text-fg-muted">
            {SWAP_SPREAD_COMPACT_CAVEAT}
          </span>
        </div>
      ),
    },
  ];

  const identityPrimary = pair
    ? `${pair.currency} ${tenor} SWAP SPREAD`
    : `${sovereignFamily} ${tenor} SWAP SPREAD`;
  const identitySubtitle = pair
    ? `${pair.sovereignShort} ${tenor} yield − ${pair.oisShort} ${tenor} OIS rate`
    : `${sovereignFamily} ${tenor} yield − ${oisFamily} ${tenor} rate`;

  return (
    <BuildExtendedShell
      category={{
        name: 'SWAP SPREAD',
        tags: ['SNAPSHOT', 'DETERMINISTIC', 'CROSS-DOMAIN'],
      }}
      identity={{
        primary: identityPrimary,
        secondary: pair ? `· ${pair.sovereignShort}/${pair.oisShort}` : undefined,
        flag: pair?.flag,
        subtitle: identitySubtitle,
        asOfDate: cm?.as_of_date,
        meta: `${lookbackDays}d window · ${sovereignField} / ${oisField}`,
      }}
      topRightCards={topRightCards}
      controls={controls}
      onControlChange={handleControlChange}
      onResetControls={handleReset}
      kpis={
        data
          ? extendedKPIs(data)
          : [
              { label: 'SPREAD', value: '—', unit: 'bp', tone: 'neutral' as const },
              { label: '1D CHANGE', value: '—', unit: 'bp', tone: 'neutral' as const },
              { label: 'Z-SCORE (252D)', value: '—', tone: 'neutral' as const },
            ]
      }
      chartPoints={sanitiseSpreadSeries(
        data?.time_series_spread?.rows ?? [],
      ).map((r) => ({ date: r.date, value: r.value ?? NaN }))}
      chartUnit="bp"
      chartValueDecimals={1}
      referenceBands={data ? buildReferenceBands(data) : []}
      stretchContext={data ? buildStretchContext(data) : undefined}
      methodology={
        data
          ? buildMethodologyRows(data, sovereignField, oisField, Number(lookbackDays))
          : []
      }
      methodologyReferences={buildReferenceChips()}
      lineage={{
        toolName,
        version: 'v1',
        kind: 'Deterministic snapshot (sovereign − OIS at matched tenor, par-leg ASW approximation, in bps)',
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
