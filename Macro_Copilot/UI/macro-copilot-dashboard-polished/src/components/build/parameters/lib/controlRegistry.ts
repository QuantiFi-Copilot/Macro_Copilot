// ============================================================================
// controlRegistry.ts — pick a control renderer for a parameter descriptor.
// ----------------------------------------------------------------------------
// Mirrors the node-renderer registry pattern from PR A — one place
// where each ``ParamControlKind`` is registered against its
// renderer component.  Consuming code (``ParameterControlSwitch``)
// is branchless.
//
// Renderer contract
// -----------------
// Every control renderer receives the same shape:
//
//   {
//     descriptor: ParamControlDescriptor,
//     override: ParamOverride | undefined,
//     onChange: (value: unknown | undefined) => void,
//   }
//
// ``onChange(undefined)`` clears the override (revert to parent's
// value).  Other values are stored as the new override.  Controls
// don't need to know HOW their value is persisted — they hand the
// payload to the parent and the override-state reducer decides
// where it lands on the API patch.
// ============================================================================

import type { ComponentType } from 'react';
import type {
  ParamControlDescriptor,
  ParamControlKind,
  ParamOverride,
} from './controlSchema';

export interface ParamControlProps {
  descriptor: ParamControlDescriptor;
  override: ParamOverride | undefined;
  onChange: (value: unknown | undefined) => void;
}

export type ParamControl = ComponentType<ParamControlProps>;

const REGISTRY = new Map<ParamControlKind, ParamControl>();
// Placeholder fallback — replaced via ``registerFallbackControl`` once
// the controls/ module's index runs.
let FALLBACK: ParamControl = () => null;

export function registerControl(
  kind: ParamControlKind,
  control: ParamControl,
): void {
  REGISTRY.set(kind, control);
}

export function registerFallbackControl(control: ParamControl): void {
  FALLBACK = control;
}

export function resolveControl(kind: ParamControlKind): ParamControl {
  return REGISTRY.get(kind) ?? FALLBACK;
}

/** Diagnostic snapshot — used by tests + the catalogue page to
 *  confirm every closed-family kind has a registered renderer. */
export function controlRegistrySnapshot(): ParamControlKind[] {
  return Array.from(REGISTRY.keys()).sort();
}
