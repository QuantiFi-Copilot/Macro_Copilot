// ============================================================================
// surfaces/BuildCompact.tsx — Compact Build view for the bond_futures
// variant of ``get_futures_volume_oi_tool``.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.2 + §5 every new primitive ships a compact
// view; this is the bond-futures rolling-generic volume/OI snapshot's.
// Mounted as a node body inside multi-tool query DAG visualizations (e.g.
// *"is positioning building in TY1 vs RX1 vs JB1?"*).
//
// Per the Option-(c) precedent (Batch 1 d8e8233 / breakeven_butterfly
// fdac7d2) this file uses the SHELL-STANDARD density — 3 KPIs + single
// identity line: OPEN INTEREST (headline) / ΔOI 1D / OI Z-SCORE (252D),
// the desk's "where is positioning / which way did it move / is it
// stretched?" read.  Volume context (level, vs-22d-mean, 22d max) lives in
// the extended view's 10-cell strip (one tap on the expand affordance).
// The sparkline is the OPEN-INTEREST tape — the same series the extended
// view's main chart shows, per §2.2.
// ============================================================================

import { BarChart3 } from 'lucide-react';
import {
  BuildCompactShell,
  type BuildCompactProps,
} from '@/components/shared/build';
import {
  FUTURES_VOLUME_OI_COMPACT_CAVEAT,
  buildReferenceBands,
  compactKPIs,
  contractMetaFor,
  sanitiseOiSeries,
  useFuturesVolumeOi,
} from './futuresVolumeOiShared';

const BuildCompact: React.FC<BuildCompactProps> = ({
  params,
  size = 'small',
  onExpand,
  callMeta,
}) => {
  const curveFamilyParam = params.curve_family ?? '';
  const contractCodeParam = params.contract_code ?? '';

  const { data, isLoading, errorMessage } = useFuturesVolumeOi({
    curveFamily: curveFamilyParam,
    contractCode: contractCodeParam,
    lookbackDays:
      params.lookback_days != null && params.lookback_days !== ''
        ? Number(params.lookback_days)
        : undefined,
  });

  const cm = data?.current_metrics;
  const contractCode = cm?.contract_code ?? contractCodeParam;
  const curveFamily = cm?.curve_family ?? curveFamilyParam;
  const meta = contractMetaFor(contractCode);

  // Identity reads `TY1 · UST_FUT` — the (curve_family, contract_code)
  // pair IS the tool's input key per TD#11; the shell inserts the `·`.
  const identityPrimary = contractCode || '—';
  const identitySecondary = curveFamily || (meta?.curveFamily ?? '');

  return (
    <BuildCompactShell
      toolDisplayName="Bond Futures Volume / OI"
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
      caveatText={FUTURES_VOLUME_OI_COMPACT_CAVEAT}
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
