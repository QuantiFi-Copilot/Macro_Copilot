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
// canonical names in each template.yaml, with operator_name + tool_name
// fallbacks for legacy / renamed nodes.
// ---------------------------------------------------------------------------

/** PR2 — every role is now a rich binding shape, NOT a bare ``string[]``.
 *  The resolver tries each layer in order:
 *
 *    1. ``nodeIds``        — canonical IDs from each template.yaml.
 *                             Strongest match, used on every PR-B+ workspace.
 *    2. ``operatorNames``  — fallback for legacy workspaces where the
 *                             node ID has drifted but the operator name
 *                             is intact (per the substrate's
 *                             ``OperatorNode.operator_name`` invariant).
 *    3. ``toolNames``      — fallback for primitive-node roles where
 *                             the persisted ``tool_name`` identifies
 *                             the role even when the node ID was
 *                             renamed.  Useful for slot-driven node
 *                             names like ``${slot:signal_tool}``.
 *
 *  Each layer's matches are searched in declaration order.  The first
 *  hit wins; layers are stable so a deterministic resolution is
 *  guaranteed across runs. */
interface RoleBinding {
  nodeIds?: string[];
  operatorNames?: string[];
  toolNames?: string[];
}

type RoleBindingMap<Roles> = Record<keyof Roles & string, RoleBinding>;

const EVENT_STUDY_NODE_BINDING: RoleBindingMap<EventStudyRoles> = {
  signal: {
    nodeIds: ['signal', 'source_signal'],
    // signal is a PrimitiveNode with a slot-substituted tool name —
    // there's no fixed tool_name fallback we can pin without
    // overshadowing the target.
  },
  target: {
    nodeIds: ['target', 'source_target'],
  },
  align: {
    nodeIds: ['align'],
    operatorNames: ['align_series'],
  },
  events: {
    nodeIds: ['events', 'threshold_events'],
    operatorNames: ['threshold_events'],
  },
  windows: {
    nodeIds: ['windows', 'event_windows'],
    operatorNames: ['event_windows'],
  },
  conditionalAggregate: {
    nodeIds: ['aggregate', 'conditional_aggregate'],
    operatorNames: ['conditional_aggregate'],
  },
  unconditionalAggregate: {
    nodeIds: ['unconditional_aggregate'],
  },
  output: {
    nodeIds: ['compare', 'abnormal', 'output'],
    operatorNames: ['series_arithmetic'],
  },
};

const REGIME_NODE_BINDING: RoleBindingMap<RegimeRoles> = {
  lhs: { nodeIds: ['lhs', 'dependent'] },
  rhs: { nodeIds: ['rhs', 'explanatory'] },
  regimeSignal: { nodeIds: ['regime_signal'] },
  relationship: {
    nodeIds: ['relationship', 'rolling_regression'],
    operatorNames: ['rolling_regression'],
  },
  beta: { nodeIds: ['beta'], operatorNames: ['select_from_series_set'] },
  highMask: { nodeIds: ['high_mask'] },
  lowMask: { nodeIds: ['low_mask'] },
  highSummary: {
    nodeIds: ['high_summary'],
    operatorNames: ['summarize_series'],
  },
  lowSummary: {
    nodeIds: ['low_summary'],
    operatorNames: ['summarize_series'],
  },
  output: {
    nodeIds: ['compare'],
    operatorNames: ['series_arithmetic'],
  },
};

const BACKTEST_NODE_BINDING: RoleBindingMap<BacktestRoles> = {
  signal: { nodeIds: ['signal'] },
  events: {
    nodeIds: ['events'],
    operatorNames: ['threshold_events'],
  },
  trades: {
    nodeIds: ['trades'],
    operatorNames: ['construct_trades'],
  },
  pricePanel: {
    nodeIds: ['price_panel'],
    toolNames: ['build_sovereign_yield_panel_tool'],
  },
  financing: {
    nodeIds: ['financing'],
    toolNames: ['compute_financing_rate_tool'],
  },
  evaluate: {
    nodeIds: ['evaluate'],
    operatorNames: ['evaluate_trades'],
  },
  summarize: {
    nodeIds: ['summarize'],
    operatorNames: ['summarize_trades'],
  },
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
 *  gets typed roles back.
 *
 *  PR2 — the walk now has three layers (see ``RoleBinding`` doc):
 *  ``nodeIds`` → ``operatorNames`` → ``toolNames``.  Within a layer
 *  we walk candidates in declaration order and take the first
 *  unused match; once a layer yields a hit, later layers are
 *  skipped.  A node can fill at most one role per resolver run; the
 *  ``usedNodeIds`` set prevents two roles from both binding to the
 *  same node, which is the failure mode that caused regime + event-
 *  study dashboards to attribute a single ``summarize_series`` node
 *  to both ``highSummary`` and ``lowSummary`` before this PR.
 */
function resolveBy<Roles>(
  workspace: Pick<WorkspaceDetail, 'nodes' | 'focus_node'>,
  binding: RoleBindingMap<Roles>,
  requiredRoleKeys: string[],
): WorkflowArtifactMap<Roles> {
  const byId = new Map<string, NodeSummary>();
  for (const n of workspace.nodes) byId.set(n.node_id, n);

  // PR2 — gather every node_id that ANY role canonically claims.
  // Layer 2 + Layer 3 fallbacks ignore these reserved IDs, which
  // prevents the regression where ``low_summary`` (canonical for
  // ``lowSummary``) was being captured by a layer-2 fallback for
  // ``highSummary`` when ``high_summary`` was missing.  A node
  // that names itself canonically for one role is NEVER acceptable
  // collateral for another.
  const reservedNodeIds = new Set<string>();
  for (const key of Object.keys(binding) as Array<keyof Roles & string>) {
    for (const id of binding[key].nodeIds ?? []) {
      reservedNodeIds.add(id);
    }
  }

  const roles: Record<string, NodeSummary | null> = {};
  const usedNodeIds = new Set<string>();

  const pickFirstFreeMatch = (
    predicate: (node: NodeSummary) => boolean,
    ownRoleNodeIds: string[] | undefined,
  ): NodeSummary | null => {
    const allow = new Set<string>(ownRoleNodeIds ?? []);
    for (const n of workspace.nodes) {
      if (usedNodeIds.has(n.node_id)) continue;
      // Skip nodes reserved by a DIFFERENT role's canonical
      // binding — that node belongs to the other role even if it
      // shares operator/tool names with this one.
      if (reservedNodeIds.has(n.node_id) && !allow.has(n.node_id)) continue;
      if (predicate(n)) return n;
    }
    return null;
  };

  for (const key of Object.keys(binding) as Array<keyof Roles & string>) {
    const candidates: RoleBinding = binding[key];
    let resolved: NodeSummary | null = null;

    // Special-case ``output`` — if the workspace explicitly sets
    // ``focus_node`` to something different from the canonical
    // candidates, prefer the focus_node.  Keeps the dashboard
    // honest when a template variant terminates at a renamed node.
    if (
      key === 'output' &&
      workspace.focus_node &&
      byId.has(workspace.focus_node) &&
      !usedNodeIds.has(workspace.focus_node)
    ) {
      resolved = byId.get(workspace.focus_node) ?? null;
    }

    // Layer 1 — canonical node IDs.
    if (!resolved && candidates.nodeIds) {
      for (const candidateId of candidates.nodeIds) {
        if (usedNodeIds.has(candidateId)) continue;
        const node = byId.get(candidateId);
        if (node) {
          resolved = node;
          break;
        }
      }
    }

    // Layer 2 — operator_name fallback.
    if (!resolved && candidates.operatorNames) {
      for (const opName of candidates.operatorNames) {
        const node = pickFirstFreeMatch(
          (n) =>
            typeof n.params?.operator_name === 'string' &&
            (n.params.operator_name as string) === opName,
          candidates.nodeIds,
        );
        if (node) {
          resolved = node;
          break;
        }
      }
    }

    // Layer 3 — tool_name fallback (primitive nodes).
    if (!resolved && candidates.toolNames) {
      for (const toolName of candidates.toolNames) {
        const node = pickFirstFreeMatch(
          (n) =>
            typeof n.params?.tool_name === 'string' &&
            (n.params.tool_name as string) === toolName,
          candidates.nodeIds,
        );
        if (node) {
          resolved = node;
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
