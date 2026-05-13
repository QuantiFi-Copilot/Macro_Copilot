// ============================================================================
// resolveWorkflowArtifacts.ts — semantic dashboard-role resolver.
// ----------------------------------------------------------------------------
// PR7 — the specialised dashboards (EventStudyDashboard,
// RegimeRelationshipDashboard, BacktestDashboard) consume this
// resolver instead of grepping through ``workspace.nodes`` inside
// JSX.  Given a workspace + dashboard kind, the resolver returns a
// semantic role map:
//
//   {
//     signal:               NodeSummary | null,
//     target:               NodeSummary | null,
//     events:               NodeSummary | null,
//     windows:              NodeSummary | null,
//     conditionalAggregate: NodeSummary | null,
//     unconditionalAggregate: NodeSummary | null,
//     output:               NodeSummary | null,
//     ...
//   }
//
// Each role is either:
//   - the persisted ``NodeSummary`` filling that role,
//   - ``null`` when the workflow doesn't define the role,
//   - ``null`` when the role IS defined but the workspace
//     hasn't persisted a node for it (rare but possible if
//     execution failed mid-DAG).
//
// The resolver also returns ``missingRequiredRoles`` so dashboards
// can render an explicit "execution incomplete" banner without
// pretending the missing artifact is present.
//
// Source of truth for role → node_id binding
// ------------------------------------------
// ``rates_agent/workflows/{event_study,regime_conditioned_relationship,
// backtest}/template.yaml`` — each template declares node_ids that
// match the canonical names used below.  Validated at PR review
// time; the registry's tests assert the binding via a fixture-
// driven check.
//
// Pure function — no React, no async — easy to test.
// ============================================================================

import type { NodeSummary, WorkspaceDetail } from '@/services/workspaceApi';
import type { DashboardKind } from './dashboardRegistry';

// ---------------------------------------------------------------------------
// Role taxonomy — one union per dashboard.  Adding a new role is a
// closed extension: add the literal here, fill it in the resolver
// branch, and consume it in the dashboard.
// ---------------------------------------------------------------------------

export interface EventStudyRoles {
  /** Source signal series — ``signal`` node (primitive). */
  signal: NodeSummary | null;
  /** Response target series — ``target`` node (primitive). */
  target: NodeSummary | null;
  /** Aligned input branch — ``align`` operator output (SeriesSet).
   *  Optional, may be missing in older / simplified bindings. */
  align: NodeSummary | null;
  /** Per-event mask — ``events`` node (EventSet). */
  events: NodeSummary | null;
  /** Per-event response matrix — ``windows`` node (WindowedPanel). */
  windows: NodeSummary | null;
  /** Conditional aggregate path — ``aggregate`` node (Series). */
  conditionalAggregate: NodeSummary | null;
  /** Unconditional baseline aggregate — ``unconditional_aggregate``. */
  unconditionalAggregate: NodeSummary | null;
  /** Abnormal response (conditional − unconditional) — ``compare`` /
   *  terminal node (Series). */
  output: NodeSummary | null;
}

export interface RegimeRoles {
  /** Dependent variable / LHS series. */
  lhs: NodeSummary | null;
  /** Explanatory / RHS series. */
  rhs: NodeSummary | null;
  /** Regime signal series. */
  regimeSignal: NodeSummary | null;
  /** Rolling relationship model output (SeriesSet). */
  relationship: NodeSummary | null;
  /** Selected beta coefficient Series. */
  beta: NodeSummary | null;
  /** High-regime mask. */
  highMask: NodeSummary | null;
  /** Low-regime mask. */
  lowMask: NodeSummary | null;
  /** High-regime summary Panel. */
  highSummary: NodeSummary | null;
  /** Low-regime summary Panel. */
  lowSummary: NodeSummary | null;
  /** Final comparison artifact — terminal node. */
  output: NodeSummary | null;
}

export interface BacktestRoles {
  /** Source signal series. */
  signal: NodeSummary | null;
  /** Event mask. */
  events: NodeSummary | null;
  /** Constructed trades (pre-evaluation TradeSet). */
  trades: NodeSummary | null;
  /** Price panel feeding evaluation. */
  pricePanel: NodeSummary | null;
  /** Financing-rate panel. */
  financing: NodeSummary | null;
  /** Evaluated trades (post-PnL TradeSet). */
  evaluate: NodeSummary | null;
  /** Trade summary Panel — terminal. */
  summarize: NodeSummary | null;
}

// ---------------------------------------------------------------------------
// Per-dashboard role binding — driven by ``node.node_id`` matching the
// canonical names in each template.yaml.  Add a fall-through to
// ``operator_name`` when the node_id naming convention drifts.
// ---------------------------------------------------------------------------

/** Canonical node_id mapping per dashboard kind.  Each entry maps
 *  a semantic role key to one or more candidate node_ids — the
 *  resolver picks the first matching node.  Multiple candidates let
 *  us tolerate small naming drift across template versions without
 *  rewriting the resolver per version.
 *
 *  All names verified against the corresponding template.yaml on
 *  the branch this PR targets.  ``terminal_node_id`` (per template)
 *  takes precedence over canonical ``output`` candidates when set
 *  on the workspace. */
const EVENT_STUDY_NODE_BINDING: Record<keyof EventStudyRoles, string[]> = {
  signal: ['signal'],
  target: ['target'],
  align: ['align'],
  events: ['events'],
  windows: ['windows'],
  conditionalAggregate: ['aggregate'],
  unconditionalAggregate: ['unconditional_aggregate'],
  output: ['compare', 'abnormal'],
};

const REGIME_NODE_BINDING: Record<keyof RegimeRoles, string[]> = {
  lhs: ['lhs'],
  rhs: ['rhs'],
  regimeSignal: ['regime_signal'],
  relationship: ['relationship'],
  beta: ['beta'],
  highMask: ['high_mask'],
  lowMask: ['low_mask'],
  highSummary: ['high_summary'],
  lowSummary: ['low_summary'],
  output: ['compare'],
};

const BACKTEST_NODE_BINDING: Record<keyof BacktestRoles, string[]> = {
  signal: ['signal'],
  events: ['events'],
  trades: ['trades'],
  pricePanel: ['price_panel'],
  financing: ['financing'],
  evaluate: ['evaluate'],
  summarize: ['summarize'],
};

/** Roles every supported dashboard considers "required" to call its
 *  surface complete.  Dashboards may render with some required
 *  roles absent (always falling back to honest "missing" callouts);
 *  this list exists so dashboards know what to badge as missing. */
const REQUIRED_ROLES: Record<DashboardKind, string[]> = {
  event_study: ['signal', 'target', 'events', 'windows', 'output'],
  regime_conditioned_relationship: [
    'lhs',
    'rhs',
    'regimeSignal',
    'relationship',
    'output',
  ],
  backtest: ['signal', 'events', 'trades', 'summarize'],
  generic: [],
};

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export interface WorkflowArtifactMap<Roles> {
  /** Per-role node binding.  ``null`` when the workspace doesn't
   *  persist a node for the role. */
  roles: Roles;
  /** Role keys that should have a node but don't — the dashboard
   *  surfaces these as explicit "missing" callouts. */
  missingRequiredRoles: string[];
  /** All nodes the resolver has NOT bound to a semantic role.  The
   *  dashboard may render these in an "Other artifacts" section so
   *  no node is silently dropped. */
  unboundNodes: NodeSummary[];
}

/** Resolve workspace nodes into the EventStudy role map. */
export function resolveEventStudyArtifacts(
  workspace: Pick<WorkspaceDetail, 'nodes' | 'focus_node'>,
): WorkflowArtifactMap<EventStudyRoles> {
  return resolveBy<EventStudyRoles>(
    workspace,
    EVENT_STUDY_NODE_BINDING,
    REQUIRED_ROLES.event_study,
  );
}

/** Resolve workspace nodes into the Regime role map. */
export function resolveRegimeArtifacts(
  workspace: Pick<WorkspaceDetail, 'nodes' | 'focus_node'>,
): WorkflowArtifactMap<RegimeRoles> {
  return resolveBy<RegimeRoles>(
    workspace,
    REGIME_NODE_BINDING,
    REQUIRED_ROLES.regime_conditioned_relationship,
  );
}

/** Resolve workspace nodes into the Backtest role map. */
export function resolveBacktestArtifacts(
  workspace: Pick<WorkspaceDetail, 'nodes' | 'focus_node'>,
): WorkflowArtifactMap<BacktestRoles> {
  return resolveBy<BacktestRoles>(
    workspace,
    BACKTEST_NODE_BINDING,
    REQUIRED_ROLES.backtest,
  );
}

// ---------------------------------------------------------------------------
// Internals
// ---------------------------------------------------------------------------

/** Generic implementation that does the role-binding walk.  Pure
 *  + typed via the role-record's keys, so each dashboard caller
 *  gets typed roles back. */
function resolveBy<Roles>(
  workspace: Pick<WorkspaceDetail, 'nodes' | 'focus_node'>,
  binding: Record<keyof Roles & string, string[]>,
  requiredRoleKeys: string[],
): WorkflowArtifactMap<Roles> {
  const byId = new Map<string, NodeSummary>();
  for (const n of workspace.nodes) byId.set(n.node_id, n);

  const roles: Record<string, NodeSummary | null> = {};
  const usedNodeIds = new Set<string>();
  for (const key of Object.keys(binding) as Array<keyof Roles & string>) {
    const candidates = binding[key];
    let resolved: NodeSummary | null = null;
    // Special-case ``output`` — if the workspace explicitly sets
    // ``focus_node`` to something different from the canonical
    // candidates, prefer the focus_node.  Keeps the dashboard
    // honest when a template variant terminates at a renamed node.
    if (
      key === 'output' &&
      workspace.focus_node &&
      byId.has(workspace.focus_node)
    ) {
      resolved = byId.get(workspace.focus_node)!;
    } else {
      for (const candidateId of candidates) {
        if (byId.has(candidateId)) {
          resolved = byId.get(candidateId)!;
          break;
        }
      }
    }
    roles[key] = resolved;
    if (resolved) usedNodeIds.add(resolved.node_id);
  }

  const missingRequiredRoles = requiredRoleKeys.filter(
    (k) => roles[k] === null || roles[k] === undefined,
  );

  const unboundNodes = workspace.nodes.filter(
    (n) => !usedNodeIds.has(n.node_id),
  );

  // The caller specifies the ``Roles`` shape; we've populated every
  // key from ``binding`` so the cast is safe.  TypeScript can't
  // verify this generically because ``Roles`` is opaque inside the
  // function body, but the per-caller wrappers above pin ``Roles``
  // exactly to their declared shape.
  return {
    roles: roles as unknown as Roles,
    missingRequiredRoles,
    unboundNodes,
  };
}
