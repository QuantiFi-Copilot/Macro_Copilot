// ============================================================================
// BriefcasePlaceholder — honest placeholder for the V2 Briefcase surface
// ----------------------------------------------------------------------------
// Briefcase is in the agreed IA but the surface itself ships in V2 (saved
// analyses, pinned monitors, exportable research notes).  Rather than
// 404 or silently redirect, we render a calm "coming in V2" page that
// names what the surface will do, so the IA tab is never broken.
// ============================================================================

import { Link } from 'react-router-dom';
import { ArrowRight, Briefcase } from 'lucide-react';

export function BriefcasePlaceholder() {
  return (
    <div className="flex h-full w-full items-center justify-center px-6">
      <div className="max-w-[520px] text-left">
        <div className="flex h-9 w-9 items-center justify-center rounded-md border border-line-soft bg-white/[0.018] text-fg-muted">
          <Briefcase size={14} />
        </div>
        <h1 className="mt-5 text-[26px] font-light text-fg-primary">
          Briefcase ships in V2.
        </h1>
        <p className="mt-2 text-[14px] leading-[1.6] text-fg-secondary">
          Saved analyses, pinned monitors, and exportable research notes —
          the surface where your work lives across sessions. Until then, every
          answer in Ask carries its lineage hash so you can replay it once
          persistent storage lands.
        </p>
        <Link
          to="/ask"
          className="mt-8 inline-flex items-center gap-1.5 text-[13px] text-ice-300 transition-colors hover:text-ice-200 hover:underline hover:underline-offset-4"
        >
          Go to Ask
          <ArrowRight size={12} />
        </Link>
      </div>
    </div>
  );
}
