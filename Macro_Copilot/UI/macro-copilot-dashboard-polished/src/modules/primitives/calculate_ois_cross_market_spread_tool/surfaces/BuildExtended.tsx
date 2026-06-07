// ============================================================================
// surfaces/BuildExtended.tsx — Extended Build view for
// calculate_ois_cross_market_spread_tool.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.1 + §5 every new primitive ships an extended
// view; this is the cross-market OIS spread's full canvas.  Mounted by
// VirtualPrimitiveCanvas for single-tool queries OR by the click-to-expand
// modal from a compact card.  Design reference: ./mockups/Extended.png.
//
// Mockup-faithful design choices:
//   - Title "SOFR-ESTR 2Y OIS SPREAD" with per-leg subtitle.
//   - Two leg dropdowns (Leg A / Leg B) plus tenor + lookback + field
//     (the structural cross-curve invariant means we expose both legs).
//   - bps-scale KPI strip + per-leg PERCENT decomposition (LEG A / LEG B
//     endpoint OIS rates) so spread = leg_a - leg_b is auditable on screen.
//   - Top-right cards: Z-score / Percentile / Policy-Regime (with the
//     central-bank pair caption — "Fed vs ECB policy-rate differential").
//   - The policy-path-divergence caveat surfaces inline via the methodology
//     card.  TODO(PR10): when the backend ships
//     ``current_metrics.methodology_label`` the "Disclosure" row switches to
//     consume the wire — see ./crossMarketOisShared.ts for the marker.
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
  OIS_CROSS_MARKET_COMPACT_CAVEAT,
  OIS_CROSS_MARKET_TENOR_OPTIONS,
  OIS_FAMILY_OPTIONS,
  buildMethodologyRows,
  buildReferenceBands,
  buildReferenceChips,
  buildStretchContext,
  extendedKPIs,
  indexPairShortLabel,
  oisFamilyFor,
  pairCentralBankCaption,
  pairShortLabel,
  pairSubtitle,
  sanitiseSpreadSeries,
  useCrossMarketOisSpread,
} from './crossMarketOisShared';

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
  const legA = params.curve_family_1 ?? 'USD_SOFR_OIS';
  const legB = params.curve_family_2 ?? 'EUR_ESTR_OIS';
  const tenor = params.tenor ?? '2Y';
  const lookbackDays = params.lookback_days ?? DEFAULTS.lookback_days;
  const fieldName = params.field_name || DEFAULTS.field_name;

  const { data, isLoading, errorMessage } = useCrossMarketOisSpread({
    curveFamily1: legA,
    curveFamily2: legB,
    tenor,
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
    const nextParams = { ...params };
    nextParams[name] = value;
    // Cross-curve invariant: curve_family_1 != curve_family_2.  When the
    // user picks a leg that equals the other, snap the other to the first
    // different family (the backend schema layer re-validates regardless).
    if (name === 'curve_family_1' && value === nextParams.curve_family_2) {
      const fallback = OIS_FAMILY_OPTIONS.find((o) => o.value !== value);
      if (fallback) nextParams.curve_family_2 = fallback.value;
    }
    if (name === 'curve_family_2' && value === nextParams.curve_family_1) {
      const fallback = OIS_FAMILY_OPTIONS.find((o) => o.value !== value);
      if (fallback) nextParams.curve_family_1 = fallback.value;
    }
    pushParams(nextParams);
  };

  const handleReset = () => {
    pushParams({
      curve_family_1: legA,
      curve_family_2: legB,
      tenor,
      ...DEFAULTS,
    });
  };

  // ----- Controls -----
  const controls: ReadonlyArray<ControlDescriptor> = [
    {
      name: 'curve_family_1',
      label: 'Leg A',
      kind: 'enum',
      value: legA,
      options: OIS_FAMILY_OPTIONS,
    },
    {
      name: 'curve_family_2',
      label: 'Leg B',
      kind: 'enum',
      value: legB,
      options: OIS_FAMILY_OPTIONS,
    },
    {
      name: 'tenor',
      label: 'Tenor',
      kind: 'enum',
      value: tenor,
      options: OIS_CROSS_MARKET_TENOR_OPTIONS,
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

  // ----- Top-right cards: Z-score / Percentile / Policy Regime -----
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
  const aMeta = oisFamilyFor(legA);
  const bMeta = oisFamilyFor(legB);
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
      key: 'policy_regime',
      node: (
        <div className="card flex h-full flex-col gap-1.5 px-4 py-3">
          <span className="kicker text-fg-muted">POLICY REGIME</span>
          <div className="text-[15px] font-medium leading-tight text-fg-primary">
            {aMeta && bMeta
              ? `${aMeta.centralBank} vs ${bMeta.centralBank}`
              : `${legA} vs ${legB}`}
          </div>
          <span className="text-[11px] leading-snug text-fg-muted">
            {OIS_CROSS_MARKET_COMPACT_CAVEAT}
          </span>
        </div>
      ),
    },
  ];

  const indexPairLabel = indexPairShortLabel(legA, legB);
  const pairLabel = pairShortLabel(legA, legB);
  const identityPrimary = `${indexPairLabel} ${tenor} OIS SPREAD`;
  const identitySubtitle = pairSubtitle(legA, legB, tenor);

  return (
    <BuildExtendedShell
      category={{
        name: 'CROSS-MARKET OIS SPREAD',
        tags: ['SNAPSHOT', 'DETERMINISTIC', 'CROSS-MARKET'],
      }}
      identity={{
        primary: identityPrimary,
        secondary: `· ${pairLabel}`,
        flag: aMeta && bMeta ? `${aMeta.flag} ${bMeta.flag}` : undefined,
        subtitle: `${identitySubtitle} · ${pairCentralBankCaption(legA, legB)}`,
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
        data ? buildMethodologyRows(data, fieldName, Number(lookbackDays)) : []
      }
      methodologyReferences={buildReferenceChips()}
      lineage={{
        toolName,
        version: 'v1',
        kind: 'Deterministic snapshot (curve_family_1 − curve_family_2 same-tenor, inner-join, in bps)',
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
