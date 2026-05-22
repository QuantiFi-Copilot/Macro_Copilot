// ============================================================================
// controlSchema.ts — shape of a derived parameter-control descriptor.
// ----------------------------------------------------------------------------
// The Build Parameters tab DOES NOT hard-code knowledge of any
// specific tool's input fields.  It derives a list of
// ``ParamControlDescriptor`` objects from the persisted node's
// params blob via ``deriveControlsForStage``, then looks up each
// descriptor's renderer in the control registry.
//
// This file declares the descriptor schema + the value/override
// types.  All of the parameter controls (Curve, Tenor, Window,
// etc.) implement the same component contract — they receive a
// ``ParamControlDescriptor`` plus the parent's override state and
// emit changes via the shared dispatcher.
//
// Future-proofing: adding a new control type = (1) one entry in the
// ``ParamControlKind`` literal union, (2) one entry in the control
// registry, (3) one new control file.  No call-site touched.
// ============================================================================

/** Closed-family enum of every control kind PR B ships.
 *
 *  Adding a new kind is a closed-family extension — every entry
 *  here must have a registered renderer in
 *  ``parameters/controls/`` and a matching case in the descriptor-
 *  derivation logic.  The discriminator on this enum keeps the
 *  registry lookup branchless at the consumer site. */
export type ParamControlKind =
  | 'curve_family' // sovereign / OIS curve family enum
  | 'tenor' // 1W / 1M / ... / 30Y chip group
  | 'lookback_days' // numeric stepper + 1y/2y/5y presets
  | 'window_days' // numeric stepper + tactical/quarterly/annual presets
  | 'field_name' // Bloomberg field enum (YLD_YTM_MID, etc.)
  | 'threshold' // numeric stepper for z-thresholds
  | 'date' // start/end date input
  | 'tool_name' // workflow tool name enum
  | 'output_field' // tool-output-field enum filtered by chosen tool
  | 'boolean' // toggle
  | 'numeric' // generic number with min/max
  | 'enum' // generic select
  | 'string' // fallback freeform input
  | 'readonly_json'; // unknown dict — read-only inspection only

/** Per-kind extra metadata.  Typed as a discriminated union so each
 *  control reads only the metadata its kind defines.
 *
 *  PR3 — ``tool_name`` and ``output_field`` carry richer context so the
 *  controls can render as validated dropdowns instead of free-text:
 *
 *    - ``tool_name.allowedTools``: the tool catalogue (loaded via
 *      ``useTools``).  When populated, the control renders a
 *      ``<select>`` over these values; when empty/undefined, the
 *      control falls back to the read-only chip (existing behavior
 *      for node-level ``tool_name`` params, which ARE topology-
 *      locked).
 *    - ``output_field.allowedFields``: the output_fields exposed by
 *      the currently-selected tool.  Populated by the deriver after
 *      it walks the tool catalogue + the paired tool slot.
 *    - ``output_field.relatedToolSlot``: name of the sibling tool
 *      slot this output_field validates against (e.g. ``signal_
 *      output_field`` is paired with ``signal_tool_name``).
 *      Persisted on the meta so the validator can resolve which
 *      tool was actually selected at submit time.
 *    - ``output_field.selectedTool``: snapshot of the paired tool
 *      slot's CURRENT value (bound or overridden).  Carried so the
 *      control header can show "Field name for {tool}" without
 *      having to plumb the override map.
 */
export type ParamControlMeta =
  | { kind: 'curve_family'; allowedDomains: Array<'sovereign' | 'ois'> }
  | { kind: 'tenor'; commonTenors: string[] }
  | { kind: 'lookback_days'; min: number; max: number }
  | { kind: 'window_days'; min: number; max: number }
  | { kind: 'field_name'; allowedFields: string[] }
  | { kind: 'threshold'; min: number; max: number; step: number }
  | { kind: 'date' }
  | { kind: 'tool_name'; allowedTools?: string[] }
  | {
      kind: 'output_field';
      allowedFields?: string[];
      /** Sibling tool slot name this output_field validates against
       *  (e.g. ``signal_tool_name`` for ``signal_output_field``).
       *  Populated by the deriver when it finds a paired tool slot;
       *  ``null`` when the pairing is unknown so the validator knows
       *  to skip the cross-slot check rather than failing closed. */
      relatedToolSlot?: string | null;
      /** Snapshot of the paired tool slot's current value at the
       *  time descriptors were derived.  Used by the control to
       *  caption the field choice. */
      selectedTool?: string | null;
    }
  | { kind: 'boolean' }
  | {
      kind: 'numeric';
      min?: number;
      max?: number;
      step?: number;
      integer?: boolean;
    }
  | { kind: 'enum'; options: Array<{ value: string; label: string }> }
  | { kind: 'string'; maxLength?: number }
  | { kind: 'readonly_json' };

/** Descriptor for one editable parameter on a workspace node.
 *
 *  Pure data — no React types, no event handlers.  This shape is
 *  derived once per node render and handed to the control
 *  components as props. */
export interface ParamControlDescriptor {
  /** Override-key path.  For top-level scalar slots, this is
   *  ``[slotName]``.  For nested dict slots (e.g. ``signal_params.
   *  window_days``), this is ``[slotName, fieldName]``.  The
   *  override-state machine uses this path to assemble the
   *  ``slot_overrides`` / ``slot_dict_overrides`` patch payload. */
  path: [string] | [string, string];
  /** Display label (Title Case) — derived from the path. */
  label: string;
  /** Short caption explaining what the parameter controls. */
  helpText?: string;
  /** Kind + per-kind metadata for the renderer. */
  meta: ParamControlMeta;
  /** Current value from the persisted node params.  ``undefined``
   *  when the parent's params don't carry this field (the control
   *  renders its kind-specific "no value" affordance). */
  currentValue: unknown;
  /** When true, the control renders as a tightened summary tile
   *  rather than a full editor.  Used by tools where a slot is
   *  template-locked (e.g. ``threshold_basis: raw_value`` on
   *  event_study) — the user sees the value but can't change it. */
  readOnly?: boolean;
  /** When false, the control hides the "edit" affordance and renders
   *  only as a styled value display.  Distinct from ``readOnly`` —
   *  hidden means "not on the editor surface at all". */
  hidden?: boolean;
}

/** Single override entry produced by the override-state reducer.
 *  Storage shape mirrors the server's two-flavor API: ``mode='scalar'``
 *  → ``slot_overrides[path[0]] = value``; ``mode='dict_field'`` →
 *  ``slot_dict_overrides[path[0]][path[1]] = value``. */
export type ParamOverride =
  | {
      mode: 'scalar';
      path: [string];
      value: unknown;
      /** For UX: the parent's value before the override was applied.
       *  Lets the PendingOverridesBar render "was → is" pairs. */
      previousValue: unknown;
    }
  | {
      mode: 'dict_field';
      path: [string, string];
      value: unknown;
      previousValue: unknown;
    };

/** Collected pending overrides addressed by their path tuple
 *  serialized as ``"slot"`` / ``"slot.field"``.  The reducer keeps
 *  only the latest value per path. */
export type OverrideMap = Record<string, ParamOverride>;

/** Stable key for a ParamOverride.  Used everywhere we need a
 *  string lookup (React keys, Map keys, etc.).  Kept as a single
 *  function so any future path-shape change touches one site. */
export function overrideKey(
  path: ParamOverride['path'] | ParamControlDescriptor['path'],
): string {
  return path.length === 1 ? path[0] : `${path[0]}.${path[1]}`;
}
