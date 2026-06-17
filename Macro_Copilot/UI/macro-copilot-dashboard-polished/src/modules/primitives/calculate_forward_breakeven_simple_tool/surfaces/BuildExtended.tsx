// ============================================================================
// surfaces/BuildExtended.tsx — Extended Build view for
// calculate_forward_breakeven_simple_tool.
// ----------------------------------------------------------------------------
// Per docs_revamped/03_standards/rendering_density.md §2.1 + §5 every new
// primitive ships an extended view; this is the forward breakeven full canvas.
// Mounted by VirtualPrimitiveCanvas when this tool is opened as the sole
// focus of a single-tool query OR by the click-to-expand modal infrastructure
// when invoked from a compact card in a multi-tool DAG.
//
// Design reference: ./mockups/Extended.png (committed alongside this module).
//
// Mirrors the sibling calculate_breakeven_inflation_simple_tool extended-view
// shape (same controls strip, same three top-right cards, same lineage
// footer, same methodology-card structure) — adapted for the forward
// primitive:
//   - tenor → forward_pair (the canonical two-pillar selector)
//   - per-pair filtering of forward_pair options against the same-country
//     breakeven tenor grid (BREAKEVEN_TENORS_BY_PAIR)
//   - NO z-score override controls (YAML-locked on this primitive; mirrors
//     the sibling breakeven-curve-spread / breakeven-butterfly)
//   - methodology_label sourced from the wire (cm.methodology_label,
//     surfaced via buildMethodologyRows) — NOT a TS literal
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
  FORWARD_BREAKEVEN_COMPACT_CAVEAT,
  FORWARD_BREAKEVEN_PAIR_OPTIONS,
  buildMethodologyRows,
  buildReferenceBands,
  buildReferenceChips,
  buildStretchContext,
  extendedKPIs,
  forwardPairFor,
  forwardPairOptionsForLinker,
  pairForLinker,
  sanitiseForwardBreakevenSeries,
  useForwardBreakeven,
} from './forwardBreakevenShared';

const LOOKBACK_OPTIONS = [
  { value: '90', label: '90d' },
  { value: '180', label: '180d' },
  { value: '365', label: '1Y' },
  { value: '730', label: '2Y' },
  { value: '1825', label: '5Y' },
];

const FIELD_OPTIONS = [
  { value: 'YLD_YTM_MID', label: 'YLD_YTM_MID' },
  { value: 'YLD_YTM_BID', label: 'YLD_YTM_BID' },
  { value: 'YLD_YTM_ASK', label: 'YLD_YTM_ASK' },
];

const DEFAULTS = {
  forward_pair: '5Y5Y',
  lookback_days: '365',
  field_name: 'YLD_YTM_MID',
};

const BuildExtended: React.FC<BuildExtendedProps> = ({
  toolName,
  params,
  onParamsChange,
}) => {
  const navigate = useNavigate();

  // Request focused mode: collapse the workspaces sidebar + copilot rail
  // so this extended canvas gets the full viewport width.
  useRequestFocusedMode(true);

  // ----- Resolve effective params (fold defaults for missing ones) -----
  const nominalFamily = params.nominal_curve_family ?? 'UST';
  const linkerFamily = params.linker_curve_family ?? 'USD_TIPS';
  const forwardPairLabel =
    (params.forward_pair as string | undefined) ?? DEFAULTS.forward_pair;
  const pair =
    forwardPairFor(forwardPairLabel) ?? forwardPairFor(DEFAULTS.forward_pair)!;
  const lookbackDays = params.lookback_days ?? DEFAULTS.lookback_days;
  const fieldName = params.field_name || DEFAULTS.field_name;

  const { data, isLoading, errorMessage } = useForwardBreakeven({
    nominalCurveFamily: nominalFamily,
    linkerCurveFamily: linkerFamily,
    startTenor: pair.startTenor,
    endTenor: pair.endTenor,
    lookbackDays: Number(lookbackDays),
    fieldName,
    asOfDate: params.as_of_date,
  });

  // ----- Param update on control change -----
  // Stage D — when mounted inside the multi-tool DAG expand-to-modal, edits
  // stay local (onParamsChange) instead of navigating the global URL.
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
    const nextParams: Record<string, string> = { ...params };
    if (name === 'pair') {
      // The single Country-Pair control expands to BOTH legs.  The linker
      // family uniquely determines the nominal counterparty.  Re-snap the
      // forward_pair to one supported by the new pair's tenor grid.
      const meta = pairForLinker(value);
      nextParams.linker_curve_family = value;
      if (meta) nextParams.nominal_curve_family = meta.nominalFamily;
      const supported = forwardPairOptionsForLinker(value);
      const stillValid = supported.find((o) => o.value === forwardPairLabel);
      const nextPair = stillValid
        ? forwardPairFor(stillValid.value)
        : forwardPairFor(supported[0]?.value ?? DEFAULTS.forward_pair);
      if (nextPair) {
        nextParams.forward_pair = nextPair.label;
        nextParams.start_tenor = nextPair.startTenor;
        nextParams.end_tenor = nextPair.endTenor;
      }
    } else if (name === 'forward_pair') {
      const nextPair = forwardPairFor(value);
      nextParams.forward_pair = value;
      if (nextPair) {
        nextParams.start_tenor = nextPair.startTenor;
        nextParams.end_tenor = nextPair.endTenor;
      }
    } else {
      nextParams[name] = value;
    }
    pushParams(nextParams);
  };

  const handleReset = () => {
    const resetPair = forwardPairFor(DEFAULTS.forward_pair)!;
    pushParams({
      nominal_curve_family: nominalFamily,
      linker_curve_family: linkerFamily,
      forward_pair: DEFAULTS.forward_pair,
      start_tenor: resetPair.startTenor,
      end_tenor: resetPair.endTenor,
      lookback_days: DEFAULTS.lookback_days,
      field_name: DEFAULTS.field_name,
    });
  };

  // ----- Controls list -----
  // Per-pair filter the forward_pair options against the same-country
  // breakeven tenor grid; falls back to the unfiltered list for an
  // unrecognised linker family.
  const forwardPairOptions = forwardPairOptionsForLinker(linkerFamily);
  const controls: ReadonlyArray<ControlDescriptor> = [
    {
      name: 'pair',
      label: 'Country Pair',
      kind: 'enum',
      value: linkerFamily,
      options: FORWARD_BREAKEVEN_PAIR_OPTIONS,
    },
    {
      name: 'forward_pair',
      label: 'Forward',
      kind: 'enum',
      value: forwardPairLabel,
      options: forwardPairOptions,
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

  // ----- Top-right cards: Z-score / Percentile / Country-Pair caveat -----
  const cm = data?.current_metrics;
  const pairMeta = pairForLinker(linkerFamily);
  const zRegime = regimeForZScore(cm?.current_z_score);
  const pBucket =
    cm?.percentile_252d != null
      ? cm.percentile_252d >= 80
        ? 'High'
        : cm.percentile_252d <= 20
          ? 'Low'
          : 'Normal'
      : 'Normal';
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
          <span className="kicker text-fg-muted">INFLATION COMPENSATION</span>
          <div className="text-[15px] font-medium leading-tight text-fg-primary">
            {pairMeta
              ? `${pairMeta.country} · ${pairMeta.nominalShort} / ${pairMeta.linkerShort}`
              : linkerFamily}
          </div>
          <span className="text-[11px] leading-snug text-fg-muted">
            {FORWARD_BREAKEVEN_COMPACT_CAVEAT}
          </span>
        </div>
      ),
    },
  ];

  // ----- Identity row -----
  const identityPrimary = pairMeta
    ? `${pairMeta.country} · ${forwardPairLabel} FORWARD BREAKEVEN`
    : `${linkerFamily} ${forwardPairLabel} FORWARD BREAKEVEN`;
  const identitySubtitle = `Forward-implied breakeven inflation (${pair.startTenor} start, ${pair.endTenor} end)`;

  return (
    <BuildExtendedShell
      category={{
        name: 'FORWARD BREAKEVEN INFLATION',
        tags: ['SNAPSHOT', 'DETERMINISTIC', 'COMPENSATION'],
      }}
      identity={{
        primary: identityPrimary,
        secondary: pairMeta ? `${pairMeta.nominalShort}/${pairMeta.linkerShort}` : forwardPairLabel,
        flag: pairMeta?.flag,
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
                nominal_curve_family: nominalFamily,
                linker_curve_family: linkerFamily,
                start_tenor: pair.startTenor,
                end_tenor: pair.endTenor,
                forward_window_label: '',
                forward_breakeven_pct: null,
                forward_breakeven_bps: null,
                daily_change_bps: null,
                weekly_change_bps: null,
                monthly_change_bps: null,
                current_z_score: null,
                rolling_window_days: 252,
                high_252d_bps: null,
                low_252d_bps: null,
                percentile_252d: null,
                start_breakeven_bps: null,
                end_breakeven_bps: null,
                start_years: 0,
                end_years: 0,
                methodology_label: '',
              },
              time_series: [],
              time_series_forward: {
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
      chartPoints={sanitiseForwardBreakevenSeries(data?.time_series ?? []).map((r) => ({
        date: r.date,
        value: r.value ?? NaN,
      }))}
      chartUnit="bp"
      chartValueDecimals={0}
      referenceBands={data ? buildReferenceBands(data) : []}
      stretchContext={data ? buildStretchContext(data) : undefined}
      methodology={data ? buildMethodologyRows(data, fieldName) : []}
      methodologyReferences={buildReferenceChips()}
      lineage={{
        toolName,
        version: 'v1',
        kind: 'Deterministic snapshot (year-weighted forward of two spot breakevens)',
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
