// ============================================================================
// NfpSurpriseWidget — bento card for the latest NFP release
// ----------------------------------------------------------------------------
// Stage 6 — the second new-feature primitive shipped through the
// module-first dispatch architecture (the first was CPI Surprise in
// Stage 5).  Same pattern: a US-only monthly release where the desk
// reads the surprise (actual − consensus, in thousands of jobs) +
// the rolling-z signal at a glance every NFP Friday.
//
// Stage 6 ships the widget shell + module wiring; the live chart
// (per-release time series + sparkline) lands in a follow-up PR
// once the typed-detail endpoint payload is wired up.  The catalog-
// add path is already testable: the user can add this widget from
// the Monitor catalog modal and see the honest "shipping next"
// snapshot.
// ============================================================================

import { useOptionalRatesDataContext } from '@/components/monitor/RatesDataProvider';
import {
  WidgetBody,
  WidgetHeader,
  WidgetProvenance,
} from '@/components/monitor/WidgetCard';
import { WidgetEmpty, WidgetLoading } from '@/components/monitor/widgets/shared';

export function NfpSurpriseWidget() {
  const ctx = useOptionalRatesDataContext();
  if (!ctx) {
    return (
      <>
        <WidgetHeader kicker="NFP Surprise" title="Latest release" />
        <WidgetBody>
          <WidgetEmpty message="No rates data context available." />
        </WidgetBody>
        <WidgetProvenance toolName="calculate_nfp_surprise_tool" />
      </>
    );
  }
  if (ctx.isLoading) {
    return <WidgetLoading />;
  }
  return (
    <>
      <WidgetHeader kicker="NFP Surprise" title="Latest release" />
      <WidgetBody>
        <div className="px-4 py-6 text-[11px] text-fg-muted">
          <p>
            US nonfarm payrolls · actual vs consensus · in thousands ·
            rolling-z signal.
          </p>
          <p className="mt-1 text-fg-faint">
            Stage 6 module-first reference.  Live chart wiring lands
            in a follow-up PR.
          </p>
        </div>
      </WidgetBody>
      <WidgetProvenance toolName="calculate_nfp_surprise_tool" />
    </>
  );
}
