// ============================================================================
// SidebarFooter — "Need inspiration?" + connection status pill.
// ----------------------------------------------------------------------------
// Stays anchored to the bottom of the sidebar.  Two pieces:
//
//   1. Inspiration card — a small card linking to /library; the same
//      affordance Mockup A's bottom-left tile shows.
//
//   2. Connection status pill — mirrors the global sidebar's existing
//      pill at the bottom of the Monitor / Rates Agent sidebars so the
//      Build sidebar reads consistently with everything else in the
//      product.  In PR A we keep this static ("Connected · TimescaleDB")
//      because hooking up the real status would mean lifting
//      RatesDataProvider into the Build shell and PR A explicitly
//      avoids changing other pages.  PR B can wire it.
// ============================================================================

import { useNavigate } from 'react-router-dom';
import { Lightbulb, Wifi } from 'lucide-react';

export function SidebarFooter() {
  const navigate = useNavigate();
  return (
    <div className="flex shrink-0 flex-col gap-2 border-t border-line-subtle px-3 pt-3 pb-3">
      <button
        type="button"
        onClick={() => navigate('/library')}
        className="group rounded-md border border-line-subtle bg-white/[0.012] px-3 py-2.5 text-left transition-colors duration-150 hover:border-ice-400/30 hover:bg-ice-500/[0.04]"
      >
        <div className="flex items-center gap-1.5">
          <Lightbulb
            size={11}
            strokeWidth={1.75}
            className="text-ice-300"
            aria-hidden
          />
          <span className="text-[10.5px] font-semibold uppercase tracking-[0.14em] text-fg-secondary">
            Need inspiration?
          </span>
        </div>
        <p className="mt-1 text-[11px] leading-[1.45] text-fg-muted">
          Browse the full Library of primitives + workflows.
        </p>
      </button>

      <div className="flex items-center gap-1.5 px-1 pt-1 text-[10px]">
        <Wifi size={10} strokeWidth={2} className="text-emerald-400" aria-hidden />
        <span className="text-fg-faint">Connected · TimescaleDB</span>
      </div>
    </div>
  );
}
