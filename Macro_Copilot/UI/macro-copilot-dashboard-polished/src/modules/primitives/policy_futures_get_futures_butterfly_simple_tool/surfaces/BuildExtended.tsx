// ============================================================================
// surfaces/BuildExtended.tsx — Extended Build view for
// policy_futures_get_futures_butterfly_simple_tool.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.1 + §5 every new primitive ships an extended
// view; this is the same-curve STIR simple butterfly's full canvas.
// Mounted by VirtualPrimitiveCanvas for single-tool queries OR by the
// click-to-expand modal from a compact card in a multi-tool DAG.
//
// Design reference: ./mockups/Extended.png.  Mockup-faithful design
// choices:
//   - A SINGLE "Curve Family" dropdown (single-curve primitive — distinct
//     from policy_futures_get_futures_cross_market_spread which crosses
//     two families).
//   - A SINGLE "Triplet" dropdown — registered (wing_short, body,
//     wing_long) presets per curve_family.  The Pydantic schema's
//     ``_strip_positions_must_be_ordered`` validator rejects any unordered
//     or duplicate ordering; surfacing three independent dropdowns would
//     invite invalid selections.
//   - bps-scale headline KPI strip — the backend ships the butterfly in
//     PERCENT POINTS; the display layer multiplies by 100 to deliver bps
//     (the desk-recognised quote-size unit for a 3-strip-slot curvature).
//   - A separate decomposition row exposing the three per-leg implied
//     rates (PERCENT) with both master-stem AND current-front underlying
//     contracts surfaced inline so the desk can audit the construction.
//   - The full P5 / ADR 0013 disclosure surfaces in the methodology card
//     via the backend's ``methodology_disclosure`` field (NOT a hardcoded
//     TS literal).
// ============================================================================

import { useNavigate } from 'react-router-dom';
import { useRequestFocusedMode } from '@/components/build/lib/focusedMode';
import {
  BuildExtendedShell,
  percentileLabel,
  signedFixed,
  toneForZScore,
  toneTextClass,
  type BuildExtendedProps,
  type ControlDescriptor,
  type TopRightCard,
} from '@/components/shared/build';
import {
  BUTTERFLY_TRIPLETS_BY_CURVE,
  POLICY_FUTURES_BUTTERFLY_COMPACT_CAVEAT,
  POLICY_FUTURES_CURVE_OPTIONS,
  buildMethodologyRows,
  buildReferenceBands,
  buildReferenceChips,
  buildStretchContext,
  butterflyTripletLabel,
  curveMetaFor,
  decompositionKPIs,
  extendedKPIs,
  sanitiseButterflySeries,
  usePolicyFuturesButterfly,
  zScoreCaptionForButterfly,
} from './futuresButterflySimpleShared';

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
  const wingShortStr = params.strip_position_wing_short ?? '1';
  const bodyStr = params.strip_position_body ?? '2';
  const wingLongStr = params.strip_position_wing_long ?? '3';
  const wingShort = Number(wingShortStr) || 1;
  const body = Number(bodyStr) || 2;
  const wingLong = Number(wingLongStr) || 3;
  const lookbackDays = params.lookback_days ?? DEFAULTS.lookback_days;
  const fieldName = params.field_name || DEFAULTS.field_name;
  const asOfDate = params.as_of_date || undefined;

  const { data, isLoading, errorMessage } = usePolicyFuturesButterfly({
    curveFamily,
    stripPositionWingShort: wingShort,
    stripPositionBody: body,
    stripPositionWingLong: wingLong,
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

  // Triplet selector encodes the (wing_short, body, wing_long) tuple as a
  // pipe-separated string so the URL state stays flat.
  const tripletKey = `${wingShort}|${body}|${wingLong}`;
  const tripletOptions = (
    BUTTERFLY_TRIPLETS_BY_CURVE[curveFamily]
    ?? BUTTERFLY_TRIPLETS_BY_CURVE.SOFR_FUT
  ).map((t) => ({
    value: `${t.wingShort}|${t.body}|${t.wingLong}`,
    label: t.label,
  }));

  const handleControlChange = (name: string, value: string) => {
    const nextParams = { ...params };
    if (name === 'curve_family') {
      nextParams.curve_family = value;
      // Snap to the first registered triplet for the new curve so the
      // ordering stays valid against the new strip grid.
      const triplets = BUTTERFLY_TRIPLETS_BY_CURVE[value] ?? [];
      if (triplets.length > 0) {
        nextParams.strip_position_wing_short = String(triplets[0].wingShort);
        nextParams.strip_position_body = String(triplets[0].body);
        nextParams.strip_position_wing_long = String(triplets[0].wingLong);
      }
    } else if (name === 'triplet') {
      const [s, b, l] = value.split('|');
      if (s && b && l) {
        nextParams.strip_position_wing_short = s;
        nextParams.strip_position_body = b;
        nextParams.strip_position_wing_long = l;
      }
    } else {
      nextParams[name] = value;
    }
    pushParams(nextParams);
  };

  const handleReset = () => {
    pushParams({
      curve_family: curveFamily,
      strip_position_wing_short: wingShortStr,
      strip_position_body: bodyStr,
      strip_position_wing_long: wingLongStr,
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
      name: 'triplet',
      label: 'Triplet',
      kind: 'enum',
      value: tripletKey,
      options: tripletOptions,
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

  // ----- Top-right cards: Z-score / Percentile / Strip-context -----
  const cm = data?.current_metrics;
  const zCaption = zScoreCaptionForButterfly(cm?.z_score_butterfly);
  const meta = curveMetaFor(curveFamily);
  const tripletDisplay = butterflyTripletLabel(curveFamily, wingShort, body, wingLong);
  const topRightCards: ReadonlyArray<TopRightCard> = [
    {
      key: 'zscore',
      node: (
        <div className="card flex h-full flex-col gap-2 px-4 py-3">
          <span className="kicker text-fg-muted">Z-SCORE (252D)</span>
          <div
            className={`text-[26px] font-medium leading-none ${toneTextClass(toneForZScore(cm?.z_score_butterfly))}`}
          >
            {signedFixed(cm?.z_score_butterfly ?? null, 2)}
          </div>
          <span className={`text-[11.5px] ${toneTextClass(toneForZScore(cm?.z_score_butterfly))}`}>
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
            {percentileLabel(cm?.percentile_252d ?? null)}
          </div>
          <span className="text-[11.5px] text-fg-secondary">
            {cm?.percentile_252d != null
              ? cm.percentile_252d >= 80
                ? 'High'
                : cm.percentile_252d <= 20
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
            {meta ? `${meta.shortLabel} · ${tripletDisplay}` : `${curveFamily} ${tripletDisplay}`}
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
    ? `${meta.shortLabel} ${tripletDisplay} Butterfly`
    : `${curveFamily} ${tripletDisplay} Butterfly`;
  const identitySecondary = 'Strip (Implied Rate)';
  const identitySubtitle = meta
    ? `${meta.longLabel} — body − 0.5 × (wing_short + wing_long), implied-rate axis`
    : `${curveFamily} — body − 0.5 × (wing_short + wing_long), implied-rate axis`;

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
          label: 'BUTTERFLY',
          value: '—',
          unit: 'bps',
          tone: 'neutral' as const,
        },
        {
          label: '1D CHANGE',
          value: '—',
          unit: 'bps',
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
        name: 'POLICY FUTURES BUTTERFLY',
        tags: ['SNAPSHOT', 'DETERMINISTIC', 'CURVATURE'],
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
      chartPoints={sanitiseButterflySeries(data?.time_series ?? []).map(
        (r) => ({ date: r.date, value: r.value ?? NaN }),
      )}
      chartUnit="bps"
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
        kind: 'Deterministic snapshot (body − 0.5 × (wing_short + wing_long) on STIR strip, implied-rate axis)',
        providers: [
          'TimescaleDB',
          'macro_data.v_market_data_daily_enriched',
          POLICY_FUTURES_BUTTERFLY_COMPACT_CAVEAT,
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
