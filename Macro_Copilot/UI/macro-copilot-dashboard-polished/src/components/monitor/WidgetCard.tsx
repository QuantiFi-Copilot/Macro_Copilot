// ============================================================================
// WidgetCard — chrome for every widget on Monitor / Rates Agent surfaces
// ----------------------------------------------------------------------------
// Reuses the `.research-card` surface treatment from the Ask page (same
// layered gradient + soft shadow stack + gradient top-rule), keyed to a
// `category` prop that drives the rail color (data → ice, analysis →
// violet, anomaly → amber).  This keeps the visual register consistent
// between Ask and Monitor.
//
// Two states:
//   - VIEW MODE: normal card, content at full opacity, no chrome
//   - EDIT MODE: dashed outline (subtle), corner remove (×) button
//     visible on hover, click on the body is forwarded to a "configure"
//     callback so the user can tap a widget to reconfigure it.
//
// V1 doesn't ship drag-and-drop — only add / remove / reconfigure.
// Drag affordance icons are intentionally NOT shown so users don't try
// an interaction that would no-op.
// ============================================================================

import { type ReactNode } from 'react';
import { Settings2, X } from 'lucide-react';
import { cn } from '@/utils/cn';
import type { WidgetCategory, WidgetSize } from './registry';

type Props = {
  category: WidgetCategory;
  size: WidgetSize;
  isEditing: boolean;
  /** Called when the user clicks the × button in edit mode. */
  onRemove?: () => void;
  /** Called when the user clicks the cog button in edit mode (only
   *  shown for parameterized widgets). */
  onConfigure?: () => void;
  /** True if this widget supports configuration (parameterized).
   *  When true and `isEditing`, a small cog button appears. */
  isConfigurable?: boolean;
  children: ReactNode;
};

export function WidgetCard({
  category,
  size,
  isEditing,
  onRemove,
  onConfigure,
  isConfigurable,
  children,
}: Props) {
  return (
    <article
      className={cn(
        // Reuses the Ask surface — same shadow stack, same backdrop
        // blur, same fade-in.
        'research-card relative flex min-h-0 flex-col overflow-hidden',
        widgetSizeClasses(size),
        isEditing &&
          'ring-1 ring-dashed ring-line-strong ring-offset-2 ring-offset-transparent',
      )}
      style={{ ['--rail-color' as string]: railColorFor(category) }}
    >
      {/* Gradient top-rule keyed to category (matches Ask) */}
      <span aria-hidden className="research-card-rail" />

      {/* Edit-mode action cluster: cog (configure, parameterized only)
          + × (remove).  Hidden in view mode so the card is visually
          calm. */}
      {isEditing && (
        <div className="absolute right-3 top-3 z-10 flex items-center gap-1">
          {isConfigurable && onConfigure && (
            <button
              type="button"
              onClick={onConfigure}
              aria-label="Configure widget"
              title="Configure"
              className="flex h-6 w-6 items-center justify-center rounded-md border border-line-soft bg-ink-900/70 text-fg-secondary backdrop-blur-sm transition-colors hover:border-ice-400/40 hover:text-ice-200"
            >
              <Settings2 size={11} strokeWidth={2} />
            </button>
          )}
          {onRemove && (
            <button
              type="button"
              onClick={onRemove}
              aria-label="Remove widget"
              title="Remove"
              className="flex h-6 w-6 items-center justify-center rounded-md border border-line-soft bg-ink-900/70 text-fg-secondary backdrop-blur-sm transition-colors hover:border-coral-400/40 hover:text-coral-300"
            >
              <X size={11} strokeWidth={2.25} />
            </button>
          )}
        </div>
      )}

      {/* Content slot — widgets render their own header / body / footer. */}
      <div className="flex min-h-0 flex-1 flex-col">{children}</div>
    </article>
  );
}

// ----------------------------------------------------------------------------
// Shared internal layout helpers — every widget uses these so the
// header / body / footer rhythm stays consistent across the catalog.

export function WidgetHeader({
  kicker,
  title,
  meta,
}: {
  kicker?: string;
  title?: string;
  meta?: ReactNode;
}) {
  return (
    <div className="flex shrink-0 items-center justify-between gap-3 px-5 pt-4 pb-3">
      <div className="flex min-w-0 flex-col gap-0.5">
        {kicker && (
          <span className="text-[10px] font-medium uppercase tracking-[0.16em] text-fg-muted">
            {kicker}
          </span>
        )}
        {title && (
          <h3 className="truncate text-[13.5px] font-medium tracking-[-0.012em] text-fg-primary">
            {title}
          </h3>
        )}
      </div>
      {meta && (
        <div className="flex shrink-0 items-center gap-1.5">{meta}</div>
      )}
    </div>
  );
}

export function WidgetBody({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn('flex min-h-0 flex-1 flex-col', className)}>
      {children}
    </div>
  );
}

/** Provenance footer — visible at the bottom of every widget.  Mirrors
 *  the ProvenanceRow pattern from research cards so the auditability
 *  read is consistent between Ask answers and Monitor widgets. */
export function WidgetProvenance({
  toolName,
  asOfDate,
  durationMs,
  lineageHash,
}: {
  toolName: string;
  asOfDate?: string | null;
  durationMs?: number | null;
  lineageHash?: string;
}) {
  const hash = lineageHash ?? stableShortHash(toolName + (asOfDate ?? ''));
  return (
    <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1 border-t border-line-subtle px-5 py-2">
      <span className="lineage-chip">
        <span className="opacity-70">lineage</span>
        <span>{hash}</span>
      </span>
      <span className="font-mono text-[10px] text-fg-muted">·</span>
      <span className="font-mono truncate text-[10px] text-fg-muted">
        {toolName}
      </span>
      {asOfDate && (
        <>
          <span className="font-mono text-[10px] text-fg-muted">·</span>
          <span className="font-mono text-[10px] text-fg-faint">{asOfDate}</span>
        </>
      )}
      {durationMs != null && (
        <>
          <span className="font-mono text-[10px] text-fg-muted">·</span>
          <span className="font-mono text-[10px] text-fg-faint">
            {durationMs < 1000
              ? `${durationMs}ms`
              : `${(durationMs / 1000).toFixed(1)}s`}
          </span>
        </>
      )}
    </div>
  );
}

// ----------------------------------------------------------------------------
// Utilities

function railColorFor(category: WidgetCategory): string {
  switch (category) {
    case 'data':
      return 'rgba(122, 162, 255, 0.55)';     // ice
    case 'analysis':
      return 'rgba(155, 140, 255, 0.45)';     // violet
    case 'anomaly':
      return 'rgba(243, 183, 85, 0.55)';      // amber
  }
}

function widgetSizeClasses(size: WidgetSize): string {
  // 12-column grid; row spans drive vertical rhythm.  Heights are
  // assigned via min-h on the card so content can grow if needed but
  // a sparsely-loaded widget doesn't collapse.
  switch (size) {
    case 'small':
      return 'col-span-12 sm:col-span-6 lg:col-span-3 min-h-[160px]';
    case 'medium':
      return 'col-span-12 lg:col-span-6 min-h-[300px]';
    case 'wide':
      return 'col-span-12 min-h-[260px]';
    case 'tall':
      return 'col-span-12 lg:col-span-6 min-h-[480px]';
  }
}

function stableShortHash(seed: string): string {
  let h = 5381;
  for (let i = 0; i < seed.length; i++) {
    h = ((h << 5) + h + seed.charCodeAt(i)) | 0;
  }
  return (h >>> 0).toString(16).padStart(8, '0').slice(-6);
}
