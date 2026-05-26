// ============================================================================
// OtrOfrSpreadWidget — bento card for OTR-OFR cash-bond spread
// ----------------------------------------------------------------------------
// Stage 6 — the OTR-OFR spread is the desk-standard cash-bond
// rich-cheap / liquidity-premium signal: spread between on-the-run
// (current benchmark) and first-off-the-run (previous benchmark) at
// the same tenor, in basis points.  Persistent positive spreads
// indicate the OTR commanding a liquidity premium; reversals are
// usually issuance / supply-demand driven.
//
// Stage 6 ships the parameterised Monitor card shell (curve + tenor
// pickers via paramFields on the module spec).  Today the aggregated
// rates-page payload doesn't carry OTR/OFR series; the live chart
// + per-tenor heatmap come in a follow-up PR once the typed-detail
// endpoint is wired.
// ============================================================================

import { useOptionalRatesDataContext } from '@/components/monitor/RatesDataProvider';
import {
  WidgetBody,
  WidgetHeader,
  WidgetProvenance,
} from '@/components/monitor/WidgetCard';
import { WidgetEmpty, WidgetLoading } from '@/components/monitor/widgets/shared';

type Props = {
  // Catalog modal hands the user's per-widget params here.  Stage 6
  // accepts curve_family + tenor (declared on the module spec); the
  // live wiring will read them in a follow-up PR.
  params?: Record<string, unknown>;
};

export function OtrOfrSpreadWidget({ params }: Props) {
  const ctx = useOptionalRatesDataContext();
  const curve = (params?.curve_family as string | undefined) ?? 'UST';
  const tenor = (params?.tenor as string | undefined) ?? '10Y';
  if (!ctx) {
    return (
      <>
        <WidgetHeader kicker="OTR-OFR Spread" title={`${curve} ${tenor}`} />
        <WidgetBody>
          <WidgetEmpty message="No rates data context available." />
        </WidgetBody>
        <WidgetProvenance toolName="calculate_otr_ofr_spread_tool" />
      </>
    );
  }
  if (ctx.isLoading) {
    return <WidgetLoading />;
  }
  return (
    <>
      <WidgetHeader kicker="OTR-OFR Spread" title={`${curve} ${tenor}`} />
      <WidgetBody>
        <div className="px-4 py-6 text-[11px] text-fg-muted">
          <p>
            On-the-run vs first-off-the-run · spread in bps · 252-day
            rolling z.
          </p>
          <p className="mt-1 text-fg-faint">
            Stage 6 module-first reference.  Live chart + per-tenor heatmap
            land in a follow-up PR.
          </p>
        </div>
      </WidgetBody>
      <WidgetProvenance toolName="calculate_otr_ofr_spread_tool" />
    </>
  );
}
