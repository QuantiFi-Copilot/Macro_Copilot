// ============================================================================
// validateOverrides.ts — PR3 cross-slot validation for the override queue.
// ----------------------------------------------------------------------------
// Pre-PR3 the "Apply & fork" button enabled the moment a single
// override was queued, even when the queue contained an inconsistent
// combination (e.g. ``signal_tool_name = X`` paired with
// ``signal_output_field = Y`` where Y is not in X's output_fields).
// That meant the user could fork a workspace whose backend bind would
// reject at substrate-bind time, with a less friendly error than the
// UI could surface.
//
// PR3 adds a pure validator the panel + the pending bar consult
// before allowing apply.  The check is intentionally narrow:
//
//   1. ``tool_name`` overrides must name a known tool (catalogue
//      lookup).  Empty catalogue → skipped (the deriver hadn't
//      loaded the catalogue, so we can't validate; we don't fail
//      closed because that would block fork on a transient network
//      blip).
//
//   2. ``output_field`` overrides must be one of the effective
//      tool's output_fields.  Effective tool = override value if
//      the paired tool_name slot is also being overridden, else the
//      bound value of the paired tool slot.
//
// Anything else (numeric ranges, tenor strings, etc.) is left to the
// per-control input layer.  This validator is the cross-slot
// "consistency" gate, not a syntax check.
// ============================================================================

import type { ToolCard } from '@/types/workflows';
import type {
  OverrideMap,
  ParamControlDescriptor,
} from './controlSchema';
import {
  isOutputFieldSlot,
  isToolNameSlot,
  pairedToolSlotFor,
} from './deriveSlotControls';

/** Single validation finding.  Caller renders these as inline
 *  errors next to the offending chip in the PendingOverridesBar
 *  AND uses them to disable the Apply button. */
export interface OverrideValidationError {
  /** The override key (``"slot"`` or ``"slot.field"``) the error
   *  belongs to. */
  overrideKey: string;
  /** Short, user-facing message.  Kept terse — the UI mounts these
   *  inside a chip-sized footnote. */
  message: string;
  /** Discriminator so the UI / tests can branch on cause without
   *  parsing the message. */
  reason:
    | 'unknown_tool'
    | 'unknown_output_field'
    | 'unknown_slot';
}

export interface OverrideValidationResult {
  ok: boolean;
  errors: OverrideValidationError[];
}

/** Pure validator.  Returns ``{ok: true, errors: []}`` when every
 *  queued override is consistent against the tool catalogue + the
 *  workspace's bound slots; otherwise ``{ok: false, errors: [...]}``. */
export function validateOverrides(args: {
  overrides: OverrideMap;
  boundSlotValues: Record<string, unknown> | null;
  tools: ToolCard[] | null;
  /** Known slot names from the workflow's slot_schema.  When provided,
   *  any override targeting a slot NOT in this set produces an
   *  ``unknown_slot`` error.  When omitted, the slot-existence check
   *  is skipped (legacy / pre-PR-B workspaces predate the schema
   *  surface, so we can't enforce it). */
  knownSlotNames?: Set<string> | null;
}): OverrideValidationResult {
  const errors: OverrideValidationError[] = [];
  const bound = args.boundSlotValues ?? {};
  const toolIndex: Record<string, ToolCard> = {};
  for (const t of args.tools ?? []) toolIndex[t.tool_name] = t;
  const haveCatalogue = Object.keys(toolIndex).length > 0;

  // Materialise the effective tool-slot map (bound + overrides
  // applied) so output_field validations resolve against the same
  // tool the user is actually queueing.
  const effectiveTools: Record<string, string> = {};
  for (const [k, v] of Object.entries(bound)) {
    if (typeof v === 'string' && isToolNameSlot(k)) effectiveTools[k] = v;
  }
  for (const o of Object.values(args.overrides)) {
    if (o.mode === 'scalar' && isToolNameSlot(o.path[0])) {
      if (typeof o.value === 'string') effectiveTools[o.path[0]] = o.value;
    }
  }

  for (const o of Object.values(args.overrides)) {
    const okey = overrideKey(o.path);
    const slotName = o.path[0];

    // 1. Unknown slot — only enforced when the schema is available.
    if (args.knownSlotNames && !args.knownSlotNames.has(slotName)) {
      errors.push({
        overrideKey: okey,
        message: `Slot "${slotName}" is not declared in the template schema.`,
        reason: 'unknown_slot',
      });
      continue;
    }

    // 2. Tool-name override — must be in the catalogue.
    if (isToolNameSlot(slotName) && o.mode === 'scalar') {
      if (haveCatalogue && typeof o.value === 'string') {
        if (!(o.value in toolIndex)) {
          errors.push({
            overrideKey: okey,
            message: `Unknown tool "${o.value}" — not in the registered catalogue.`,
            reason: 'unknown_tool',
          });
        }
      }
      continue;
    }

    // 3. Output-field override — must be one of the effective tool's
    //    output_fields.  Skipped silently when we lack the catalogue
    //    OR the paired tool slot value is unknown.
    if (isOutputFieldSlot(slotName) && o.mode === 'scalar') {
      const paired = pairedToolSlotFor(slotName);
      if (!paired || !haveCatalogue) continue;
      const selectedTool = effectiveTools[paired];
      if (!selectedTool) continue;
      const card = toolIndex[selectedTool];
      if (!card) continue;
      const allowed = new Set(card.output_fields.map((f) => f.name));
      if (typeof o.value === 'string' && !allowed.has(o.value)) {
        errors.push({
          overrideKey: okey,
          message: `"${o.value}" is not an output field of ${selectedTool}.`,
          reason: 'unknown_output_field',
        });
      }
      continue;
    }
  }

  return { ok: errors.length === 0, errors };
}

/** True iff an individual descriptor + proposed value would pass the
 *  same validation.  Used by ``ProposedOverridesChips`` to gate chip
 *  application before the value lands in the override queue. */
export function isProposedOverrideValid(args: {
  descriptor: ParamControlDescriptor;
  value: unknown;
  boundSlotValues: Record<string, unknown> | null;
  tools: ToolCard[] | null;
  knownSlotNames?: Set<string> | null;
}): OverrideValidationResult {
  // Build a synthetic single-entry override map keyed by the chip's
  // path, then reuse ``validateOverrides`` so the rules stay in one
  // place.  We treat the chip's path as a scalar slot when path.length
  // is 1, dict_field otherwise — mirrors the reducer's coercion.
  const okey =
    args.descriptor.path.length === 1
      ? args.descriptor.path[0]
      : `${args.descriptor.path[0]}.${args.descriptor.path[1]}`;
  const synthetic: OverrideMap = {
    [okey]:
      args.descriptor.path.length === 1
        ? {
            mode: 'scalar',
            path: args.descriptor.path,
            value: args.value,
            previousValue: args.descriptor.currentValue,
          }
        : {
            mode: 'dict_field',
            path: args.descriptor.path,
            value: args.value,
            previousValue: args.descriptor.currentValue,
          },
  };
  return validateOverrides({
    overrides: synthetic,
    boundSlotValues: args.boundSlotValues,
    tools: args.tools,
    knownSlotNames: args.knownSlotNames,
  });
}

function overrideKey(path: [string] | [string, string]): string {
  return path.length === 1 ? path[0] : `${path[0]}.${path[1]}`;
}
