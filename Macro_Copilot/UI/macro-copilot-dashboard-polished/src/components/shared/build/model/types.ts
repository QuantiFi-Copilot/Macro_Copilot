// ============================================================================
// src/components/shared/build/model/types.ts — rich-model grammar types.
// ----------------------------------------------------------------------------
// Consolidation target #4: the shared UI grammar for rich-model results
// (PCA / rolling regression / attribution / half-life / beta-adjusted
// spread).  These types parameterize the grammar components by SHAPE —
// never by which model (FP13 finance-blindness).  A new model joins by
// mapping its Output onto these shapes in its own module folder; the
// shared layer needs no edit (the scaling invariant).
//
// Doctrine: docs_revamped/03_standards/rendering_density.md (dual-view),
// FP13 (finance-blind shared layer), P3 (consistency by contract).
// ============================================================================

import type { ChartTone } from '@/lib/chart';

// ---------------------------------------------------------------------------
// Quality — the closed vocabulary for model-fit health chips.
// ---------------------------------------------------------------------------
//
// Maps the backends' per-component / per-fit flags onto three visual
// levels:
//   ok       → mint   (clean fit; e.g. quality_flag === 'ok',
//                       condition_flag === 0)
//   warning  → amber  (usable but caveated; e.g. 'sign_anchor_tied',
//                       wide CIs)
//   degraded → coral  (do not lean on this number; e.g. 'degenerate',
//                       condition_flag === 1 near-singular design matrix)
export type QualityLevel = 'ok' | 'warning' | 'degraded';

// ---------------------------------------------------------------------------
// MatrixTable — loadings / coefficient matrices.
// ---------------------------------------------------------------------------

/** One row of a model matrix (a tenor row of PCA loadings, a regressor
 *  row of betas).  ``cells`` is keyed by column key; null renders an
 *  honest em-dash. */
export interface MatrixRow {
  /** Row identity rendered in the leftmost column (e.g. "2Y", "UST_10Y"). */
  key: string;
  cells: Record<string, number | null>;
}

// ---------------------------------------------------------------------------
// DecompositionBars — variance shares / attribution contributions.
// ---------------------------------------------------------------------------

/** One bar of a decomposition.  ``value`` semantics depend on the mode:
 *  - mode "share": a fraction in [0, 1] (variance_share); bars are
 *    unsigned, the cumulative column renders when ``cumulative`` set.
 *  - mode "signed": a signed quantity in display units (contribution
 *    bps); bars extend from a center axis, mint positive / coral
 *    negative. */
export interface DecompositionEntry {
  label: string;
  value: number | null;
  /** mode "share" only — the running cumulative fraction. */
  cumulative?: number | null;
  /** Optional fixed tone (defaults to the model tone cycle by index
   *  in "share" mode and to sign-based mint/coral in "signed" mode). */
  tone?: ChartTone;
  /** Optional quality chip rendered next to the label. */
  quality?: QualityLevel;
  qualityNote?: string;
  /** Render this entry visually de-emphasised (the residual /
   *  unexplained remainder row). */
  isResidual?: boolean;
}

// ---------------------------------------------------------------------------
// ModelSeriesPanel — factor scores / rolling betas / residual z.
// ---------------------------------------------------------------------------

/** One line of the shared multi-series model chart. */
export interface ModelSeries {
  /** Stable key (legend + React key), e.g. "pc1", "DE_BUND_10Y". */
  key: string;
  /** Human label for the legend; defaults to ``key``. */
  label?: string;
  rows: Array<{ date: string; value: number | null }>;
  /** Optional fixed tone; defaults to the model tone cycle by index. */
  tone?: ChartTone;
}

// ---------------------------------------------------------------------------
// The shared tone cycle — one sequence for every model's N-things
// (components, regressors).  Mirrors the PCA pilot so migrated models
// keep their established colour reads.
// ---------------------------------------------------------------------------

export const MODEL_TONE_CYCLE: readonly ChartTone[] = [
  'blue',
  'rates',
  'amber',
  'green',
  'coral',
  'neutral',
];

export function modelToneAt(index: number): ChartTone {
  return MODEL_TONE_CYCLE[index % MODEL_TONE_CYCLE.length];
}
