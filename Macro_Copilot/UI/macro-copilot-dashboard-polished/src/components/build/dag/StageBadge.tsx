// ============================================================================
// StageBadge — numbered badge in the top-left of every stage card.
// ----------------------------------------------------------------------------
// Tiny visual anchor that gives each stage a stable index in the DAG.
// Color-coded by stage category so the user can scan "this DAG has
// 4 primitives (ice) → 3 operators (violet) → 1 terminal (amber)"
// at a glance.
// ============================================================================

import { cn } from '@/utils/cn';
import {
  badgeClassesForStage,
} from '@/components/build/lib/stageCategory';
import type { StageCategory } from '@/components/build/lib/buildTypes';

type Props = {
  number: number;
  category: StageCategory;
};

export function StageBadge({ number, category }: Props) {
  const c = badgeClassesForStage(category);
  return (
    <span
      className={cn(
        'flex h-5 w-5 shrink-0 items-center justify-center rounded-full border font-mono text-[10.5px] font-semibold',
        c.border,
        c.bg,
        c.text,
      )}
      aria-label={`Stage ${number}`}
    >
      {number}
    </span>
  );
}
