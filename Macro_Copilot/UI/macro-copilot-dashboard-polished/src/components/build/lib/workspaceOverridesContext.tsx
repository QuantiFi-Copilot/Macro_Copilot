// ============================================================================
// workspaceOverridesContext — shared override state for the slug shell.
// ----------------------------------------------------------------------------
// The Parameters tab used to own its override-state machine via a
// component-local ``useReducer``.  Phase 4 lifts that state into a
// React context that wraps the entire slug-bound shell so:
//
//   - ParametersView consumes overrides + dispatch from the context
//     instead of owning the reducer itself
//   - PendingOverridesBar reads the same state, regardless of which
//     tab the user is on
//   - WorkspaceCopilotRail can dispatch override actions from
//     assistant-emitted ``proposed_overrides`` payloads or from
//     user-clicked quick-action chips
//   - The "Apply & fork" pipeline lives here, so every consumer
//     calls one ``apply()`` rather than re-implementing the fork
//     POST + navigation
//
// The reducer + descriptor types live next to the parameters surface
// (parameters/lib/overridesState.ts).  This file is the React-side
// orchestration on top of that pure state machine.
// ============================================================================

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useReducer,
  useState,
  type ReactNode,
} from 'react';
import { useNavigate } from 'react-router-dom';
import {
  forkWorkspace,
  type WorkspaceDetail,
} from '@/services/workspaceApi';
import { useTools, useWorkflow } from '@/hooks/useWorkflows';
import {
  hasPendingOverrides,
  overridesReducer,
  overridesToServerPatch,
  type OverridesAction,
} from '@/components/build/parameters/lib/overridesState';
import {
  overrideKey,
  type OverrideMap,
  type ParamControlDescriptor,
  type ParamOverride,
} from '@/components/build/parameters/lib/controlSchema';
import {
  validateOverrides,
  type OverrideValidationError,
  type OverrideValidationResult,
} from '@/components/build/parameters/lib/validateOverrides';

// ----------------------------------------------------------------------------
// Context value shape
// ----------------------------------------------------------------------------

export interface WorkspaceOverridesValue {
  /** The workspace this provider is scoped to. */
  workspace: WorkspaceDetail;
  /** Current override map (path-keyed). */
  overrides: OverrideMap;
  /** True when at least one override is queued.  Memoised so the
   *  pending-bar and shell-level affordances don't recompute on every
   *  render. */
  hasPending: boolean;
  /** Raw reducer dispatcher.  Prefer the wrapper helpers below in
   *  normal code; this is exposed for the parameters surface's
   *  per-field ``ControlRow``. */
  dispatch: (action: OverridesAction) => void;
  /** True when an "Apply & fork" submission is in flight.  Used to
   *  disable both the PendingOverridesBar's button and any chip-driven
   *  apply UI so the user can't double-fork. */
  isApplying: boolean;
  /** Last fork-attempt error.  Cleared on a successful apply or on
   *  ``clearError()``. */
  error: string | null;
  /** PR3 — pure cross-slot validation result for the queued
   *  overrides (tool catalogue + bound slot values + schema).
   *  Consumed by ``PendingOverridesBar`` to gate the Apply button
   *  and surface per-chip inline errors.  ``ok === true`` means
   *  every queued override is consistent. */
  validation: OverrideValidationResult;
  /** True iff Apply should be enabled.  Computed off ``hasPending``,
   *  ``isApplying``, the forkable check, and ``validation.ok``.
   *  Centralised here so every consumer sees the same gate. */
  canApply: boolean;
  /** Submit the queued overrides as a fork.  On success navigates to
   *  the new workspace slug.  Rejects only on transport-level errors;
   *  domain errors set ``error`` and resolve normally.
   *  PR3 — rejects with a domain error when ``validation.ok === false``
   *  so a misconfigured override can never reach the backend. */
  apply: () => Promise<void>;
  /** Convenience wrapper: dispatch ``{type:'set', descriptor, value}``
   *  for an arbitrary descriptor.  Used by chat-driven overrides
   *  where the caller assembled the descriptor from a ``proposed_
   *  overrides`` payload. */
  setOverride: (descriptor: ParamControlDescriptor, value: unknown) => void;
  /** Convenience wrapper: dispatch ``{type:'clear', descriptor}``. */
  clearOverride: (descriptor: ParamControlDescriptor) => void;
  /** Reset every queued override. */
  resetAll: () => void;
  /** Clear the error banner without clearing the override queue. */
  clearError: () => void;
}

const WorkspaceOverridesContext =
  createContext<WorkspaceOverridesValue | null>(null);

// ----------------------------------------------------------------------------
// Provider
// ----------------------------------------------------------------------------

type ProviderProps = {
  workspace: WorkspaceDetail;
  children: ReactNode;
};

export function WorkspaceOverridesProvider({
  workspace,
  children,
}: ProviderProps) {
  const navigate = useNavigate();
  const [overrides, dispatch] = useReducer(overridesReducer, {});
  const [isApplying, setIsApplying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // PR3 — pull the workflow card + tool catalogue for cross-slot
  // validation.  Both gracefully degrade: missing card → skip slot-
  // existence check; missing tool catalogue → skip tool-name /
  // output-field cross-checks.  We don't fail closed on transient
  // network blips.
  const { data: card } = useWorkflow(workspace.template_id ?? null);
  const { data: tools } = useTools();

  const setOverride = useCallback(
    (descriptor: ParamControlDescriptor, value: unknown) => {
      dispatch({ type: 'set', descriptor, value });
    },
    [],
  );

  const clearOverride = useCallback(
    (descriptor: ParamControlDescriptor) => {
      dispatch({ type: 'clear', descriptor });
    },
    [],
  );

  const resetAll = useCallback(() => {
    dispatch({ type: 'reset' });
  }, []);

  // PR3 — derive validation off the latest snapshot.  Memoised so
  // consumers can re-render on each keystroke without re-validating
  // every queue entry.
  const knownSlotNames = useMemo<Set<string> | null>(() => {
    if (!card) return null;
    return new Set(card.slot_schema.map((s) => s.name));
  }, [card]);

  const validation = useMemo<OverrideValidationResult>(
    () =>
      validateOverrides({
        overrides,
        boundSlotValues: workspace.bound_slot_values,
        tools,
        knownSlotNames,
      }),
    [overrides, workspace.bound_slot_values, tools, knownSlotNames],
  );

  const isForkable = Boolean(
    workspace.template_id && workspace.bound_slot_values,
  );

  const apply = useCallback(async () => {
    if (!hasPendingOverrides(overrides)) return;
    if (!isForkable) {
      // PR-11C — open-DAG workspaces (template_id === null AND
      // bound_slot_values === null) get a distinct honest message
      // because they're non-forkable BY DESIGN, not by drift.
      const isOpenDag =
        workspace.template_id === null && workspace.bound_slot_values === null;
      setError(
        isOpenDag
          ? 'Open-DAG compositions are not slot-forkable (no template recipe).  Re-ask the original question in Ask with your change; the lane will compose a fresh DAG and persist a new workspace.'
          : 'This workspace pre-dates the fork substrate (no template_id / bound_slot_values).  Re-run the original prompt to make it forkable.',
      );
      return;
    }
    // PR3 — refuse to submit when the queue contains a cross-slot
    // inconsistency.  Picking the first error keeps the banner
    // terse; the per-chip inline messages already point users at
    // every individual problem.
    if (!validation.ok) {
      const first: OverrideValidationError | undefined = validation.errors[0];
      setError(
        first
          ? `Can't fork: ${first.message}`
          : `Can't fork: queued overrides have validation errors.`,
      );
      return;
    }
    setIsApplying(true);
    setError(null);
    try {
      const patch = overridesToServerPatch(overrides);
      const res = await forkWorkspace(workspace.slug, {
        slot_overrides: patch.slot_overrides,
        slot_dict_overrides: patch.slot_dict_overrides,
      });
      navigate(`/workspace/${res.slug}`);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setIsApplying(false);
    }
  }, [overrides, workspace, navigate, isForkable, validation]);

  const clearError = useCallback(() => setError(null), []);

  const hasPending = useMemo(
    () => hasPendingOverrides(overrides),
    [overrides],
  );

  const canApply =
    hasPending && !isApplying && isForkable && validation.ok;

  const value = useMemo<WorkspaceOverridesValue>(
    () => ({
      workspace,
      overrides,
      hasPending,
      dispatch,
      isApplying,
      error,
      validation,
      canApply,
      apply,
      setOverride,
      clearOverride,
      resetAll,
      clearError,
    }),
    [
      workspace,
      overrides,
      hasPending,
      isApplying,
      error,
      validation,
      canApply,
      apply,
      setOverride,
      clearOverride,
      resetAll,
      clearError,
    ],
  );

  return (
    <WorkspaceOverridesContext.Provider value={value}>
      {children}
    </WorkspaceOverridesContext.Provider>
  );
}

// ----------------------------------------------------------------------------
// Consumers
// ----------------------------------------------------------------------------

/** Required-context hook — throws when consumed outside the provider.
 *  Use this in components that are mounted only under a slug-bound
 *  workspace (ParametersView, PendingOverridesBar). */
export function useWorkspaceOverrides(): WorkspaceOverridesValue {
  const ctx = useContext(WorkspaceOverridesContext);
  if (!ctx) {
    throw new Error(
      'useWorkspaceOverrides must be used inside a WorkspaceOverridesProvider',
    );
  }
  return ctx;
}

/** Optional-context hook — returns null when the provider isn't
 *  mounted.  Use this in components that render in BOTH the empty
 *  shell (no workspace) and the slug-bound shell (workspace present),
 *  like the copilot rail. */
export function useOptionalWorkspaceOverrides(): WorkspaceOverridesValue | null {
  return useContext(WorkspaceOverridesContext);
}

// Re-export the descriptor + override types so downstream consumers
// don't reach across to the parameters/lib/ folder for them.
export type {
  OverrideMap,
  ParamOverride,
  ParamControlDescriptor,
  OverridesAction,
};
export { overrideKey };
