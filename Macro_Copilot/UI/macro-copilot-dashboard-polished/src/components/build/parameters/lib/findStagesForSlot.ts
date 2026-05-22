// ============================================================================
// findStagesForSlot.ts — heuristic mapping from a slot to affected stages.
// ----------------------------------------------------------------------------
// PR5 — when the user looks at an editable slot (e.g. ``signal_params.
// window_days``), they need to know which stages it affects so the
// "Affected stages" chip strip is meaningful.  The substrate doesn't
// expose the slot→node binding directly on the workspace summary
// (the catalogue's ``TemplateCard`` doesn't include the YAML's
// ``$slot`` references), so this helper does a heuristic walk over
// the persisted node params + the slot's bound value.
//
// Heuristic
// ---------
// For each slot path ``[slot]`` or ``[slot, field]`` and its bound
// value, we check every node in the workspace and report a match
// when:
//
//   1. (value match) the node's inner params dict has a key whose
//      value equals the slot value.  Reliable for scalar slots whose
//      bound value is distinctive.
//
//   2. (name + value match) the inner params dict has a key whose
//      NAME matches the slot's leaf name AND whose value equals the
//      bound value.  Tightens the match for slots whose value alone
//      isn't distinctive (e.g. ``window_days=126`` could match
//      multiple unrelated knobs, but only ``window_days`` exactly is
//      the canonical match).
//
//   3. (name-only match) the inner params dict has a key matching
//      the slot's leaf name.  Looser fallback; only used when the
//      value match path produced nothing.
//
// Returns a deduplicated, topologically-stable list of node IDs +
// the match strength so the UI can display chips like:
//
//     Affects: rolling_zscore (exact) · threshold_events (exact)
//
// Where the heuristic finds NOTHING, we return an empty list — the
// chip strip shows "(computed during binding)" honestly rather than
// guessing.
//
// Pure function — no React, no async — easy to unit-test.
// ============================================================================

import type { NodeSummary } from '@/services/workspaceApi';

/** Strength of a slot→stage match — used to decide whether to render
 *  the chip definitively or with a "best guess" caveat. */
export type SlotMatchStrength = 'exact' | 'value' | 'name';

export interface SlotStageMatch {
  /** ``NodeSummary.node_id`` of the matched stage. */
  nodeId: string;
  /** ``NodeSummary.name`` (or node_id if missing) for the chip label. */
  label: string;
  /** Why we matched — used by the UX to decide chip styling. */
  strength: SlotMatchStrength;
}

/** Find every stage whose node params reference a slot's bound value.
 *  See module docstring for the heuristic.  Returns a deduplicated
 *  list (one entry per node, preferring the strongest match). */
export function findStagesForSlot(args: {
  path: [string] | [string, string];
  boundValue: unknown;
  nodes: NodeSummary[];
}): SlotStageMatch[] {
  const leafName = args.path[args.path.length - 1];
  const matches = new Map<string, SlotStageMatch>();

  for (const node of args.nodes) {
    const inner = innerParams(node);
    if (!inner) continue;
    const strength = matchStrength(inner, leafName, args.boundValue);
    if (strength === null) continue;
    const existing = matches.get(node.node_id);
    if (
      existing === undefined ||
      strengthRank(strength) > strengthRank(existing.strength)
    ) {
      matches.set(node.node_id, {
        nodeId: node.node_id,
        label: node.name || node.node_id,
        strength,
      });
    }
  }

  return Array.from(matches.values());
}

// ----------------------------------------------------------------------------
// Internals
// ----------------------------------------------------------------------------

function strengthRank(s: SlotMatchStrength): number {
  switch (s) {
    case 'exact':
      return 3;
    case 'value':
      return 2;
    case 'name':
      return 1;
  }
}

/** Inspect a node's params dict and decide the strongest match for
 *  the ``(leafName, boundValue)`` pair.  Returns ``null`` when no
 *  match path applies. */
function matchStrength(
  inner: Record<string, unknown>,
  leafName: string,
  boundValue: unknown,
): SlotMatchStrength | null {
  let valueMatchSomewhere = false;
  let nameMatchSomewhere = false;
  let exactMatch = false;

  for (const [key, val] of Object.entries(inner)) {
    const keyMatches = key === leafName;
    const valMatches = deepEqual(val, boundValue);
    if (keyMatches && valMatches) {
      exactMatch = true;
      break;
    }
    if (keyMatches) nameMatchSomewhere = true;
    if (valMatches) valueMatchSomewhere = true;
  }

  if (exactMatch) return 'exact';
  // Value-only matches are strong when the bound value is
  // distinctive (a long string, a tenor, a curve name); we accept
  // them when the bound value is "interesting" enough that random
  // collisions are unlikely.  Cheap heuristic: non-empty string or
  // a number with at least 2 significant digits.
  if (valueMatchSomewhere && isDistinctiveValue(boundValue)) return 'value';
  if (nameMatchSomewhere) return 'name';
  return null;
}

/** Skip noisy value matches: ``true`` / ``false`` / ``0`` / ``1``
 *  could collide too easily with unrelated knobs.  We accept tenors,
 *  curve names, numeric windows ≥ 20, and non-trivial strings. */
function isDistinctiveValue(v: unknown): boolean {
  if (v === null || v === undefined) return false;
  if (typeof v === 'boolean') return false;
  if (typeof v === 'number') return Number.isFinite(v) && Math.abs(v) >= 5;
  if (typeof v === 'string') return v.length >= 2;
  // Objects / arrays — accept on the assumption that the deep-equal
  // check is already restrictive.
  return true;
}

/** Walk the substrate's node-param wrapping shape:
 *  PrimitiveNode → ``{tool_name, output_field, params: {…}}``
 *  OperatorNode  → ``{operator_name, params: {…}}``
 *  Unwrap the inner params dict; return null for malformed shapes. */
function innerParams(node: NodeSummary): Record<string, unknown> | null {
  const raw = (node.params ?? {}) as Record<string, unknown>;
  if (
    typeof raw.params === 'object' &&
    raw.params !== null &&
    !Array.isArray(raw.params)
  ) {
    return raw.params as Record<string, unknown>;
  }
  // Older / synthetic shapes where the params dict isn't wrapped.
  // Still recurse one level into them.
  return raw;
}

/** JSON-equality.  The substrate's bound values are always JSON-
 *  shaped on the wire so this is safe + cheap. */
function deepEqual(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (a == null || b == null) return false;
  if (typeof a !== typeof b) return false;
  if (typeof a !== 'object') return false;
  try {
    return JSON.stringify(a) === JSON.stringify(b);
  } catch {
    return false;
  }
}
