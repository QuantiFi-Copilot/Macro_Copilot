// ============================================================================
// CategoryTile — single tile in the Build empty-state grid.
// ----------------------------------------------------------------------------
// Shares the ``research-card`` chassis with Monitor's WidgetCard and
// Library's ToolCard so the empty state reads in the same register as
// the rest of the polished surfaces.  Each tile carries a category-
// keyed rail color (spreads → ice, decompose → violet, etc.) for
// faint visual variation that mirrors how Monitor groups widgets.
//
// Interaction:
//   - Click an active tile → ``onSelect(category)`` is fired and the
//     parent seeds the composer with the tile's ``promptSeed``.
//   - "Soon" tiles render dimmed with a small badge + the soonReason
//     caption.  They never fire ``onSelect``.
//   - Hover lifts the card a hair and brightens the arrow indicator;
//     the underlying ``research-card`` already handles the shadow lift.
// ============================================================================

import {
  Activity,
  ArrowUpRight,
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

// Per-category accent tone.  Drives the ``--rail-color`` on the
// research-card top-rule + the icon badge tint.  Kept here (not in
// stageCategory.ts) because empty-state tiles are an editorial
// surface — they exist independently of the substrate's stage
// classification and shouldn't import the workspace types.
type Accent = 'ice' | 'violet' | 'mint' | 'amber';

const ACCENT_BY_ID: Record<string, Accent> = {
  run_a_backtest: 'amber',
  analyze_a_spread: 'ice',
  decompose_a_move: 'violet',
  screen_a_universe: 'mint',
  compare_regimes: 'violet',
  build_custom_dag: 'ice',
};

const RAIL_COLOR: Record<Accent, string> = {
  ice: 'rgba(122, 162, 255, 0.55)',
  violet: 'rgba(155, 140, 255, 0.50)',
  mint: 'rgba(63, 214, 154, 0.50)',
  amber: 'rgba(243, 183, 85, 0.50)',
};

const ICON_BADGE: Record<
  Accent,
  { border: string; bg: string; text: string; hoverBg: string }
> = {
  ice: {
    border: 'border-ice-400/35',
    bg: 'bg-ice-500/10',
    text: 'text-ice-200',
    hoverBg: 'group-hover:bg-ice-500/20',
  },
  violet: {
    border: 'border-violet-400/35',
    bg: 'bg-violet-500/10',
    text: 'text-violet-200',
    hoverBg: 'group-hover:bg-violet-500/20',
  },
  mint: {
    border: 'border-mint-400/35',
    bg: 'bg-mint-500/10',
    text: 'text-mint-300',
    hoverBg: 'group-hover:bg-mint-500/20',
  },
  amber: {
    border: 'border-amber-400/35',
    bg: 'bg-amber-500/10',
    text: 'text-amber-300',
    hoverBg: 'group-hover:bg-amber-500/20',
  },
};

type Props = {
  category: BuildEmptyCategory;
  onSelect: (category: BuildEmptyCategory) => void;
};

export function CategoryTile({ category, onSelect }: Props) {
  const Icon = ICONS[category.iconName];
  const isSoon = !!category.soon;
  const accent = ACCENT_BY_ID[category.id] ?? 'ice';
  const railColor = RAIL_COLOR[accent];
  const badge = ICON_BADGE[accent];

  return (
    <button
      type="button"
      disabled={isSoon}
      onClick={() => onSelect(category)}
      style={{ ['--rail-color' as string]: railColor }}
      className={cn(
        'research-card group relative flex h-full min-h-[150px] flex-col gap-3 overflow-hidden px-4 pt-4 pb-4 text-left transition-transform duration-200 ease-sleek',
        isSoon
          ? 'cursor-not-allowed opacity-55'
          : 'hover:-translate-y-px focus-visible:-translate-y-px focus-visible:outline-none',
      )}
    >
      <span aria-hidden className="research-card-rail" />

      <div className="flex items-start justify-between gap-3">
        <span
          className={cn(
            'flex h-9 w-9 items-center justify-center rounded-[10px] border transition-colors',
            badge.border,
            badge.bg,
            badge.text,
            !isSoon && badge.hoverBg,
          )}
        >
          <Icon size={16} strokeWidth={1.75} aria-hidden />
        </span>

        {isSoon ? (
          <span className="rounded-sm border border-line-soft bg-white/[0.02] px-1.5 py-0.5 font-mono text-[8.5px] font-semibold uppercase tracking-[0.18em] text-fg-muted">
            soon
          </span>
        ) : (
          <ArrowUpRight
            size={14}
            strokeWidth={1.75}
            aria-hidden
            className="mt-1 text-fg-faint transition-colors group-hover:text-ice-200"
          />
        )}
      </div>

      <div className="flex min-w-0 flex-col gap-1">
        <h3 className="text-[13px] font-semibold tracking-[-0.008em] text-fg-primary">
          {category.label}
        </h3>
        <p className="line-clamp-2 text-[11.5px] leading-[1.5] text-fg-secondary">
          {category.description}
        </p>
        {isSoon && category.soonReason && (
          <p className="mt-1 line-clamp-2 text-[10.5px] leading-[1.45] text-fg-faint">
            {category.soonReason}
          </p>
        )}
      </div>
    </button>
  );
}
