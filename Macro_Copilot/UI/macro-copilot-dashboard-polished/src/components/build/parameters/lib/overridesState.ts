// ============================================================================
// overridesState.ts — reducer + helpers for the pending-overrides queue.
// ----------------------------------------------------------------------------
// The Parameters tab tracks pending overrides client-side (no API
// hits until the user clicks "Apply").  This file isolates the
// state shape + reducer so any consumer (the panel, the pending bar,
// the chat-side rerun handler) reads from the same source of truth.
//
// Override identity
// -----------------
// Each override is keyed by its full path (``"slot"`` for scalars,
// ``"slot.field"`` for dict-merge entries).  Re-applying the same
// override (same path, new value) replaces the previous entry.
// Setting a value back to the parent's original CLEARS the entry
// — the override-state never carries a "no-op" override.
//
// The reducer is intentionally NOT a useReducer hook; it's a plain
// function so it's reusable + unit-testable independent of React.
// The parent component owns the state via ``useReducer(reducer, ...)``.
// ============================================================================

import type {
  OverrideMap,
  ParamControlDescriptor,
  ParamOverride,
} from './controlSchema';
import { overrideKey } from './controlSchema';

export type OverridesAction =
  | {
      type: 'set';
      descriptor: ParamControlDescriptor;
      value: unknown;
    }
  | {
      type: 'clear';
      descriptor: ParamControlDescriptor;
    }
  | { type: 'reset' };

export function overridesReducer(
  state: OverrideMap,
  action: OverridesAction,
): OverrideMap {
  switch (action.type) {
    case 'reset':
      return {};

    case 'clear': {
      const k = overrideKey(action.descriptor.path);
      if (!(k in state)) return state;
      const next = { ...state };
      delete next[k];
      return next;
    }

    case 'set': {
      const { descriptor, value } = action;
      const k = overrideKey(descriptor.path);

      // Setting back to the parent's value clears instead of
      // recording — keeps the queue free of no-op overrides.
      if (
        deepEqual(value, descriptor.currentValue) ||
        // Empty-string / undefined → treat as clear for scalar
        // text inputs that get blanked.
        value === undefined ||
        value === null ||
        value === ''
      ) {
        if (!(k in state)) return state;
        const next = { ...state };
        delete next[k];
        return next;
      }

      const previousValue = descriptor.currentValue;
      const entry: ParamOverride =
        descriptor.path.length === 1
          ? {
              mode: 'scalar',
              path: descriptor.path,
              value,
              previousValue,
            }
          : {
              mode: 'dict_field',
              path: descriptor.path,
              value,
              previousValue,
            };

      return { ...state, [k]: entry };
    }
  }
}

/** Translate the override map into the server's two-bucket patch
 *  shape: ``{slot_overrides, slot_dict_overrides}``.  Pure;
 *  consumed by the fork-button click handler. */
export function overridesToServerPatch(state: OverrideMap): {
  slot_overrides: Record<string, unknown>;
  slot_dict_overrides: Record<string, Record<string, unknown>>;
} {
  const scalar: Record<string, unknown> = {};
  const dict: Record<string, Record<string, unknown>> = {};
  for (const override of Object.values(state)) {
    if (override.mode === 'scalar') {
      scalar[override.path[0]] = override.value;
    } else {
      const [slot, field] = override.path;
      if (!(slot in dict)) dict[slot] = {};
      dict[slot][field] = override.value;
    }
  }
  return { slot_overrides: scalar, slot_dict_overrides: dict };
}

/** True when the override map is empty — used by the
 *  "Apply overrides" button to gate its enabled state. */
export function hasPendingOverrides(state: OverrideMap): boolean {
  return Object.keys(state).length > 0;
}

// ----------------------------------------------------------------------------
// Internals
// ----------------------------------------------------------------------------

function deepEqual(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (a == null || b == null) return false;
  if (typeof a !== typeof b) return false;
  if (typeof a !== 'object') return false;
  // JSON round-trip is sufficient for the scalar / shallow-dict
  // values we see in slot params; the substrate's bound values are
  // always JSON-shaped on the wire so this is safe.
  try {
    return JSON.stringify(a) === JSON.stringify(b);
  } catch {
    return false;
  }
}
