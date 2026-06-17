// ============================================================================
// surfaces/BuildExtended.tsx — Extended Build view for
// calculate_otr_ofr_spread_tool.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.1 + §5 every new primitive ships an extended
// view; this is OTR/OFR spread's full canvas.  Mounted by
// VirtualPrimitiveCanvas for single-tool queries OR by the click-to-expand
// modal from a compact card.  Design reference: ./mockups/Extended.png.
//
// Mockup-faithful design choices:
//   - Identity row "UST 10Y OTR-OFR SPREAD" + OTR bond identifier subtitle.
//   - Controls strip (country + tenor + lookback + field) — NO z-score
//     overrides (the primitive YAML-locks them per config.yaml).
//   - bps-scale KPI strip + per-leg OTR / OFR yield decomposition so the
//     desk can audit the spread end-to-end without a second tool call.
//   - Methodology card threads ``data.methodology_note`` (TOP-LEVEL on the
//     Output; NOT current_metrics.methodology_label — TD #27 disclosure).
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
  COUNTRY_OPTIONS,
  TENOR_OPTIONS,
  bondIdentifier,
  buildMethodologyRows,
  buildReferenceBands,
  buildReferenceChips,
  buildStretchContext,
  countryMetaFor,
  extendedKPIs,
  identitySubtitle,
  sanitiseSpreadSeries,
  useOtrOfrSpreadData,
} from './otrOfrSpreadShared';

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

const SHORT_CAVEAT =
  'Liquidity-premium PROXY — sign POSITIVE = OTR cheap to OFR.';

const BuildExtended: React.FC<BuildExtendedProps> = ({
  toolName,
  params,
  onParamsChange,
}) => {
  const navigate = useNavigate();
  useRequestFocusedMode(true);

  // ----- Resolve effective params -----
  const country = (params.country ?? 'US').toUpperCase();
  const tenor = params.tenor ?? '10Y';
  const lookbackDays = params.lookback_days ?? DEFAULTS.lookback_days;
  const fieldName = params.field_name || DEFAULTS.field_name;

  const { data, isLoading, errorMessage } = useOtrOfrSpreadData({
    country,
    tenor,
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

  const handleControlChange = (name: string, value: string) => {
    const nextParams = { ...params };
    nextParams[name] = value;
    pushParams(nextParams);
  };

  const handleReset = () => {
    pushParams({
      country,
      tenor,
      ...DEFAULTS,
    });
  };

  // ----- Controls -----
  const controls: ReadonlyArray<ControlDescriptor> = [
    {
      name: 'country',
      label: 'Country',
      kind: 'enum',
      value: country,
      options: COUNTRY_OPTIONS,
    },
    {
      name: 'tenor',
      label: 'Tenor',
      kind: 'enum',
      value: tenor,
      options: TENOR_OPTIONS,
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

  // ----- Top-right cards: Z-score / Percentile / Liquidity-premium caveat
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
  const meta = countryMetaFor(country);
  const otrId = bondIdentifier(
    cm?.otr_cusip ?? null,
    cm?.otr_isin ?? null,
  );
  const ofrId = bondIdentifier(
    cm?.ofr_cusip ?? null,
    cm?.ofr_isin ?? null,
  );

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
      key: 'liquidity_premium',
      node: (
        <div className="card flex h-full flex-col gap-1.5 px-4 py-3">
          <span className="kicker text-fg-muted">LIQUIDITY PREMIUM</span>
          <div className="text-[15px] font-medium leading-tight text-fg-primary">
            {meta ? `${meta.short} · ${tenor}` : `${country} · ${tenor}`}
          </div>
          <span className="text-[11px] leading-snug text-fg-muted">
            {SHORT_CAVEAT}
          </span>
        </div>
      ),
    },
  ];

  return (
    <BuildExtendedShell
      category={{
        name: 'OTR-OFR SPREAD',
        tags: ['SNAPSHOT', 'DETERMINISTIC', 'LIQUIDITY PROXY'],
      }}
      identity={{
        primary: meta ? `${meta.short} ${tenor}` : `${country} ${tenor}`,
        secondary: 'OTR-OFR',
        flag: meta?.flag,
        subtitle: identitySubtitle(country),
        asOfDate: cm?.as_of_date,
        meta: `OTR ${otrId} · OFR ${ofrId} · ${lookbackDays}d window · ${fieldName}`,
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
                country,
                tenor,
                slot_label: '',
                current_spread_bps: null,
                daily_change_bps: null,
                current_z_score: null,
                rolling_window_days: 252,
                high_252d_bps: null,
                low_252d_bps: null,
                percentile_252d: null,
                otr_yield_pct: null,
                ofr_yield_pct: null,
                otr_instrument_id: null,
                otr_cusip: null,
                otr_isin: null,
                otr_vendor_ticker: null,
                ofr_instrument_id: null,
                ofr_cusip: null,
                ofr_isin: null,
                ofr_vendor_ticker: null,
                observation_count: 0,
              },
              time_series: [],
              time_series_spread: {
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
              methodology_note: '',
            })
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
          ? buildMethodologyRows(data, fieldName, Number(lookbackDays))
          : []
      }
      methodologyReferences={buildReferenceChips()}
      lineage={{
        toolName,
        version: 'v1',
        kind: 'Deterministic snapshot (OTR yield − OFR yield × 100)',
        providers: [
          'TimescaleDB',
          'macro_data.otr_history',
          'macro_data.market_data_daily',
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
