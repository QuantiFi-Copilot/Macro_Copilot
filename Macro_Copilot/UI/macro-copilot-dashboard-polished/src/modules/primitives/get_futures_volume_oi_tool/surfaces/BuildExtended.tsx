// ============================================================================
// surfaces/BuildExtended.tsx — Extended Build view for the bond_futures
// variant of ``get_futures_volume_oi_tool``.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.1 + §5 every new primitive ships an extended
// view; this is the bond-futures rolling-generic volume/OI snapshot's full
// canvas.  Mounted by VirtualPrimitiveCanvas for single-tool queries OR by
// the click-to-expand modal from a compact card in a multi-tool DAG.
//
// Design choices (no mockups for this dispatch — shell-standard layout per
// the get_futures_price_level_tool sibling):
//   - Controls: Curve Family / Contract Code / Lookback ONLY.  The backend
//     FuturesVolumeOiInput exposes no field_name (PX_VOLUME + OPEN_INT are
//     YAML-owned conventions) and no z-score overrides (ADR 0013 V1
//     deterministic mode) — surfacing either would silently shadow the YAML.
//   - MAIN chart = the OPEN-INTEREST line (the positioning signal; rows map
//     {date, value: open_interest} via sanitiseOiSeries).  Volume context
//     (level / vs-22d-mean ratio / 22d max) lives in the KPI strip — the
//     shared MainChart is single-series and the shell's KPI strip is the
//     honest slot for the second tape.
//   - 10-cell KPI strip on the CONTRACT-COUNT axis (extendedKPIs).
//   - Top-right cards: OI Z-score / OI Percentile / Front-Contract context
//     (security_name + expiry_date + contract_size — the SCD2 identity row).
//   - Full P5 / ADR 0013 disclosure surfaces in the methodology card via the
//     backend's ``methodology_disclosure`` field (NOT a hardcoded TS literal
//     — wire-honesty per PR10 / P5).  FP9: every number rendered is a
//     backend float; client-side math is display-affordance only.
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
  FUTURES_VOI_CONTRACT_OPTIONS,
  FUTURES_VOI_CURVE_OPTIONS,
  FUTURES_VOLUME_OI_COMPACT_CAVEAT,
  buildMethodologyRows,
  buildReferenceBands,
  buildReferenceChips,
  buildStretchContext,
  contractMetaFor,
  extendedKPIs,
  formatContractsExact,
  sanitiseOiSeries,
  useFuturesVolumeOi,
} from './futuresVolumeOiShared';

const LOOKBACK_OPTIONS = [
  { value: '90', label: '90d' },
  { value: '180', label: '180d' },
  { value: '365', label: '365d' },
  { value: '730', label: '2Y' },
  { value: '1825', label: '5Y' },
];

const DEFAULTS = {
  lookback_days: '365',
};

/** First contract code in the registry for a given curve family — used to
 *  keep the (curve_family, contract_code) pair coherent when the user
 *  switches families (e.g. UST_FUT→DE_FUT snaps TY1→RX1 instead of
 *  shipping a mismatched pair the backend would reject). */
function firstContractForFamily(family: string): string | null {
  for (const opt of FUTURES_VOI_CONTRACT_OPTIONS) {
    const meta = contractMetaFor(opt.value);
    if (meta?.curveFamily === family) return opt.value;
  }
  return null;
}

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

  const { data, isLoading, errorMessage } = useFuturesVolumeOi({
    curveFamily,
    contractCode,
    lookbackDays: Number(lookbackDays),
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
    const nextParams = { ...params, [name]: value };
    if (name === 'curve_family') {
      // Keep the pair coherent — TD#11 keys the input on
      // (curve_family, contract_code); a stale contract from the previous
      // family would be rejected by the backend Pydantic validator.
      const meta = contractMetaFor(nextParams.contract_code ?? contractCode);
      if (!meta || meta.curveFamily !== value) {
        const snapped = firstContractForFamily(value);
        if (snapped) nextParams.contract_code = snapped;
      }
    } else if (name === 'contract_code') {
      // Selecting a contract uniquely determines its family.
      const meta = contractMetaFor(value);
      if (meta) nextParams.curve_family = meta.curveFamily;
    }
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
      options: FUTURES_VOI_CURVE_OPTIONS,
    },
    {
      name: 'contract_code',
      label: 'Contract',
      kind: 'enum',
      value: contractCode,
      options: FUTURES_VOI_CONTRACT_OPTIONS,
    },
    {
      name: 'lookback_days',
      label: 'Lookback',
      kind: 'enum',
      value: lookbackDays,
      options: LOOKBACK_OPTIONS,
    },
  ];

  // ----- Top-right cards: OI Z-score / OI Percentile / Front contract -----
  const cm = data?.current_metrics;
  const meta = contractMetaFor(contractCode);
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

  const contractTagline = meta?.longLabel ?? contractCode;
  const frontUnderlying = cm?.security_name ?? contractTagline;
  const frontDetail = [
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
      key: 'front-contract',
      node: (
        <div className="card flex h-full flex-col gap-1.5 px-4 py-3">
          <span className="kicker text-fg-muted">FRONT CONTRACT</span>
          <div className="text-[15px] font-medium leading-tight text-fg-primary">
            {frontUnderlying}
          </div>
          <span className="text-[11px] leading-snug text-fg-muted">
            {frontDetail || '—'}
          </span>
          <span className="text-[11px] leading-snug text-fg-muted">
            {FUTURES_VOLUME_OI_COMPACT_CAVEAT}
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
    ? `${meta.shortLabel} ${meta.tenor} · ${meta.curveFamily} · volume + open interest`
    : `${cm?.tenor ?? ''} · ${curveFamily} · volume + open interest`;

  return (
    <BuildExtendedShell
      category={{
        name: 'BOND FUTURES VOLUME / OPEN INTEREST',
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
                contract_code: contractCode,
                tenor: meta?.tenor ?? '',
                contract_size: null,
                expiry_date: null,
                security_name: null,
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
        kind: 'Deterministic snapshot (rolling-generic bond-futures volume + OI)',
        providers: [
          'TimescaleDB',
          'macro_data.v_market_data_daily_enriched',
          FUTURES_VOLUME_OI_COMPACT_CAVEAT,
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
