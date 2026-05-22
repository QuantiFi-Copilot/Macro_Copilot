// ============================================================================
// WidgetGrid — bento layout for the widget engine
// ----------------------------------------------------------------------------
// 12-column responsive grid.  Each WidgetCard's class list declares
// its own col-span based on size, so the grid itself just lays them
// out left-to-right with consistent gutters.  When in edit mode, the
// grid appends a "+ Add widget" placeholder tile at the end.
//
// Empty layouts: when the user has cleared everything, the grid shows
// a centered onboarding state with a single "+ Add your first widget"
// CTA — no widgets, no clutter.
// ============================================================================

import { Plus } from 'lucide-react';
import type { LayoutState } from './registry';
import { WidgetRenderer } from './WidgetRenderer';

type Props = {
  layout: LayoutState;
  isEditing: boolean;
  onRemove: (instanceId: string) => void;
  onConfigure: (instanceId: string) => void;
  onAddWidget: () => void;
};

export function WidgetGrid({
  layout,
  isEditing,
  onRemove,
  onConfigure,
  onAddWidget,
}: Props) {
  if (layout.widgets.length === 0) {
    return <EmptyGrid onAddWidget={onAddWidget} />;
  }

  return (
    <div className="grid grid-cols-12 gap-4 lg:gap-5">
      {layout.widgets.map((instance) => (
        <WidgetRenderer
          key={instance.id}
          instance={instance}
          isEditing={isEditing}
          onRemove={onRemove}
          onConfigure={onConfigure}
        />
      ))}
      {isEditing && <AddWidgetTile onClick={onAddWidget} />}
    </div>
  );
}

// ----------------------------------------------------------------------------

function AddWidgetTile({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="group col-span-12 lg:col-span-6 flex min-h-[300px] flex-col items-center justify-center gap-2 rounded-[14px] border border-dashed border-line-soft bg-white/[0.005] transition-all duration-200 ease-sleek hover:border-ice-400/40 hover:bg-ice-400/[0.025]"
    >
      <span className="flex h-9 w-9 items-center justify-center rounded-full border border-line-soft bg-ink-900/40 text-fg-muted transition-colors group-hover:border-ice-400/40 group-hover:text-ice-200">
        <Plus size={14} strokeWidth={2} />
      </span>
      <span className="text-[12.5px] font-medium tracking-[-0.005em] text-fg-secondary transition-colors group-hover:text-fg-primary">
        Add widget
      </span>
      <span className="font-mono text-[10px] tracking-[0.04em] text-fg-faint">
        choose from the catalog
      </span>
    </button>
  );
}

function EmptyGrid({ onAddWidget }: { onAddWidget: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center gap-4 px-6 py-20 text-center">
      <span className="font-serif-display text-[36px] font-light italic leading-tight text-fg-primary">
        nothing here yet.
      </span>
      <p className="max-w-[420px] text-[13px] leading-[1.55] text-fg-secondary">
        Add a widget to start tracking the markets you care about. Each widget
        sources its data from a deterministic tool — every number carries its
        lineage.
      </p>
      <button
        type="button"
        onClick={onAddWidget}
        className="composer-send-active mt-2 inline-flex items-center gap-1.5 rounded-md px-3 py-2 text-[12.5px] font-medium"
      >
        <Plus size={12} strokeWidth={2.25} />
        <span>Add your first widget</span>
      </button>
    </div>
  );
}
