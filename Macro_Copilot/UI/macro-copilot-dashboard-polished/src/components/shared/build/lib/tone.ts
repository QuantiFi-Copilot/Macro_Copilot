// ============================================================================
// shared/build/lib/tone.ts — Tone helpers for numeric KPI values.
// ----------------------------------------------------------------------------
// Per docs_revamped/03_standards/rendering_density.md §2.2 the compact
// view MUST surface sign-convention coloring (positive/negative changes)
// and z-score extremity bands.  These helpers centralise the threshold
// logic so every per-tool wrapper picks tones consistently — preventing
// drift across modules where one tool flags |z| ≥ 1.5 as elevated and
// another flags |z| ≥ 1.7.
//
// FINANCE-BLIND DEFAULT for change tone:
//   The default convention in this file is "positive numeric value →
//   ``negative`` tone (typically coral/red)" because in fixed-income
//   contexts (yields, real yields, OIS rates) a positive level change
//   represents tightening — the bond price fell.  Per-tool wrappers
//   that need the OPPOSITE convention (e.g. spread WIDENING signaling
//   risk-off) pass ``invert: true`` to ``toneForChange``.
// ============================================================================

import type { ValueTone } from './types';

// ---------------------------------------------------------------------------
// Z-score regime thresholds — closed family.
// ---------------------------------------------------------------------------
//
// |z| < 1.5      → ``neutral``
// 1.5 ≤ |z| < 2  → ``elevated`` (amber)
// |z| ≥ 2        → ``extreme-up`` (coral) for z > 0; ``extreme-down``
//                  (mint) for z < 0
//
// Thresholds are documented in
// docs_revamped/03_standards/rendering_density.md §2.2 and pinned by
// per-module visual tests.  Changing them requires an ADR.

export const Z_ELEVATED_THRESHOLD = 1.5;
export const Z_EXTREME_THRESHOLD = 2.0;

/** Map a z-score (signed) to its tone.  Returns ``neutral`` when z is
 *  null/undefined. */
export function toneForZScore(z: number | null | undefined): ValueTone {
  if (z == null || Number.isNaN(z)) return 'neutral';
  const abs = Math.abs(z);
  if (abs >= Z_EXTREME_THRESHOLD) {
    return z >= 0 ? 'extreme-up' : 'extreme-down';
  }
  if (abs >= Z_ELEVATED_THRESHOLD) return 'elevated';
  return 'neutral';
}

/** Map a signed change value to its tone.
 *
 *  Default: positive change → ``negative`` tone (tightening), negative
 *  change → ``positive`` tone (easing).  This matches the fixed-income
 *  convention shown in the rendering-density mockups.
 *
 *  Pass ``invert: true`` for tools where positive change should render
 *  as ``positive`` tone (e.g. equity returns, recovery rates, etc.). */
export function toneForChange(
  value: number | null | undefined,
  options: { invert?: boolean } = {},
): ValueTone {
  if (value == null || Number.isNaN(value) || value === 0) return 'neutral';
  const positive = options.invert ? value < 0 : value > 0;
  return positive ? 'negative' : 'positive';
}

/** Map a percentile (0-100) to a tone bucket.  Used by the stretch-
 *  context panel and as an optional tone on KPI cells.  Thresholds:
 *  < 20 → ``positive`` (low end of the trailing range — typically
 *  cheap), 20-80 → ``neutral`` (mid-range), > 80 → ``negative``
 *  (rich, stretched). */
export function toneForPercentile(p: number | null | undefined): ValueTone {
  if (p == null || Number.isNaN(p)) return 'neutral';
  if (p >= 80) return 'negative';
  if (p <= 20) return 'positive';
  return 'neutral';
}

/** Map a z-score to its regime label (Normal / Elevated / Extreme). */
export function regimeForZScore(z: number | null | undefined):
  | 'Normal'
  | 'Elevated'
  | 'Extreme' {
  if (z == null || Number.isNaN(z)) return 'Normal';
  const abs = Math.abs(z);
  if (abs >= Z_EXTREME_THRESHOLD) return 'Extreme';
  if (abs >= Z_ELEVATED_THRESHOLD) return 'Elevated';
  return 'Normal';
}

/** Map a percentile to its bucket label (Low / Normal / High). */
export function bucketForPercentile(p: number | null | undefined):
  | 'Low'
  | 'Normal'
  | 'High' {
  if (p == null || Number.isNaN(p)) return 'Normal';
  if (p >= 80) return 'High';
  if (p <= 20) return 'Low';
  return 'Normal';
}

/** Map a ValueTone to the project's tailwind text color class.  Kept
 *  centralised here so every shell renders tones identically. */
export function toneTextClass(tone: ValueTone | undefined): string {
  switch (tone) {
    case 'positive':
      return 'text-mint-300';
    case 'negative':
      return 'text-coral-300';
    case 'elevated':
      return 'text-amber-300';
    case 'extreme-up':
      return 'text-coral-300';
    case 'extreme-down':
      return 'text-mint-300';
    case 'neutral':
    case undefined:
    default:
      return 'text-fg-primary';
  }
}

/** Map a reference-band tone to a stroke color class. */
export function referenceBandStrokeClass(
  tone: 'neutral' | 'positive' | 'negative' | 'elevated' | 'extreme',
): string {
  switch (tone) {
    case 'positive':
    case 'extreme':
      return 'stroke-mint-300';
    case 'negative':
      return 'stroke-coral-300';
    case 'elevated':
      return 'stroke-amber-300';
    case 'neutral':
    default:
      return 'stroke-fg-faint';
  }
}
