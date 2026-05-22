// ============================================================================
// Shared widget primitives — loading / error / empty state
// ----------------------------------------------------------------------------
// Tiny visual components every widget reuses so loading and error
// reads stay consistent across the catalog.  All match the Ask page's
// 2026 register: hushed, monospace, no spinner-heavy chrome.
// ============================================================================

import { AlertCircle, Loader2 } from 'lucide-react';

export function WidgetLoading({ label }: { label?: string }) {
  return (
    <div className="flex flex-1 items-center justify-center px-5 py-10">
      <div className="flex items-center gap-2 text-fg-muted">
        <Loader2 size={12} className="animate-spin text-lineage-300" />
        <span className="font-mono text-[11px] tracking-[0.02em]">
          {label ?? 'Loading…'}
        </span>
      </div>
    </div>
  );
}

export function WidgetError({ message }: { message: string }) {
  return (
    <div className="flex flex-1 items-start gap-2.5 px-5 py-5">
      <AlertCircle size={13} className="mt-0.5 shrink-0 text-coral-400" />
      <div className="min-w-0">
        <p className="text-[10px] font-medium uppercase tracking-[0.16em] text-coral-300">
          DATA ERROR
        </p>
        <p className="mt-1 text-[11.5px] leading-[1.55] text-coral-300/85">
          {message}
        </p>
      </div>
    </div>
  );
}

export function WidgetEmpty({ message }: { message: string }) {
  return (
    <div className="flex flex-1 items-center justify-center px-5 py-8">
      <p className="text-[11.5px] text-fg-muted">{message}</p>
    </div>
  );
}
