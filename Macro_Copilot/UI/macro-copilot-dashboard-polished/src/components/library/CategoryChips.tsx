// ============================================================================
// CategoryChips — horizontal chip row for every category in CATEGORY_LABELS
// ----------------------------------------------------------------------------
// Each chip carries a small color dot keyed to its semantic family
// (data → ice, analysis → violet, anomaly → amber).  Chips show
// tool counts from the manifest's category_counts; categories with
// zero tools (e.g. when a sub-agent filter is active) hide.
//
// Render order — Stage 4f
// -----------------------
// The pre-Stage-4f hard-coded list dropped every category Stage 1 added
// to the backend manifests (panels, scanners, spreads, economic_release_
// surprises, meeting_pricing, aggregates, panel_assembly).  The row is
// now derived from ``CATEGORY_LABELS`` so the chip row stays in lock-
// step with the canonical list: pinned-priority categories appear first
// (in the original visual-brief order), then every remaining label key
// in alphabetical order.  Adding a new category = one new entry to
// ``CATEGORY_LABELS``; no edit to this file required.
// ============================================================================

import { cn } from '@/utils/cn';
import { CATEGORY_LABELS, CATEGORY_TONE } from '@/types/library';

type Props = {
  active: string; // 'all' or a category id
  /** Per-category counts after the current sub-agent filter is
   *  applied.  Categories whose count is 0 are not rendered. */
  counts: Record<string, number>;
  totalCount: number;
  onSelect: (category: string) => void;
};

// Pinned-priority categories — these render first in the order shown,
// matching the original visual brief.  Any other category in
// ``CATEGORY_LABELS`` renders alphabetically after this prefix.
const PINNED_CATEGORIES = [
  'snapshots',
  'curve_shape',
  'cross_market_rv',
  'forwards_classify',
  'screening',
  'rolling_analytics',
  'model_fits',
];

const PINNED_SET = new Set(PINNED_CATEGORIES);

/** Final render order: pinned-priority categories first, then every
 *  remaining ``CATEGORY_LABELS`` key alphabetically.  Computed once at
 *  module load. */
const CATEGORY_ORDER: ReadonlyArray<string> = [
  ...PINNED_CATEGORIES.filter((c) => c in CATEGORY_LABELS),
  ...Object.keys(CATEGORY_LABELS)
    .filter((c) => !PINNED_SET.has(c))
    .sort(),
];

export function CategoryChips({ active, counts, totalCount, onSelect }: Props) {
  return (
    <div className="flex flex-wrap items-center gap-1.5 px-8 py-3">
      <Chip
        label="All"
        count={totalCount}
        tone={null}
        isActive={active === 'all'}
        onClick={() => onSelect('all')}
      />
      {CATEGORY_ORDER.filter((c) => (counts[c] ?? 0) > 0).map((c) => (
        <Chip
          key={c}
          label={CATEGORY_LABELS[c] ?? c}
          count={counts[c]}
          tone={CATEGORY_TONE[c]}
          isActive={active === c}
          onClick={() => onSelect(c)}
        />
      ))}
    </div>
  );
}

function Chip({
  label,
  count,
  tone,
  isActive,
  onClick,
}: {
  label: string;
  count: number;
  tone: 'data' | 'analysis' | 'anomaly' | null;
  isActive: boolean;
  onClick: () => void;
}) {
  const dotClass =
    tone === 'data'
      ? 'bg-ice-300'
      : tone === 'analysis'
        ? 'bg-lineage-300'
        : tone === 'anomaly'
          ? 'bg-amber-300'
          : '';
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'group flex h-7 items-center gap-1.5 rounded-full px-2.5 text-[11.5px] tracking-[-0.005em] transition-all duration-200 ease-sleek',
        isActive
          ? 'bg-[linear-gradient(180deg,rgba(255,255,255,0.06),rgba(255,255,255,0.02))] text-fg-primary shadow-[inset_0_1px_0_rgba(255,255,255,0.06),inset_0_0_0_1px_rgba(148,163,184,0.20)]'
          : 'text-fg-secondary ring-1 ring-line-soft hover:bg-white/[0.022] hover:text-fg-primary',
      )}
    >
      {dotClass && (
        <span
          aria-hidden
          className={cn(
            'h-1.5 w-1.5 rounded-full',
            dotClass,
            isActive && 'shadow-[0_0_4px_currentColor]',
          )}
        />
      )}
      <span>{label}</span>
      <span className="font-mono text-[9.5px] text-fg-faint">· {count}</span>
    </button>
  );
}
