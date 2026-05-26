// ============================================================================
// src/types/monitorWidget.ts — Monitor widget catalog types (leaf-file).
// ----------------------------------------------------------------------------
// Stage 4d — extracted from src/components/monitor/registry.ts so per-
// primitive ``module.ts`` files can type-only import the shapes without
// re-entering the monitor page-shell graph (FP12 + runtime cycle
// avoidance).  ``src/components/monitor/registry.ts`` re-exports the
// same types for back-compat with non-module consumers.
//
// All types here are PURE values.  No React import.  No runtime side
// effects.  Safe to import from anywhere.
// ============================================================================

import type { ComponentType } from 'react';

/** Drives the gradient top-rule color on a widget card.  Closed
 *  family — adding a new category requires an explicit spec change. */
export type WidgetCategory = 'data' | 'analysis' | 'anomaly';

/** Bento-grid sizes.  Small/medium are 4-up / 2-up on a 12-column
 *  grid; wide is full-row; tall is medium-width with double height
 *  for charts that need vertical space (PCA, attribution
 *  decomposition). */
export type WidgetSize = 'small' | 'medium' | 'wide' | 'tall';

/** Per-field config for parameterised widgets.  Each field renders a
 *  matching control in the catalog modal's config form.  Conservative
 *  set: select (enum) and number — enough for every V1 rates widget. */
export type WidgetParamField =
  | {
      kind: 'select';
      name: string;
      label: string;
      defaultValue: string;
      options: ReadonlyArray<{ value: string; label: string }>;
      /** Optional cross-field constraint hint surfaced in the form. */
      mustDifferFrom?: string;
    }
  | {
      kind: 'number';
      name: string;
      label: string;
      defaultValue: number;
      min?: number;
      max?: number;
      step?: number;
    };

/** Public widget-catalog metadata.  Built either from a per-module
 *  ``MonitorWidgetMeta`` (Stage 4d module-derived path) or from the
 *  hand-authored hybrid set in ``monitor/registry.ts`` (today only
 *  the pre-aggregated ``yield_snapshot`` widget). */
export type WidgetTypeMeta = {
  /** Stable id; used as registry key + serialized in layout state. */
  id: string;
  /** User-facing label in the catalog. */
  label: string;
  /** One-line description shown in the catalog tile. */
  description: string;
  /** Drives the gradient top-rule color on the card. */
  category: WidgetCategory;
  /** Size assigned when the user adds this widget without specifying. */
  defaultSize: WidgetSize;
  /** Sizes the user can choose from at add-time. */
  allowedSizes: ReadonlyArray<WidgetSize>;
  /** True if the widget needs user-supplied params (curve, tenor, etc).
   *  Pre-aggregated widgets are false — they show "the rates page" data
   *  unchanged. */
  parameterized: boolean;
  /** Per-field config used to render the catalog modal's config form. */
  paramFields?: ReadonlyArray<WidgetParamField>;
  /** The substrate primitive (or pre-aggregated endpoint) this widget
   *  surfaces.  Documented for the catalog tile + provenance footer. */
  sourceTool: string;
};

/** Per-module Monitor widget declaration.  Identical to
 *  ``WidgetTypeMeta`` except (a) ``sourceTool`` is implicit (= module's
 *  toolName, derived by the registry walker), and (b) the renderer
 *  component is carried alongside the metadata so the walker can wire
 *  both the catalog entry AND the ``WidgetRenderer`` dispatch from a
 *  single source. */
export interface MonitorWidgetMeta {
  id: string;
  label: string;
  description: string;
  category: WidgetCategory;
  defaultSize: WidgetSize;
  allowedSizes: ReadonlyArray<WidgetSize>;
  parameterized: boolean;
  paramFields?: ReadonlyArray<WidgetParamField>;
  /** Renderer component.  Pre-aggregated widgets ignore the ``params``
   *  prop; parameterised widgets read from it.  Typed as
   *  ``ComponentType<any>`` for the migration duration; aspirational
   *  strict ``ComponentType<MonitorWidgetProps>`` returns in Stage N. */
  component: ComponentType<any>; // eslint-disable-line @typescript-eslint/no-explicit-any
}
