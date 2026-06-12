// ============================================================================
// surfaces/BuildCompact.tsx — Compact Build view for
// ``policy_futures_get_volume_open_interest_snapshot_tool``.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.2 + §5 every primitive claiming
// ``custom_build_surface`` ships a compact view; this is the STIR
// strip-slot volume/OI snapshot's grid card.  Mounted as a node body
// inside multi-tool DAG visualizations (e.g. *"is positioning building
// in SFR1 vs ER1 vs SFI1?"*).
//
// Cell-for-cell mirror of the bond_futures sibling
// (``../../get_futures_volume_oi_tool/surfaces/BuildCompact.tsx`` — P3):
// shell-standard density, 3 KPIs (OPEN INTEREST / ΔOI 1D / OI Z-SCORE),
// the OPEN-INTEREST sparkline (same series as the extended main chart),
// contract-count caveat footer.
// ============================================================================

import { BarChart3 } from 'lucide-react';
import {
  BuildCompactShell,
  type BuildCompactProps,
} from '@/components/shared/build';
import {
  POLICY_VOI_COMPACT_CAVEAT,
  buildReferenceBands,
  compactKPIs,
  curveMetaFor,
  sanitiseOiSeries,
  usePolicyVoiSnapshot,
} from './policyFuturesVoiShared';

const BuildCompact: React.FC<BuildCompactProps> = ({
  params,
  size = 'small',
  onExpand,
  callMeta,
}) => {
  const curveFamilyParam = params.curve_family ?? '';
  const stripPositionStr = params.strip_position ?? '';
  const stripPosition = Number(stripPositionStr);

  const { data, isLoading, errorMessage } = usePolicyVoiSnapshot({
    curveFamily: curveFamilyParam,
    stripPosition,
    lookbackDays:
      params.lookback_days != null && params.lookback_days !== ''
        ? Number(params.lookback_days)
        : undefined,
  });

  const cm = data?.current_metrics;
  const curveFamily = cm?.curve_family ?? curveFamilyParam;
  const meta = curveMetaFor(curveFamily);

  // Identity reads `SFR3 · SOFR_FUT` — the wire's strip-slot master stem
  // is the canonical disambiguator; fall back to the registry-derived
  // stem while loading.
  const identityPrimary =
    cm?.contract_code ??
    (meta && stripPositionStr
      ? `${meta.stripStemPrefix}${stripPositionStr}`
      : '—');
  const identitySecondary = curveFamily || '';

  return (
    <BuildCompactShell
      toolDisplayName="Policy Futures Volume / OI"
      statusPill="SNAPSHOT"
      headerIcon={<BarChart3 size={12} strokeWidth={1.75} className="text-ice-300" />}
      identity={{
        primary: identityPrimary,
        secondary: identitySecondary,
        flag: meta?.flag,
        subtitle: meta?.longLabel,
      }}
      kpis={
        data
          ? compactKPIs(data)
          : [
              { label: 'OPEN INTEREST', value: '—' },
              { label: 'ΔOI 1D', value: '—' },
              { label: 'OI Z-SCORE (252D)', value: '—' },
            ]
      }
      chartPoints={sanitiseOiSeries(data?.time_series ?? []).map((r) => ({
        date: r.date,
        value: r.value ?? NaN,
      }))}
      chartUnit="contracts"
      referenceBands={data ? buildReferenceBands(data) : []}
      caveatText={POLICY_VOI_COMPACT_CAVEAT}
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
