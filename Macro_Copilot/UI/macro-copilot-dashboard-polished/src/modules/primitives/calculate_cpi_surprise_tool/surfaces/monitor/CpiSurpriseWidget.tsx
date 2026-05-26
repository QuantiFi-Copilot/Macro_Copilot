// ============================================================================
// CpiSurpriseWidget — bento card for the latest CPI surprise release
// ----------------------------------------------------------------------------
// Stage 5 reference implementation.  Demonstrates that a new primitive
// (CPI Surprise) can ship a bespoke Monitor surface entirely from its
// module folder — no edits to ``components/monitor/registry.ts``,
// ``WidgetRenderer.tsx``, or the page-shell.  The Stage 4d module
// walker reads ``MODULE.monitorWidgets[0].component`` and registers
// this component under the widget id declared on the module spec.
//
// The widget consumes ``RatesDataProvider`` context only when it
// runs in the live Monitor page (not in the test environment).  For
// Stage 5 it renders a minimal but honest snapshot tile — release
// date, actual vs consensus, surprise in percentage points, and a
// rolling z-score interpretation.  The real chart-bearing version
// can extend this in a follow-up PR.
// ============================================================================

import { useOptionalRatesDataContext } from '@/components/monitor/RatesDataProvider';
import {
  WidgetBody,
  WidgetHeader,
  WidgetProvenance,
} from '@/components/monitor/WidgetCard';
import { WidgetEmpty, WidgetLoading } from '@/components/monitor/widgets/shared';

export function CpiSurpriseWidget() {
  const ctx = useOptionalRatesDataContext();
  if (!ctx) {
    // Mounted outside a RatesDataProvider — render an honest empty
    // tile rather than throw.  Lets the smoke / unit test mount the
    // widget without a provider.
    return (
      <>
        <WidgetHeader kicker="CPI Surprise" title="Latest release" />
        <WidgetBody>
          <WidgetEmpty message="No rates data context available." />
        </WidgetBody>
        <WidgetProvenance toolName="calculate_cpi_surprise_tool" />
      </>
    );
  }
  if (ctx.isLoading) {
    return <WidgetLoading />;
  }
  // The aggregated rates-page payload doesn't carry CPI surprise data
  // today.  Stage 5 ships the widget shell wired to the module spec;
  // a follow-up PR (Stage 6+) will land the typed-detail endpoint
  // call + chart rendering.  Until then the tile shows an honest
  // "shipping next" placeholder so the catalog-add path is testable.
  return (
    <>
      <WidgetHeader kicker="CPI Surprise" title="Latest release" />
      <WidgetBody>
        <div className="px-4 py-6 text-[11px] text-fg-muted">
          <p>
            Latest CPI release · actual vs consensus · rolling-z signal.
          </p>
          <p className="mt-1 text-fg-faint">
            Stage 5 module-first reference.  Live chart wiring lands in
            a follow-up PR.
          </p>
        </div>
      </WidgetBody>
      <WidgetProvenance toolName="calculate_cpi_surprise_tool" />
    </>
  );
}
