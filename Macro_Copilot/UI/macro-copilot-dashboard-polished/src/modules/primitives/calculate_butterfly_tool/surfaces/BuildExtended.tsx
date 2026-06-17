// ============================================================================
// surfaces/BuildExtended.tsx — Extended Build view for
// calculate_butterfly_tool (sovereign butterfly).
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.1 + §5 every primitive ships an extended view;
// this is the same-curve sovereign butterfly's full canvas.  Mounted by
// VirtualPrimitiveCanvas for single-tool queries OR by the click-to-expand
// modal from a compact card.  Design reference: ./mockups/Extended.png.
//
// Mockup-faithful design choices:
//   - A SINGLE "Sovereign Curve" dropdown (single-curve primitive — distinct
//     from calculate_cross_market_spread_tool, which crosses two families).
//   - A SINGLE "Triplet" dropdown — registered (short, belly, long) presets
//     per curve_family.  The Pydantic schema's ``_tenors_must_all_differ``
//     validator rejects duplicate orderings at the input layer; surfacing
//     three independent tenor dropdowns would invite invalid selections.
//   - bps-scale KPI strip — backend already ships the butterfly + 1d change
//     + 252d range + wing spreads in BPS (no unit conversion).  5D / 1M
//     change + observation count are re-derived client-side from
//     time_series_butterfly because the wire is leaner than the linker /
//     ZCIS butterfly siblings.
//   - A separate decomposition row exposing the three endpoint yields
//     (PERCENT) + two wing spreads (BPS) so the desk can audit
//     ``(2 × belly − short − long) × 100`` on the same screen.
//   - The "sovereign fly; cross-check OIS fly" caveat surfaces in the
//     methodology card.  TODO(PR10): when the backend ships
//     ``current_metrics.methodology_label`` the "Disclosure" row switches
//     to consume the wire — see ./butterflyShared.ts for the marker.
// ============================================================================

import { useNavigate } from 'react-router-dom';
import { useRequestFocusedMode } from '@/components/build/lib/focusedMode';
import {
  BuildExtendedShell,
  asOfDateControl,
  bucketForPercentile,
  percentileLabel,
  signedFixed,
  toneForZScore,
  toneTextClass,
  type BuildExtendedProps,
  type ControlDescriptor,
  type TopRightCard,
} from '@/components/shared/build';
import {
  BUTTERFLY_CURVE_OPTIONS,
  BUTTERFLY_TRIPLETS_BY_CURVE,
  SOVEREIGN_BUTTERFLY_COMPACT_CAVEAT,
  buildMethodologyRows,
  buildReferenceBands,
  buildReferenceChips,
  buildStretchContext,
  decompositionKPIs,
  extendedKPIs,
  sanitiseButterflySeries,
  sovereignFamilyFor,
  tripletHyphenLabel,
  useButterfly,
  zScoreCaptionForButterfly,
} from './butterflyShared';

const LOOKBACK_OPTIONS = [
  { value: '90', label: '90d' },
  { value: '180', label: '180d' },
  { value: '365', label: '365d' },
  { value: '730', label: '2Y' },
  { value: '1825', label: '5Y' },
];

const FIELD_OPTIONS = [
  { value: 'YLD_YTM_MID', label: 'YLD_YTM_MID' },
  { value: 'YLD_YTM_BID', label: 'YLD_YTM_BID' },
  { value: 'YLD_YTM_ASK', label: 'YLD_YTM_ASK' },
];

const DEFAULTS = {
  lookback_days: '365',
  field_name: 'YLD_YTM_MID',
};

const BuildExtended: React.FC<BuildExtendedProps> = ({
  toolName,
  params,
  onParamsChange,
}) => {
  const navigate = useNavigate();
  useRequestFocusedMode(true);

  // ----- Resolve effective params -----
  const curveFamily = params.curve_family ?? 'UST';
  const shortTenor = params.short_tenor ?? '2Y';
  const bellyTenor = params.belly_tenor ?? '5Y';
  const longTenor = params.long_tenor ?? '10Y';
  const lookbackDays = params.lookback_days ?? DEFAULTS.lookback_days;
  const fieldName = params.field_name || DEFAULTS.field_name;

  const { data, isLoading, errorMessage } = useButterfly({
    curveFamily,
    shortTenor,
    bellyTenor,
    longTenor,
    lookbackDays: Number(lookbackDays),
    fieldName,
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

  // Triplet selector encodes the (short, belly, long) tuple as a single
  // string ("2Y|5Y|10Y") to keep the URL state flat.
  const tripletKey = `${shortTenor}|${bellyTenor}|${longTenor}`;
  const tripletOptions = (
    BUTTERFLY_TRIPLETS_BY_CURVE[curveFamily]
    ?? BUTTERFLY_TRIPLETS_BY_CURVE.UST
  ).map((t) => ({
    value: `${t.short}|${t.belly}|${t.long}`,
    label: t.label,
  }));

  const handleControlChange = (name: string, value: string) => {
    const nextParams = { ...params };
    if (name === 'curve_family') {
      nextParams.curve_family = value;
      // Snap to the first registered triplet for the new curve so the
      // (short, belly, long) tuple stays valid against the new pillar grid.
      const triplets = BUTTERFLY_TRIPLETS_BY_CURVE[value] ?? [];
      if (triplets.length > 0) {
        nextParams.short_tenor = triplets[0].short;
        nextParams.belly_tenor = triplets[0].belly;
        nextParams.long_tenor = triplets[0].long;
      }
    } else if (name === 'triplet') {
      const [s, b, l] = value.split('|');
      if (s && b && l) {
        nextParams.short_tenor = s;
        nextParams.belly_tenor = b;
        nextParams.long_tenor = l;
      }
    } else {
      nextParams[name] = value;
    }
    pushParams(nextParams);
  };

  const handleReset = () => {
    pushParams({
      curve_family: curveFamily,
      short_tenor: shortTenor,
      belly_tenor: bellyTenor,
      long_tenor: longTenor,
      ...DEFAULTS,
    });
  };

  // ----- Controls -----
  const controls: ReadonlyArray<ControlDescriptor> = [
    {
      name: 'curve_family',
      label: 'Sovereign Curve',
      kind: 'enum',
      value: curveFamily,
      options: BUTTERFLY_CURVE_OPTIONS,
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
    asOfDateControl(params.as_of_date),
  ];

  // ----- Top-right cards: Z-score / Percentile / Sovereign curve -----
  const cm = data?.current_metrics;
  const zCaption = zScoreCaptionForButterfly(cm?.current_z_score);
  const pBucket = bucketForPercentile(cm?.percentile_252d ?? null);
  const familyMeta = sovereignFamilyFor(curveFamily);
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
          <span className="text-[11.5px] text-fg-secondary">{pBucket}</span>
        </div>
      ),
    },
    {
      key: 'sovereign_curve',
      node: (
        <div className="card flex h-full flex-col gap-1.5 px-4 py-3">
          <span className="kicker text-fg-muted">SOVEREIGN CURVE</span>
          <div className="text-[15px] font-medium leading-tight text-fg-primary">
            {familyMeta
              ? `${familyMeta.shortLabel} · ${familyMeta.longLabel}`
              : curveFamily}
          </div>
          <span className="text-[11px] leading-snug text-fg-muted">
            {SOVEREIGN_BUTTERFLY_COMPACT_CAVEAT}
          </span>
        </div>
      ),
    },
  ];

  const triplet = tripletHyphenLabel(shortTenor, bellyTenor, longTenor);
  const identityPrimary = familyMeta
    ? `${familyMeta.shortLabel} ${triplet} BUTTERFLY`
    : `${curveFamily} ${triplet} BUTTERFLY`;
  const identitySubtitle = familyMeta
    ? `${familyMeta.longLabel} curve curvature · (2 × belly − short − long)`
    : `${curveFamily} ${shortTenor} · ${bellyTenor} · ${longTenor} · single sovereign curve`;

  // Combine the headline KPI strip and the decomposition row.  The shell
  // renders them as one continuous strip; the decomposition labels carry
  // their own tenor markers so the grouping reads naturally.
  const allKpis = data
    ? [
        ...extendedKPIs(data),
        ...decompositionKPIs(data, shortTenor, bellyTenor, longTenor),
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
        name: 'SOVEREIGN BUTTERFLY',
        tags: ['SNAPSHOT', 'DETERMINISTIC', 'CURVATURE'],
      }}
      identity={{
        primary: identityPrimary,
        secondary: familyMeta ? `· ${familyMeta.shortLabel}` : undefined,
        flag: familyMeta?.flag,
        subtitle: identitySubtitle,
        asOfDate: cm?.as_of_date,
        meta: `${lookbackDays}d window · ${fieldName}`,
      }}
      topRightCards={topRightCards}
      controls={controls}
      onControlChange={handleControlChange}
      onResetControls={handleReset}
      kpis={allKpis}
      chartPoints={sanitiseButterflySeries(
        data?.time_series_butterfly?.rows ?? [],
      ).map((r) => ({ date: r.date, value: r.value ?? NaN }))}
      chartUnit="bps"
      chartValueDecimals={1}
      referenceBands={data ? buildReferenceBands(data) : []}
      stretchContext={data ? buildStretchContext(data) : undefined}
      methodology={
        data
          ? buildMethodologyRows(
              data,
              fieldName,
              Number(lookbackDays),
              shortTenor,
              bellyTenor,
              longTenor,
            )
          : []
      }
      methodologyReferences={buildReferenceChips()}
      lineage={{
        toolName,
        version: 'v1',
        kind: 'Deterministic snapshot ((2×belly − short − long) on single sovereign curve, raw yield space)',
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
