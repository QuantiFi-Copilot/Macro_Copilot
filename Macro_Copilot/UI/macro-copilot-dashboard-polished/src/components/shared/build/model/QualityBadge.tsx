// ============================================================================
// src/components/shared/build/model/QualityBadge.tsx — model-fit health chip.
// ----------------------------------------------------------------------------
// Consolidation target #4: ONE rendering for every model's quality /
// condition flags (PCA component quality_flag, regression
// condition_flag, half-life mean-reversion validity) so "this number is
// caveated" reads identically across models (P3, P5 honest disclosure).
// Finance-blind: parameterized by level + label, never by model.
// ============================================================================

import {
  AlertTriangle,
  CheckCircle2,
  XCircle,
  type LucideIcon,
} from 'lucide-react';
import { cn } from '@/utils/cn';
import type { QualityLevel } from './types';

const LEVEL_CLASS: Record<QualityLevel, string> = {
  ok: 'border-mint-400/25 bg-mint-400/[0.06] text-mint-300',
  warning: 'border-amber-400/25 bg-amber-400/[0.06] text-amber-300',
  degraded: 'border-coral-400/25 bg-coral-400/[0.06] text-coral-300',
};

// Component REFS, not pre-built elements — module-scope JSX executes
// at import time, which the module-test harness's classic JSX
// transform rejects (no React in scope).  JSX belongs inside render.
const LEVEL_ICON: Record<QualityLevel, LucideIcon> = {
  ok: CheckCircle2,
  warning: AlertTriangle,
  degraded: XCircle,
};

export interface QualityBadgeProps {
  level: QualityLevel;
  /** Chip text (e.g. "ok", "degenerate", "near-singular").  Rendered
   *  uppercase-kicker style. */
  label: string;
  /** Optional longer note shown as the title tooltip (P5: the caveat
   *  is reachable without leaving the surface). */
  note?: string;
}

export function QualityBadge({ level, label, note }: QualityBadgeProps) {
  const Icon = LEVEL_ICON[level];
  return (
    <span
      title={note}
      className={cn(
        'inline-flex items-center gap-1 rounded-full border px-1.5 py-px',
        'text-[9px] uppercase tracking-[0.1em]',
        LEVEL_CLASS[level],
      )}
    >
      <Icon size={10} strokeWidth={2} aria-hidden />
      {label}
    </span>
  );
}

/** Map a backend per-component PCA quality_flag onto the closed visual
 *  vocabulary.  Unknown flags map to "warning" (caveated, not hidden —
 *  P6: never silently render an unknown state as healthy). */
export function qualityLevelForFlag(flag: string): QualityLevel {
  if (flag === 'ok') return 'ok';
  if (flag === 'degenerate') return 'degraded';
  return 'warning';
}

/** Map a regression condition_flag (0 = OK, 1 = near-singular design
 *  matrix) onto the visual vocabulary. */
export function qualityLevelForConditionFlag(flag: number | null | undefined): QualityLevel {
  if (flag == null) return 'warning';
  return flag === 0 ? 'ok' : 'degraded';
}
