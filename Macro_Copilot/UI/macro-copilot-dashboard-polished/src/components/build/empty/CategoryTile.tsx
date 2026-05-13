// ============================================================================
// CategoryTile — single tile in the Build empty-state grid.
// ----------------------------------------------------------------------------
// Same visual register as Monitor's WidgetCatalog tile + the existing
// `QuickLaunchTile` (icon-in-rounded-box + label + 1-line description).
// Differences from QuickLaunchTile:
//
//   - Larger footprint, two-line description.
//   - Optional "soon" badge that dims the tile and disables the click.
//   - Hovering shows a faint "Drop into composer" caption so the user
//     understands the click action.
// ============================================================================

import {
  Activity,
  Code,
  Filter,
  GitCompare,
  PieChart,
  TrendingUp,
  type LucideIcon,
} from 'lucide-react';
import { cn } from '@/utils/cn';
import type { BuildEmptyCategory } from '../lib/buildTypes';

const ICONS: Record<BuildEmptyCategory['iconName'], LucideIcon> = {
  TrendingUp,
  Activity,
  PieChart,
  Filter,
  GitCompare,
  Code,
};

type Props = {
  category: BuildEmptyCategory;
  onSelect: (category: BuildEmptyCategory) => void;
};

export function CategoryTile({ category, onSelect }: Props) {
  const Icon = ICONS[category.iconName];
  const isSoon = !!category.soon;

  return (
    <button
      type="button"
      disabled={isSoon}
      onClick={() => onSelect(category)}
      className={cn(
        'group relative flex h-full flex-col items-start gap-3 rounded-[12px] border border-line-soft bg-white/[0.012] px-4 py-4 text-left transition-all duration-200 ease-sleek',
        isSoon
          ? 'cursor-not-allowed opacity-60'
          : 'hover:border-ice-400/35 hover:bg-ice-500/[0.04] hover:-translate-y-px',
      )}
    >
      <span
        className={cn(
          'flex h-8 w-8 items-center justify-center rounded-md border border-line-subtle bg-white/[0.02] text-ice-300 transition-colors',
          !isSoon &&
            'group-hover:border-ice-400/35 group-hover:bg-ice-500/15 group-hover:text-ice-200',
        )}
      >
        <Icon size={15} strokeWidth={1.75} aria-hidden />
      </span>

      <div className="flex min-w-0 flex-col gap-1">
        <div className="flex items-center gap-2">
          <h3 className="text-[13px] font-semibold tracking-[-0.008em] text-fg-primary">
            {category.label}
          </h3>
          {isSoon && (
            <span className="rounded-sm border border-line-soft bg-white/[0.02] px-1.5 py-0.5 text-[8.5px] font-semibold uppercase tracking-[0.18em] text-fg-muted">
              soon
            </span>
          )}
        </div>
        <p className="line-clamp-2 text-[11.5px] leading-[1.5] text-fg-secondary">
          {category.description}
        </p>
        {isSoon && category.soonReason && (
          <p className="mt-1 text-[10.5px] leading-[1.45] text-fg-faint">
            {category.soonReason}
          </p>
        )}
      </div>
    </button>
  );
}
