// ============================================================================
// surfaces/BuildCompact.tsx — Compact Build view for the bond_futures
// variant of ``get_futures_price_level_tool``.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.2 + §5 every new primitive ships a compact
// view; this is the bond-futures rolling-generic price level's.  Mounted
// as a node body inside multi-tool query DAG visualizations (e.g.
// *"compare TY1 vs RX1 vs JB1 fronts"*).
//
// Design reference: ./mockups/Compact.png.
//
// Per the Option-(c) precedent (Batch 1 d8e8233 / breakeven_butterfly
// fdac7d2) this file uses the SHELL-STANDARD density — 3 KPIs + single
// identity line.  The mockup shows a denser layout (3-cell headline +
// secondary 6-cell row: 5D / 1M / pctl / high / low / observations);
// promoting that into the compact view would require extending
// BuildCompactShell with a ``secondaryKpis`` slot (touching 10+ shipped
// compact views).  See THESIS.md → Mockup conformance for the deferral
// note.  The full 9-cell KPI strip remains available in the extended
// view (one tap on the expand affordance).
// ============================================================================

import { Activity } from 'lucide-react';
import {
  BuildCompactShell,
  type BuildCompactProps,
} from '@/components/shared/build';
import {
  BOND_FUTURES_PRICE_COMPACT_CAVEAT,
  buildReferenceBands,
  compactKPIs,
  contractMetaFor,
  sanitisePriceSeries,
  useBondFuturesPrice,
  quoteUnitsLabel,
} from './bondFuturesPriceShared';

const BuildCompact: React.FC<BuildCompactProps> = ({
  params,
  size = 'small',
  onExpand,
  callMeta,
}) => {
  const curveFamilyParam = params.curve_family ?? '';
  const contractCodeParam = params.contract_code ?? '';

  const { data, isLoading, errorMessage } = useBondFuturesPrice({
    curveFamily: curveFamilyParam,
    contractCode: contractCodeParam,
    lookbackDays:
      params.lookback_days != null && params.lookback_days !== ''
        ? Number(params.lookback_days)
        : undefined,
    fieldName: params.field_name || undefined,
  });

  const cm = data?.current_metrics;
  const contractCode = cm?.contract_code ?? contractCodeParam;
  const meta = contractMetaFor(contractCode);
  const wireUnits = quoteUnitsLabel(cm?.quote_units ?? null, meta);

  const identityPrimary = contractCode || '—';
  // Compact identity secondary mirrors the mockup: `TY1 · 10Y UST`.
  const identitySecondary = meta
    ? `${meta.tenor} ${meta.shortLabel}`
    : (cm?.tenor ?? '');

  return (
    <BuildCompactShell
      toolDisplayName="Bond Futures Price Level"
      statusPill="SNAPSHOT"
      headerIcon={<Activity size={12} strokeWidth={1.75} className="text-ice-300" />}
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
              { label: `PRICE (${wireUnits.toUpperCase()})`, value: '—' },
              { label: `1D CHANGE (${wireUnits.toUpperCase()})`, value: '—' },
              { label: 'Z-SCORE (252D)', value: '—' },
            ]
      }
      chartPoints={sanitisePriceSeries(data?.time_series ?? []).map((r) => ({
        date: r.date,
        value: r.value ?? NaN,
      }))}
      chartUnit={wireUnits}
      referenceBands={data ? buildReferenceBands(data) : []}
      caveatText={BOND_FUTURES_PRICE_COMPACT_CAVEAT}
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
