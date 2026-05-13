// ============================================================================
// ForwardPrimitiveView — placeholder for OIS forward-rate analysis.
// ----------------------------------------------------------------------------
// The /detail/forward endpoint isn't wired yet; we render an explicit
// placeholder inside the shared PrimitiveCanvasShell so the chassis stays
// consistent with the other primitive views.
// ============================================================================

import { Construction } from 'lucide-react';
import { PrimitiveCanvasShell, ToolNameChip } from './PrimitiveCanvasShell';

export function ForwardPrimitiveView() {
  return (
    <PrimitiveCanvasShell
      kicker="Primitive · OIS Forward"
      title="OIS-implied forward rates"
      subtitle="Detail endpoint not yet wired."
      meta={<ToolNameChip tool="calculate_ois_forward_rate_tool" />}
    >
      <section className="research-card flex flex-col items-center gap-3 px-6 py-10 text-center">
        <div className="flex h-10 w-10 items-center justify-center rounded-lg border border-amber-400/30 bg-amber-500/10 text-amber-300">
          <Construction size={18} />
        </div>
        <div className="max-w-md">
          <h3 className="text-[14px] font-semibold tracking-[-0.01em] text-fg-primary">
            Forward-rate workspace not yet wired
          </h3>
          <p className="mt-1.5 text-[12px] leading-relaxed text-fg-secondary">
            The OIS forward-rate detail endpoint hasn't shipped yet.  Use the
            chat copilot for forward-rate queries — it will route to the OIS
            tools directly.  This view will fill in once the backend exposes{' '}
            <span className="font-mono">/api/v1/rates/detail/forward</span>.
          </p>
        </div>
      </section>
    </PrimitiveCanvasShell>
  );
}
