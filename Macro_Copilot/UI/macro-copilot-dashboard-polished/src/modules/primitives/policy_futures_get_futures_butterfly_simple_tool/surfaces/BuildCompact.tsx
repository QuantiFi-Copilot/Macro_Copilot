// ============================================================================
// surfaces/BuildCompact.tsx — Compact Build view for
// policy_futures_get_futures_butterfly_simple_tool.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.2 + §5 every new primitive ships a compact
// view; this is the same-curve STIR simple butterfly's.  Mounted as a node
// body inside multi-tool query DAG visualizations (e.g. *"compare SFR
// 1-2-3 fly vs ER 1-2-3 fly"*).
//
// Design reference: ./mockups/Compact.png.
//
// Per the Option-(c) precedent (Batch 1 fdac7d2 +
// policy_futures_get_futures_price_level_tool d8e8233) this file uses the
// SHELL-STANDARD density (3 KPIs + single identity line).  The mockup's
// denser layout — extra header chips (USA flag · STIR · 50/50 wings) plus
// the explicit ±2σ band annotations on the sparkline — is preserved
// visually; promoting any extra KPI cells into the compact view would
// require extending BuildCompactShell with a ``secondaryKpis`` slot,
// touching 10+ shipped compact views.  See THESIS.md → Mockup conformance
// for the deferral rationale.
// ============================================================================

import { Sparkles } from 'lucide-react';
import {
  BuildCompactShell,
  type BuildCompactProps,
} from '@/components/shared/build';
import {
  POLICY_FUTURES_BUTTERFLY_COMPACT_CAVEAT,
  buildReferenceBands,
  butterflyTripletLabel,
  compactKPIs,
  curveMetaFor,
  sanitiseButterflySeries,
  stripSegmentLabel,
  usePolicyFuturesButterfly,
} from './futuresButterflySimpleShared';

const BuildCompact: React.FC<BuildCompactProps> = ({
  params,
  size = 'small',
  onExpand,
  callMeta,
}) => {
  const curveFamilyParam = params.curve_family ?? '';
  const wingShortNum =
    params.strip_position_wing_short
    && Number(params.strip_position_wing_short)
      ? Number(params.strip_position_wing_short)
      : 0;
  const bodyNum =
    params.strip_position_body && Number(params.strip_position_body)
      ? Number(params.strip_position_body)
      : 0;
  const wingLongNum =
    params.strip_position_wing_long && Number(params.strip_position_wing_long)
      ? Number(params.strip_position_wing_long)
      : 0;

  const { data, isLoading, errorMessage } = usePolicyFuturesButterfly({
    curveFamily: curveFamilyParam,
    stripPositionWingShort: wingShortNum,
    stripPositionBody: bodyNum,
    stripPositionWingLong: wingLongNum,
    lookbackDays:
      params.lookback_days != null && params.lookback_days !== ''
        ? Number(params.lookback_days)
        : undefined,
    asOfDate: params.as_of_date || undefined,
    fieldName: params.field_name || undefined,
  });

  const cm = data?.current_metrics;
  const curveFamily = cm?.curve_family ?? curveFamilyParam;
  const wingShort = cm?.strip_position_wing_short ?? wingShortNum;
  const body = cm?.strip_position_body ?? bodyNum;
  const wingLong = cm?.strip_position_wing_long ?? wingLongNum;
  const meta = curveMetaFor(curveFamily);
  const tripletDisplay =
    wingShort > 0 && body > 0 && wingLong > 0
      ? butterflyTripletLabel(curveFamily, wingShort, body, wingLong)
      : '—';
  // Segment chip — single segment when all three legs share Whites/Reds;
  // mixed otherwise.  Mockup shows "WHITES" for SFR 1-2-3.
  const segments = new Set<string>();
  if (wingShort > 0) segments.add(stripSegmentLabel(wingShort));
  if (body > 0) segments.add(stripSegmentLabel(body));
  if (wingLong > 0) segments.add(stripSegmentLabel(wingLong));
  const segmentTag =
    segments.size === 1 ? [...segments][0] : segments.size > 1 ? 'Mixed' : '';

  // Mockup-faithful identity row:
  //   primary:   "SFR · 1-2-3 · WHITES"   (master short + triplet + segment)
  //   secondary: "SOFR Strip Butterfly (Implied Rate)"
  const primary = meta
    ? `${meta.shortLabel} · ${tripletDisplay}${segmentTag ? ` · ${segmentTag}` : ''}`
    : `${curveFamily || '—'} · ${tripletDisplay}`;
  const secondary = meta
    ? `${meta.shortLabel} Strip Butterfly (Implied Rate)`
    : 'STIR Strip Butterfly (Implied Rate)';

  return (
    <BuildCompactShell
      toolDisplayName="Policy Futures Butterfly"
      statusPill="STIR"
      headerIcon={<Sparkles size={12} strokeWidth={1.75} className="text-ice-300" />}
      identity={{
        primary,
        secondary,
        flag: meta?.flag,
      }}
      kpis={
        data
          ? compactKPIs(data)
          : [
              { label: 'FLY (BPS)', value: '—', unit: 'bps' },
              { label: '1D CHANGE (BPS)', value: '—', unit: 'bps' },
              { label: 'Z-SCORE (252D)', value: '—' },
            ]
      }
      chartPoints={sanitiseButterflySeries(data?.time_series ?? []).map(
        (r) => ({ date: r.date, value: r.value ?? NaN }),
      )}
      chartUnit="bps"
      referenceBands={data ? buildReferenceBands(data) : []}
      caveatText={POLICY_FUTURES_BUTTERFLY_COMPACT_CAVEAT}
      asOf={cm?.as_of_date}
      freshness="fresh"
      onExpand={onExpand}
      size={size}
      isLoading={isLoading}
      errorMessage={errorMessage ?? undefined}
      callMeta={callMeta}
    />
  );
};

export default BuildCompact;
