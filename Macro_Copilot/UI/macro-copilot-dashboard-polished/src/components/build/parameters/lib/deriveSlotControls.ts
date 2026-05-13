// ============================================================================
// deriveSlotControls.ts — slot-schema-driven descriptor derivation.
// ----------------------------------------------------------------------------
// PR5 — pivots the Parameters tab from "edit node.params" to "edit
// workspace.bound_slot_values".  The substrate's fork endpoint
// (``POST /workspace/{slug}/fork``) takes ``slot_overrides`` +
// ``slot_dict_overrides``, both keyed by SLOT NAMES from the
// template's ``slot_schema``.  Node params are execution detail
// (substrate-bound values + per-bridge-layer enrichment) and are
// NOT the right mutation surface.
//
// Before PR5 the editor derived descriptors from ``node.params``,
// which produced paths like ``["params", "field_name"]`` that the
// fork endpoint silently ignored (the backend looks them up against
// the template's slot names, not the node-param keys).  PR5
// derives descriptors directly from ``slot_schema`` (the template's
// declared slot list) joined against ``workspace.bound_slot_values``
// (the workspace's snapshot of those slots at bind time).
//
// What this helper does
// ---------------------
//   1. Walk every ``SlotDeclaration`` in the workflow template's
//      ``slot_schema``.
//   2. For scalar slots (str / int / float / bool), emit one
//      ``ParamControlDescriptor`` with ``path: [slotName]``.
//   3. For dict slots whose persisted ``bound_slot_values`` carries a
//      plain-object value, expand one level: each inner field becomes
//      its own descriptor with ``path: [slotName, fieldName]``.
//      Deeper nesting / list slots fall through to ``readonly_json``
//      so the user can at least inspect them.
//   4. Map slot types + name patterns to control kinds the existing
//      registry already handles (curve_family / tenor /
//      lookback_days / window_days / threshold / date / etc.).
//
// Why we reuse the existing ``ParamControlDescriptor`` schema
// -----------------------------------------------------------
// The override-state machine, the control registry, the
// ``overridesToServerPatch`` translator, and the fork POST already
// speak ``ParamControlDescriptor`` + ``OverrideMap``.  PR5 keeps
// those untouched; the only change is the descriptor SOURCE.  This
// minimises the blast radius.
// ============================================================================

import type {
  SlotDeclaration,
  WorkflowTemplateCard,
} from '@/types/workflows';
import type {
  ParamControlDescriptor,
  ParamControlKind,
  ParamControlMeta,
} from './controlSchema';

const KNOWN_BLOOMBERG_FIELDS = [
  'YLD_YTM_MID',
  'YLD_YTM_LAST',
  'PX_MID',
  'PX_LAST',
  'PX_BID',
  'PX_ASK',
];

const COMMON_TENORS = [
  '1W',
  '1M',
  '3M',
  '6M',
  '1Y',
  '2Y',
  '3Y',
  '5Y',
  '7Y',
  '10Y',
  '20Y',
  '30Y',
];

/** Top-level entry point — derives the editable-descriptor list for
 *  every slot the workspace's template declares.  Falls back to the
 *  bound-values themselves when the schema is missing (legacy
 *  workspaces predate ``slot_schema`` surfacing).
 *
 *  Pure function — no React, no async — straightforward to test. */
export function deriveSlotControls(args: {
  boundSlotValues: Record<string, unknown> | null;
  slotSchema: SlotDeclaration[] | null;
}): ParamControlDescriptor[] {
  const bound = args.boundSlotValues ?? {};
  const schema = args.slotSchema ?? [];

  // When the template card hasn't loaded yet (or the workspace is
  // legacy + has no template_id), fall back to deriving from the
  // bound values' shape so the user can at least SEE what's
  // currently bound.  These fall-through descriptors are still
  // overridable through the same fork pipeline — the backend
  // resolves them against the actual slot schema at bind time.
  if (schema.length === 0) {
    return walkBoundValuesAsScalars(bound);
  }

  const out: ParamControlDescriptor[] = [];
  const seenSlotNames = new Set<string>();
  for (const decl of schema) {
    seenSlotNames.add(decl.name);
    const value = bound[decl.name];

    // Dict slots — expand one level if the bound value is a plain
    // object so each inner field becomes its own descriptor.  This is
    // the canonical "edit window_days inside signal_params" path the
    // backend's ``slot_dict_overrides`` is designed for.
    if (decl.type === 'dict' && isPlainObject(value)) {
      // Add a synthetic header descriptor (read-only label) so the
      // user sees the slot name above its expanded fields.  Marked
      // hidden so it doesn't render a control input.
      out.push({
        path: [decl.name],
        label: prettyKey(decl.name),
        helpText: decl.description,
        meta: { kind: 'readonly_json' },
        currentValue: value,
        readOnly: true,
        hidden: true,
      });
      for (const [innerKey, innerValue] of Object.entries(value)) {
        out.push(
          buildDescriptor({
            path: [decl.name, innerKey],
            value: innerValue,
            helpText: `Inside ${prettyKey(decl.name)}.`,
            parentDecl: decl,
          }),
        );
      }
      continue;
    }

    // List slots — render read-only for now; the override pipeline
    // doesn't have a per-index merge primitive today (would need a
    // third backend override-flavor).  Future work; PR5 surfaces the
    // value honestly rather than faking an editor.
    if (decl.type === 'list') {
      out.push({
        path: [decl.name],
        label: prettyKey(decl.name),
        helpText:
          decl.description ||
          'List slot — editable per-index in a future PR.',
        meta: { kind: 'readonly_json' },
        currentValue: value,
        readOnly: true,
      });
      continue;
    }

    // Scalar slot.
    out.push(
      buildDescriptor({
        path: [decl.name],
        value,
        helpText: decl.description,
        parentDecl: decl,
      }),
    );
  }

  // Capture any persisted bound values the schema doesn't declare.
  // These are usually substrate-internal or legacy entries; render
  // them read-only so the user can inspect them without producing
  // a meaningless slot patch.
  for (const [key, value] of Object.entries(bound)) {
    if (seenSlotNames.has(key)) continue;
    out.push({
      path: [key],
      label: prettyKey(key),
      helpText:
        'This bound value is not declared in the current template schema.  Read-only.',
      meta: { kind: 'readonly_json' },
      currentValue: value,
      readOnly: true,
    });
  }

  return out;
}

/** Convenience: derive controls from a ``WorkflowTemplateCard`` +
 *  workspace bound values.  Thin wrapper so consumers don't have to
 *  spell out the schema-from-card extraction at every call site. */
export function deriveSlotControlsFromCard(args: {
  boundSlotValues: Record<string, unknown> | null;
  card: WorkflowTemplateCard | null;
}): ParamControlDescriptor[] {
  return deriveSlotControls({
    boundSlotValues: args.boundSlotValues,
    slotSchema: args.card?.slot_schema ?? null,
  });
}

// ----------------------------------------------------------------------------
// Internals
// ----------------------------------------------------------------------------

function buildDescriptor(args: {
  path: [string] | [string, string];
  value: unknown;
  helpText?: string;
  parentDecl?: SlotDeclaration;
}): ParamControlDescriptor {
  const leaf = args.path[args.path.length - 1];
  const kind = classifyKind(leaf, args.value, args.parentDecl);
  const meta = buildMeta(kind, leaf, args.value);
  return {
    path: args.path,
    label: prettyKey(leaf),
    helpText: args.helpText ?? helpForLeaf(leaf, kind),
    meta,
    currentValue: args.value,
  };
}

/** Map a slot's (leaf-name + value + optional schema declaration)
 *  to a ``ParamControlKind``.  Name patterns win when present —
 *  they encode the user's intent (curve_family / tenor / threshold).
 *  Otherwise the schema's declared type drives the fall-through. */
function classifyKind(
  leaf: string,
  value: unknown,
  parentDecl?: SlotDeclaration,
): ParamControlKind {
  const lower = leaf.toLowerCase();

  // Name-pattern heuristics — same vocabulary the node-param deriver
  // uses so the control UX is consistent across surfaces.
  if (lower.includes('curve_family') || lower.endsWith('_curve')) {
    return 'curve_family';
  }
  if (lower === 'tenor' || lower.endsWith('_tenor')) return 'tenor';
  if (lower.endsWith('lookback_days')) return 'lookback_days';
  if (
    lower.endsWith('window_days') ||
    lower === 'window' ||
    lower.endsWith('_window')
  ) {
    return 'window_days';
  }
  if (lower === 'field_name' || lower.endsWith('_field_name'))
    return 'field_name';
  if (lower === 'threshold' || lower.endsWith('_threshold'))
    return 'threshold';
  if (
    lower === 'start_date' ||
    lower === 'end_date' ||
    lower.endsWith('_date')
  ) {
    return 'date';
  }

  // Schema-type fall-through.
  if (parentDecl) {
    if (parentDecl.type === 'bool') return 'boolean';
    if (parentDecl.type === 'int' || parentDecl.type === 'float')
      return 'numeric';
    if (parentDecl.type === 'str') return 'string';
    if (parentDecl.type === 'dict' || parentDecl.type === 'list')
      return 'readonly_json';
  }

  // Last-resort value-type fall-through (e.g. legacy bound values
  // with no schema declaration).
  if (typeof value === 'boolean') return 'boolean';
  if (typeof value === 'number') return 'numeric';
  if (isPlainObject(value) || Array.isArray(value)) return 'readonly_json';
  return 'string';
}

function buildMeta(
  kind: ParamControlKind,
  leaf: string,
  value: unknown,
): ParamControlMeta {
  switch (kind) {
    case 'curve_family': {
      const lower = leaf.toLowerCase();
      const allowed: Array<'sovereign' | 'ois'> = [];
      if (lower.includes('ois')) allowed.push('ois');
      if (lower.includes('sovereign')) allowed.push('sovereign');
      if (allowed.length === 0) allowed.push('sovereign', 'ois');
      return { kind, allowedDomains: allowed };
    }
    case 'tenor':
      return { kind, commonTenors: COMMON_TENORS };
    case 'lookback_days':
      return { kind, min: 30, max: 7300 };
    case 'window_days':
      return { kind, min: 20, max: 1260 };
    case 'field_name':
      return { kind, allowedFields: KNOWN_BLOOMBERG_FIELDS };
    case 'threshold':
      return { kind, min: 0, max: 5, step: 0.1 };
    case 'date':
      return { kind };
    case 'tool_name':
      return { kind };
    case 'output_field':
      return { kind };
    case 'boolean':
      return { kind };
    case 'numeric':
      return {
        kind,
        integer: typeof value === 'number' && Number.isInteger(value),
      };
    case 'enum':
      return { kind, options: [] };
    case 'string':
      return { kind };
    case 'readonly_json':
      return { kind };
  }
}

/** Pre-PR5 fall-through used when the template card hasn't loaded
 *  (or the workspace pre-dates the slot-schema surface).  Walks the
 *  bound values one level so the user can at least edit the obvious
 *  scalars; dict slots are dict-expanded by the same one-level rule
 *  ``deriveSlotControls`` uses. */
function walkBoundValuesAsScalars(
  bound: Record<string, unknown>,
): ParamControlDescriptor[] {
  const out: ParamControlDescriptor[] = [];
  for (const [key, value] of Object.entries(bound)) {
    if (isPlainObject(value)) {
      out.push({
        path: [key],
        label: prettyKey(key),
        meta: { kind: 'readonly_json' },
        currentValue: value,
        readOnly: true,
        hidden: true,
      });
      for (const [nk, nv] of Object.entries(value)) {
        out.push(
          buildDescriptor({
            path: [key, nk],
            value: nv,
            helpText: `Inside ${prettyKey(key)}.`,
          }),
        );
      }
      continue;
    }
    if (Array.isArray(value)) {
      out.push({
        path: [key],
        label: prettyKey(key),
        meta: { kind: 'readonly_json' },
        currentValue: value,
        readOnly: true,
      });
      continue;
    }
    out.push(
      buildDescriptor({ path: [key], value }),
    );
  }
  return out;
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return (
    typeof value === 'object' &&
    value !== null &&
    !Array.isArray(value)
  );
}

function prettyKey(raw: string): string {
  return raw
    .split('_')
    .filter(Boolean)
    .map((tok) => tok.charAt(0).toUpperCase() + tok.slice(1).toLowerCase())
    .join(' ');
}

function helpForLeaf(
  leaf: string,
  kind: ParamControlKind,
): string | undefined {
  const lower = leaf.toLowerCase();
  if (kind === 'curve_family')
    return 'Sovereign or OIS curve family identifier.';
  if (kind === 'tenor') return 'Tenor point on the curve.';
  if (kind === 'lookback_days')
    return 'Calendar days of displayed history.';
  if (kind === 'window_days')
    return 'Rolling-window length used by the statistical fit.';
  if (kind === 'field_name')
    return 'Bloomberg field mnemonic (overrides config.yaml default).';
  if (kind === 'threshold')
    return '|signal| boundary at which an event fires.';
  if (lower.startsWith('start_'))
    return 'Earliest date in the analysis window.';
  if (lower.startsWith('end_'))
    return 'Latest date in the analysis window.';
  return undefined;
}
