// ============================================================================
// surfaces/BuildExtended.tsx — Extended Build view for
// policy_futures_get_futures_calendar_spread_tool.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.1 + §5 every new primitive ships an extended
// view; this is the same-curve STIR calendar spread's full canvas.
// Mounted by VirtualPrimitiveCanvas for single-tool queries OR by the
// click-to-expand modal from a compact card in a multi-tool DAG.
//
// Design reference: ./mockups/Extended.png.  Mockup-faithful design
// choices:
//   - A SINGLE "Curve Family" dropdown (single-curve primitive — distinct
//     from policy_futures_get_futures_cross_market_spread which crosses
//     two families).
//   - A SINGLE "Pair" dropdown — registered (short, long) presets per
//     curve_family.  The Pydantic schema's
//     ``_strip_positions_must_be_ordered`` validator rejects any
//     unordered or duplicate pair; surfacing two independent strip-
//     position dropdowns would invite invalid selections.
//   - bps-scale headline KPI strip — the backend ships the spread in
//     PERCENT POINTS in the FRONT − BACK convention; the display layer
//     flips the sign and multiplies by 100 to deliver bps in the
//     BACK − FRONT convention (mockup-faithful: positive bps = back
//     rate higher than front = steeper policy path).
//   - A separate decomposition row exposing the per-leg master stems +
//     current-front underlying contracts inline so the desk can audit
//     which contracts each strip slot resolves to today.
//   - The full P5 / ADR 0013 disclosure surfaces in the methodology card
//     via the backend's ``methodology_disclosure`` field (NOT a
//     hardcoded TS literal).
// ============================================================================

import { useNavigate } from 'react-router-dom';
import { useRequestFocusedMode } from '@/components/build/lib/focusedMode';
import {
  BuildExtendedShell,
  asOfDateControl,
  percentileLabel,
  signedFixed,
  toneForZScore,
  toneTextClass,
  type BuildExtendedProps,
  type ControlDescriptor,
  type TopRightCard,
} from '@/components/shared/build';
import {
  CALENDAR_PAIRS_BY_CURVE,
  POLICY_FUTURES_CALENDAR_COMPACT_CAVEAT,
  POLICY_FUTURES_CURVE_OPTIONS,
  buildMethodologyRows,
  buildReferenceBands,
  buildReferenceChips,
  buildStretchContext,
  calendarPairLabel,
  curveMetaFor,
  decompositionKPIs,
  extendedKPIs,
  flipWireZScore,
  sanitiseSpreadSeries,
  usePolicyFuturesCalendar,
  zScoreCaptionForSpread,
} from './futuresCalendarSpreadShared';

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
  const shortStr = params.strip_position_short ?? '1';
  const longStr = params.strip_position_long ?? '3';
  const shortPos = Number(shortStr) || 1;
  const longPos = Number(longStr) || 3;
  const lookbackDays = params.lookback_days ?? DEFAULTS.lookback_days;
  const fieldName = params.field_name || DEFAULTS.field_name;
  const asOfDate = params.as_of_date || undefined;

  const { data, isLoading, errorMessage } = usePolicyFuturesCalendar({
    curveFamily,
    stripPositionShort: shortPos,
    stripPositionLong: longPos,
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

  // Pair selector encodes the (short, long) tuple as a pipe-separated
  // string so the URL state stays flat.
  const pairKey = `${shortPos}|${longPos}`;
  const pairOptions = (
    CALENDAR_PAIRS_BY_CURVE[curveFamily]
    ?? CALENDAR_PAIRS_BY_CURVE.SOFR_FUT
  ).map((p) => ({
    value: `${p.short}|${p.long}`,
    label: p.label,
  }));

  const handleControlChange = (name: string, value: string) => {
    const nextParams = { ...params };
    if (name === 'curve_family') {
      nextParams.curve_family = value;
      // Snap to the first registered pair for the new curve so the
      // ordering stays valid against the new strip grid.
      const pairs = CALENDAR_PAIRS_BY_CURVE[value] ?? [];
      if (pairs.length > 0) {
        nextParams.strip_position_short = String(pairs[0].short);
        nextParams.strip_position_long = String(pairs[0].long);
      }
    } else if (name === 'pair') {
      const [s, l] = value.split('|');
      if (s && l) {
        nextParams.strip_position_short = s;
        nextParams.strip_position_long = l;
      }
    } else {
      nextParams[name] = value;
    }
    pushParams(nextParams);
  };

  const handleReset = () => {
    pushParams({
      curve_family: curveFamily,
      strip_position_short: shortStr,
      strip_position_long: longStr,
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
      name: 'pair',
      label: 'Pair',
      kind: 'enum',
      value: pairKey,
      options: pairOptions,
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

  // ----- Top-right cards: Z-score / Percentile / Strip-context -----
  const cm = data?.current_metrics;
  const displayZ = flipWireZScore(cm?.z_score_spread_implied_rate);
  const zCaption = zScoreCaptionForSpread(cm?.z_score_spread_implied_rate);
  const meta = curveMetaFor(curveFamily);
  const pairDisplay = calendarPairLabel(shortPos, longPos);
  const displayPercentile =
    cm?.percentile_252d != null && Number.isFinite(cm.percentile_252d)
      ? 100 - cm.percentile_252d
      : null;
  const topRightCards: ReadonlyArray<TopRightCard> = [
    {
      key: 'zscore',
      node: (
        <div className="card flex h-full flex-col gap-2 px-4 py-3">
          <span className="kicker text-fg-muted">Z-SCORE (252D)</span>
          <div
            className={`text-[26px] font-medium leading-none ${toneTextClass(toneForZScore(displayZ))}`}
          >
            {signedFixed(displayZ, 2)}
          </div>
          <span className={`text-[11.5px] ${toneTextClass(toneForZScore(displayZ))}`}>
            {zCaption}
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
            {percentileLabel(displayPercentile)}
          </div>
          <span className="text-[11.5px] text-fg-secondary">
            {displayPercentile != null
              ? displayPercentile >= 80
                ? 'High'
                : displayPercentile <= 20
                  ? 'Low'
                  : 'Normal'
              : 'Normal'}
          </span>
        </div>
      ),
    },
    {
      key: 'strip-context',
      node: (
        <div className="card flex h-full flex-col gap-1.5 px-4 py-3">
          <span className="kicker text-fg-muted">STRIP CONTEXT</span>
          <div className="text-[15px] font-medium leading-tight text-fg-primary">
            {meta ? `${meta.shortLabel} · ${pairDisplay}` : `${curveFamily} ${pairDisplay}`}
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

  // ----- Identity row -----
  const identityPrimary = meta
    ? `${meta.shortLabel} ${pairDisplay} Calendar Spread`
    : `${curveFamily} ${pairDisplay} Calendar Spread`;
  const identitySecondary = 'Strip (Implied Rate)';
  const identitySubtitle = meta
    ? `${meta.longLabel} — back-leg minus front-leg implied rate (bps display)`
    : `${curveFamily} — back-leg minus front-leg implied rate (bps display)`;

  // Combine the headline KPI strip and the decomposition row.  The shell
  // renders them as one continuous strip; the decomposition labels carry
  // their own master-stem markers so the grouping reads naturally.
  const allKpis = data
    ? [
        ...extendedKPIs(data),
        ...decompositionKPIs(data),
      ]
    : [
        {
          label: 'SPREAD',
          value: '—',
          unit: 'bp',
          tone: 'neutral' as const,
        },
        {
          label: '1D CHANGE',
          value: '—',
          unit: 'bp',
          tone: 'neutral' as const,
        },
        {
          label: 'Z-SCORE (252D)',
          value: '—',
          tone: 'neutral' as const,
        },
      ];

  return (
    <BuildExtendedShell
      category={{
        name: 'POLICY FUTURES CALENDAR SPREAD',
        tags: ['SNAPSHOT', 'DETERMINISTIC', 'CURVE SHAPE'],
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
      kpis={allKpis}
      chartPoints={sanitiseSpreadSeries(data?.time_series ?? []).map(
        (r) => ({ date: r.date, value: r.value ?? NaN }),
      )}
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
        kind: 'Deterministic snapshot (back-leg − front-leg on STIR strip, implied-rate axis in bps)',
        providers: [
          'TimescaleDB',
          'macro_data.v_market_data_daily_enriched',
          POLICY_FUTURES_CALENDAR_COMPACT_CAVEAT,
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
