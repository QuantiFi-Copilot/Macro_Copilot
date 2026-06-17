// ============================================================================
// shared/build/lib/asOfDateControl.ts
// ----------------------------------------------------------------------------
// Canonical ControlDescriptor for the per-query ``as_of_date`` selector — the
// Build-surface analogue of the Monitor's ``AsOfControl``.  Every extended
// Build wrapper whose tool Input accepts ``as_of_date`` spreads this into its
// controls array so the control renders identically (label, kind, placement)
// across all primitives.
//
// Contract (matches the backend per-query input + the docs date-range
// contract): empty value → latest live data (no ``as_of_date`` sent); a
// YYYY-MM-DD value → compute as of that historical trade date (replayable).
// The ``date``-kind ControlField renders the native picker + a "Live" clear.
// ============================================================================

import type { ControlDescriptor } from './types';

/** Build the as-of-date control descriptor.  ``value`` is the current
 *  ``params.as_of_date`` (``undefined`` / empty → live).  Always a primary
 *  (always-visible) control — the replay affordance is a first-class knob,
 *  not an advanced override. */
export function asOfDateControl(value?: string): ControlDescriptor {
  return {
    name: 'as_of_date',
    label: 'As of',
    kind: 'date',
    value: value ?? '',
  };
}
