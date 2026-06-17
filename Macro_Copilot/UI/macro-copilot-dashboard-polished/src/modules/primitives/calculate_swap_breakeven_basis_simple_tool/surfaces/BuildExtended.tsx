// ============================================================================
// surfaces/BuildExtended.tsx — Extended Build view for
// calculate_swap_breakeven_basis_simple_tool.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.1 + §5 every new primitive ships an extended
// view; this is swap-breakeven basis's full canvas.  Mounted by
// VirtualPrimitiveCanvas for single-tool queries OR by the click-to-expand
// modal from a compact card.  Design reference: ./mockups/Extended.png.
//
// Mockup-faithful design choices:
//   - Title "USD 10Y SWAP-BE BASIS" with per-leg subtitle naming the
//     ZCIS index + bond pair (ZCIS CPI-U vs UST/TIPS).
//   - A SINGLE "Country Pair" dropdown (not three) — V1 swap-breakeven
//     basis is a same-currency object, so the pair key uniquely picks the
//     ZCIS + nominal + linker triplet; the control expands to ALL THREE
//     curve_family params.
//   - bps-scale KPI strip + the two underlying yields for the decomposition
//     (ZCIS leg pct + bond-implied breakeven pct).
//   - Top-right cards: Z-score / Percentile / Liquidity-Premium-proxy
//     caveat naming the resolved index families (wire's
//     ``index_family_caveat`` when surfaced).
//   - Sign convention POSITIVE = ZCIS rich vs bond BE (catalog
//     ``zcis_minus_breakeven`` guardrail surfaces in the methodology card).
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
  SWAP_BREAKEVEN_BASIS_PAIR_OPTIONS,
  SWAP_BREAKEVEN_BASIS_TENOR_OPTIONS_BY_PAIR,
  buildMethodologyRows,
  buildReferenceBands,
  buildReferenceChips,
  buildStretchContext,
  extendedKPIs,
  fallbackBasisCaveat,
  pairForKey,
  pairForZcisFamily,
  sanitiseBasisSeries,
  useSwapBreakevenBasis,
} from './swapBreakevenBasisShared';

const LOOKBACK_OPTIONS = [
  { value: '90', label: '90d' },
  { value: '180', label: '180d' },
  { value: '365', label: '365d' },
  { value: '730', label: '2Y' },
  { value: '1825', label: '5Y' },
];

const FIELD_OPTIONS = [
  { value: 'PX_MID', label: 'PX_MID' },
  { value: 'PX_BID', label: 'PX_BID' },
  { value: 'PX_ASK', label: 'PX_ASK' },
];

const DEFAULTS = {
  lookback_days: '365',
  field_name: 'PX_MID',
};

/** Resolve which pair key matches the current zcis curve_family,
 *  defaulting to USD if the pair is unknown / missing. */
function resolvePairKey(zcisFamily: string | undefined): string {
  if (!zcisFamily) return 'USD';
  const meta = pairForZcisFamily(zcisFamily);
  return meta?.key ?? 'USD';
}

const BuildExtended: React.FC<BuildExtendedProps> = ({
  toolName,
  params,
  onParamsChange,
}) => {
  const navigate = useNavigate();
  useRequestFocusedMode(true);

  // ----- Resolve effective params -----
  const pairKey = resolvePairKey(params.zcis_curve_family);
  const pair = pairForKey(pairKey);
  const zcisFamily = params.zcis_curve_family ?? pair?.zcisFamily ?? 'USD_ZCIS';
  const nominalFamily = params.nominal_curve_family ?? pair?.nominalFamily ?? 'UST';
  const linkerFamily = params.linker_curve_family ?? pair?.linkerFamily ?? 'USD_TIPS';
  const tenor = params.tenor ?? '10Y';
  const lookbackDays = params.lookback_days ?? DEFAULTS.lookback_days;
  const fieldName = params.field_name || DEFAULTS.field_name;

  const { data, isLoading, errorMessage } = useSwapBreakevenBasis({
    zcisCurveFamily: zcisFamily,
    nominalCurveFamily: nominalFamily,
    linkerCurveFamily: linkerFamily,
    tenor,
    lookbackDays: Number(lookbackDays),
    fieldName,
    asOfDate: params.as_of_date,
  });

  // ----- URL update on control change -----
  const pushParams = (nextParams: Record<string, string>) => {
    // Stage D — when mounted inside the multi-tool DAG expand-to-modal,
    // edits stay local (onParamsChange) instead of navigating the global
    // URL, which would replace the multi-tool context behind the modal.
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
      // The single Country-Pair control expands to ALL THREE legs.
      const meta = pairForKey(value);
      if (meta) {
        nextParams.zcis_curve_family = meta.zcisFamily;
        nextParams.nominal_curve_family = meta.nominalFamily;
        nextParams.linker_curve_family = meta.linkerFamily;
        const tenors = SWAP_BREAKEVEN_BASIS_TENOR_OPTIONS_BY_PAIR[value] ?? [];
        // Preserve current tenor if it remains valid on the new pair;
        // otherwise snap to that pair's first tenor.
        if (
          tenors.length > 0
          && !tenors.find((t) => t.value === nextParams.tenor)
        ) {
          nextParams.tenor = tenors[0].value;
        }
      }
    } else {
      nextParams[name] = value;
    }
    pushParams(nextParams);
  };

  const handleReset = () => {
    pushParams({
      zcis_curve_family: zcisFamily,
      nominal_curve_family: nominalFamily,
      linker_curve_family: linkerFamily,
      tenor,
      ...DEFAULTS,
    });
  };

  // ----- Controls -----
  const tenorOptions =
    SWAP_BREAKEVEN_BASIS_TENOR_OPTIONS_BY_PAIR[pairKey]
    ?? SWAP_BREAKEVEN_BASIS_TENOR_OPTIONS_BY_PAIR.USD;
  const controls: ReadonlyArray<ControlDescriptor> = [
    {
      name: 'pair',
      label: 'Country Pair',
      kind: 'enum',
      value: pairKey,
      options: SWAP_BREAKEVEN_BASIS_PAIR_OPTIONS,
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
      name: 'field_name',
      label: 'Field',
      kind: 'enum',
      value: fieldName,
      options: FIELD_OPTIONS,
    },
    asOfDateControl(params.as_of_date),
  ];

  // ----- Top-right cards: Z-score / Percentile / Liquidity-Premium-Proxy -----
  const cm = data?.current_metrics;
  const zRegime = regimeForZScore(cm?.z_score_252d);
  const pBucket =
    cm?.percentile_252d != null
      ? cm.percentile_252d >= 80
        ? 'High'
        : cm.percentile_252d <= 20
          ? 'Low'
          : 'Normal'
      : 'Normal';
  // Per the load-bearing wire-honesty mandate the caveat MUST surface on
  // the extended view.  Prefer the wire's resolved caveat string; fall
  // back to the per-pair static line when the wire hasn't yet resolved.
  const caveatLine = cm?.index_family_caveat ?? fallbackBasisCaveat(pair);
  const topRightCards: ReadonlyArray<TopRightCard> = [
    {
      key: 'zscore',
      node: (
        <div className="card flex h-full flex-col gap-2 px-4 py-3">
          <span className="kicker text-fg-muted">Z-SCORE (252D)</span>
          <div
            className={`text-[26px] font-medium leading-none ${toneTextClass(toneForZScore(cm?.z_score_252d))}`}
          >
            {signedFixed(cm?.z_score_252d ?? null, 2)}
          </div>
          <span className={`text-[11.5px] ${toneTextClass(toneForZScore(cm?.z_score_252d))}`}>
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
      key: 'liquidity_premium',
      node: (
        <div className="card flex h-full flex-col gap-1.5 px-4 py-3">
          <span className="kicker text-fg-muted">LIQUIDITY-PREMIUM PROXY</span>
          <div className="text-[15px] font-medium leading-tight text-fg-primary">
            {pair
              ? `${pair.zcisIndexShort} ZCIS vs ${pair.nominalShort}/${pair.linkerShort} BE`
              : `${zcisFamily} vs ${nominalFamily}/${linkerFamily}`}
          </div>
          <span className="text-[11px] leading-snug text-fg-muted">
            {caveatLine}
          </span>
        </div>
      ),
    },
  ];

  const identityPrimary = pair
    ? `${pair.country} ${tenor} SWAP-BE BASIS`
    : `${zcisFamily} ${tenor} SWAP-BE BASIS`;
  const identitySubtitle = pair
    ? `${pair.zcisIndexShort} ZCIS ${tenor} minus ${pair.nominalShort}/${pair.linkerShort} ${tenor} breakeven`
    : 'ZCIS rate minus bond-implied breakeven at the same tenor';

  return (
    <BuildExtendedShell
      category={{
        name: 'INFLATION RV · SWAP-BE BASIS',
        tags: ['SNAPSHOT', 'DETERMINISTIC', 'SAME-CURRENCY'],
      }}
      identity={{
        primary: identityPrimary,
        secondary: undefined,
        flag: pair?.flag,
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
                zcis_curve_family: zcisFamily,
                nominal_curve_family: nominalFamily,
                linker_curve_family: linkerFamily,
                tenor,
                tenor_years: NaN,
                basis_label: '',
                basis_pct: NaN,
                basis_bps: NaN,
                zcis_pct: null,
                breakeven_pct: null,
                breakeven_bps: null,
                nominal_yield_pct: null,
                real_yield_pct: null,
                change_1d_bps: null,
                change_1w_bps: null,
                change_1m_bps: null,
                z_score_252d: null,
                high_252d_bps: null,
                low_252d_bps: null,
                percentile_252d: null,
                observation_count: 0,
                zcis_inflation_index_family: '',
                zcis_index_lag: '',
                zcis_interpolation: '',
                zcis_underlying_index: null,
                linker_inflation_index_family: null,
                linker_index_lag: null,
                index_families_match: false,
                index_family_caveat: null,
                methodology_label: '',
              },
              time_series: [],
              time_series_basis: {
                series_name: '',
                units: 'bps',
                description: '',
                rows: [],
              },
              time_series_zscore: {
                series_name: '',
                units: 'z_score',
                description: '',
                rows: [],
              },
            })
      }
      chartPoints={sanitiseBasisSeries(
        data?.time_series_basis?.rows ?? [],
      ).map((r) => ({ date: r.date, value: r.value ?? NaN }))}
      chartUnit="bp"
      chartValueDecimals={1}
      referenceBands={data ? buildReferenceBands(data) : []}
      stretchContext={data ? buildStretchContext(data) : undefined}
      methodology={
        data
          ? buildMethodologyRows(data, fieldName, Number(lookbackDays))
          : []
      }
      methodologyReferences={buildReferenceChips()}
      lineage={{
        toolName,
        version: 'v1',
        kind: 'Deterministic snapshot (ZCIS − bond-implied breakeven, inner-join)',
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
