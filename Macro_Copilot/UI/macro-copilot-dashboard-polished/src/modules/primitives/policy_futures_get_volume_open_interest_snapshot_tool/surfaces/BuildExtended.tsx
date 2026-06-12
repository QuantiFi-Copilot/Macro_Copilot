// ============================================================================
// surfaces/BuildExtended.tsx — Extended Build view for
// ``policy_futures_get_volume_open_interest_snapshot_tool``.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.1 + §5 every primitive claiming
// ``custom_build_surface`` ships an extended view; this is the STIR
// strip-slot volume/OI snapshot's full canvas.  Cell-for-cell mirror of
// the bond_futures sibling
// (``../../get_futures_volume_oi_tool/surfaces/BuildExtended.tsx`` — P3,
// same Pydantic Output class on the wire) with the identity keyed by
// (curve_family, strip_position) instead of (curve_family, contract_code):
//   - Controls: Curve Family / Strip Position / Lookback ONLY.  The
//     backend Input exposes no field_name (PX_VOLUME + OPEN_INT are
//     YAML-owned, PR9) and no z-score overrides.
//   - MAIN chart = the OPEN-INTEREST line (positioning signal); volume
//     context lives in the KPI strip (shared MainChart is single-series).
//   - 10-cell KPI strip on the CONTRACT-COUNT axis (extendedKPIs).
//   - Top-right cards: OI Z-score / OI Percentile / Front-Underlying
//     context (underlying_contract_code + expiry + contract_size).
//   - Full P5 / ADR 0013 disclosure surfaces via the backend's
//     ``methodology_disclosure`` field (never a TSX literal).  FP9: every
//     number rendered is a backend float.
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
  POLICY_VOI_COMPACT_CAVEAT,
  POLICY_VOI_CURVE_OPTIONS,
  POLICY_VOI_STRIP_OPTIONS,
  buildMethodologyRows,
  buildReferenceBands,
  buildReferenceChips,
  buildStretchContext,
  curveMetaFor,
  extendedKPIs,
  formatContractsExact,
  sanitiseOiSeries,
  stripSegmentLabel,
  usePolicyVoiSnapshot,
} from './policyFuturesVoiShared';

const LOOKBACK_OPTIONS = [
  { value: '90', label: '90d' },
  { value: '180', label: '180d' },
  { value: '365', label: '365d' },
  { value: '730', label: '2Y' },
  { value: '1825', label: '5Y' },
];

const DEFAULTS = {
  curve_family: 'SOFR_FUT',
  strip_position: '1',
  lookback_days: '365',
};

const BuildExtended: React.FC<BuildExtendedProps> = ({
  toolName,
  params,
  onParamsChange,
}) => {
  const navigate = useNavigate();
  useRequestFocusedMode(true);

  // ----- Resolve effective params -----
  const curveFamily = params.curve_family || DEFAULTS.curve_family;
  const stripPositionStr = params.strip_position || DEFAULTS.strip_position;
  const stripPosition = Number(stripPositionStr);
  const lookbackDays = params.lookback_days || DEFAULTS.lookback_days;

  const { data, isLoading, errorMessage } = usePolicyVoiSnapshot({
    curveFamily,
    stripPosition,
    lookbackDays: Number(lookbackDays),
  });

  // ----- URL update on control change -----
  const pushParams = (nextParams: Record<string, string>) => {
    // Inside the multi-tool DAG expand-to-modal, edits stay local
    // (onParamsChange) instead of navigating the global URL.
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
    pushParams({ ...DEFAULTS });
  };

  // ----- Controls -----
  const controls: ReadonlyArray<ControlDescriptor> = [
    {
      name: 'curve_family',
      label: 'Curve Family',
      kind: 'enum',
      value: curveFamily,
      options: POLICY_VOI_CURVE_OPTIONS,
    },
    {
      name: 'strip_position',
      label: 'Strip Position',
      kind: 'enum',
      value: stripPositionStr,
      options: POLICY_VOI_STRIP_OPTIONS,
    },
    {
      name: 'lookback_days',
      label: 'Lookback',
      kind: 'enum',
      value: lookbackDays,
      options: LOOKBACK_OPTIONS,
    },
  ];

  // ----- Top-right cards: OI Z-score / OI Percentile / Front underlying -----
  const cm = data?.current_metrics;
  const meta = curveMetaFor(curveFamily);
  const z = cm?.oi_z_score;
  const zRegime = regimeForZScore(z);
  const pBucket =
    cm?.oi_percentile_252d != null
      ? cm.oi_percentile_252d >= 80
        ? 'High'
        : cm.oi_percentile_252d <= 20
          ? 'Low'
          : 'Normal'
      : 'Normal';

  const stemCode =
    cm?.contract_code ??
    (meta ? `${meta.stripStemPrefix}${stripPositionStr}` : `#${stripPositionStr}`);
  const frontUnderlying =
    cm?.underlying_contract_code ?? cm?.security_name ?? '—';
  const frontDetail = [
    cm?.security_name && cm?.underlying_contract_code ? cm.security_name : null,
    cm?.expiry_date ? `expires ${cm.expiry_date}` : null,
    cm?.contract_size != null
      ? `size ${formatContractsExact(cm.contract_size)}`
      : null,
  ]
    .filter(Boolean)
    .join(' · ');

  const topRightCards: ReadonlyArray<TopRightCard> = [
    {
      key: 'oi-zscore',
      node: (
        <div className="card flex h-full flex-col gap-2 px-4 py-3">
          <span className="kicker text-fg-muted">OI Z-SCORE (252D)</span>
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
      key: 'oi-percentile',
      node: (
        <div className="card flex h-full flex-col gap-2 px-4 py-3">
          <span className="kicker text-fg-muted">OI PERCENTILE (252D)</span>
          <div className="text-[26px] font-medium leading-none text-fg-primary">
            {percentileLabel(cm?.oi_percentile_252d ?? null)}
          </div>
          <span className="text-[11.5px] text-fg-secondary">{pBucket}</span>
        </div>
      ),
    },
    {
      key: 'front-underlying',
      node: (
        <div className="card flex h-full flex-col gap-1.5 px-4 py-3">
          <span className="kicker text-fg-muted">FRONT UNDERLYING</span>
          <div className="text-[15px] font-medium leading-tight text-fg-primary">
            {frontUnderlying}
          </div>
          <span className="text-[11px] leading-snug text-fg-muted">
            {frontDetail || '—'}
          </span>
          <span className="text-[11px] leading-snug text-fg-muted">
            {POLICY_VOI_COMPACT_CAVEAT}
          </span>
        </div>
      ),
    },
  ];

  // ----- Identity row.  IdentityBlock inserts a `·` between primary and
  //       secondary; pass BARE strings — do NOT prepend '· '.
  const identityPrimary = stemCode;
  const identitySecondary = meta?.longLabel ?? curveFamily;
  const identitySubtitle = meta
    ? `${meta.shortLabel} strip position ${stripPositionStr} (${stripSegmentLabel(stripPosition).toLowerCase()}) · ${meta.regime} · volume + open interest`
    : `strip position ${stripPositionStr} · ${curveFamily} · volume + open interest`;

  return (
    <BuildExtendedShell
      category={{
        name: 'POLICY FUTURES VOLUME / OPEN INTEREST',
        tags: ['SNAPSHOT', 'DETERMINISTIC', 'CONTRACTS'],
      }}
      identity={{
        primary: identityPrimary,
        secondary: identitySecondary,
        flag: meta?.flag,
        subtitle: identitySubtitle,
        asOfDate: cm?.as_of_date,
        meta: `${lookbackDays}d window · PX_VOLUME + OPEN_INT`,
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
                contract_code: stemCode,
                underlying_contract_code: null,
                security_name: null,
                expiry_date: null,
                contract_size: null,
                current_volume: NaN,
                current_open_interest: NaN,
                delta_open_interest_1d: null,
                oi_z_score: null,
                oi_high_252d: null,
                oi_low_252d: null,
                oi_percentile_252d: null,
                volume_rolling_mean_22d: null,
                volume_rolling_max_22d: null,
                observation_count: 0,
              },
              time_series: [],
              methodology_disclosure: '',
            })
      }
      chartPoints={sanitiseOiSeries(data?.time_series ?? []).map((r) => ({
        date: r.date,
        value: r.value ?? NaN,
      }))}
      chartUnit="contracts"
      chartValueDecimals={0}
      referenceBands={data ? buildReferenceBands(data) : []}
      stretchContext={data ? buildStretchContext(data) : undefined}
      methodology={data ? buildMethodologyRows(data) : []}
      methodologyReferences={buildReferenceChips()}
      lineage={{
        toolName,
        version: 'v1',
        kind: 'Deterministic snapshot (STIR strip-slot volume + OI)',
        providers: [
          'TimescaleDB',
          'macro_data.v_market_data_daily_enriched',
          POLICY_VOI_COMPACT_CAVEAT,
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
