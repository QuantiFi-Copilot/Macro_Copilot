// ============================================================================
// deriveControlsForStage.ts — extract editable params from a NodeSummary.
// ----------------------------------------------------------------------------
// Heart of the "no hardcoded tool knowledge" discipline.  Given a
// persisted node's ``params`` blob (from ``WorkspaceDetail``), this
// helper returns an ordered list of ``ParamControlDescriptor``
// objects the editor renders.
//
// Resolution rules
// ----------------
// 1. The substrate persists node params in two shapes (see
//    ``state/dag_repo.py::_workflow_node_params_for_storage``):
//      - PrimitiveNode → ``{tool_name, output_field, params}``
//      - OperatorNode → ``{operator_name, params}``
//    We unwrap the nested ``params`` dict and walk its keys.
//
// 2. Each top-level key is classified into a ``ParamControlKind``
//    via name-pattern matching:
//      - ``*curve_family*``               → 'curve_family'
//      - ``*_tenor`` / ``tenor``          → 'tenor'
//      - ``*lookback_days``               → 'lookback_days'
//      - ``*window_days``                 → 'window_days'
//      - ``field_name`` / ``*_field_name``→ 'field_name'
//      - ``threshold`` / ``*_threshold``  → 'threshold'
//      - ``*_date`` / ``start_date`` / etc→ 'date'
//      - ``*_tool_name``                  → 'tool_name'
//      - ``*_output_field``               → 'output_field'
//      - typeof value === 'boolean'       → 'boolean'
//      - typeof value === 'number'        → 'numeric'
//      - typeof value === 'object'        → 'readonly_json'
//      - else                             → 'string'
//
// 3. Nested-dict values (e.g. ``signal_params``) are expanded one
//    level so each inner field becomes its own descriptor with a
//    two-segment path ``[slot, field]``.  Deeper nesting falls
//    through to ``readonly_json`` so the user can at least inspect
//    the shape.
//
// 4. Substrate identifier fields (``tool_name`` on PrimitiveNode,
//    ``operator_name`` on OperatorNode, ``output_field``) are
//    rendered as READ-ONLY descriptors — those identify the node,
//    they aren't methodology knobs.
//
// Pure function — no React imports, no async — so it's
// straightforward to unit-test.
// ============================================================================

import type { NodeSummary } from '@/services/workspaceApi';
import type {
  ParamControlDescriptor,
  ParamControlKind,
  ParamControlMeta,
} from './controlSchema';

const KNOWN_CURVE_FAMILIES = [
  'UST',
  'DE_BUND',
  'UK_GILT',
  'JGB',
  'FR_OAT',
  'IT_BTP',
  'ES_BONO',
  'AU_GOVT',
  'CANADA_GOVT',
  'USD_SOFR_OIS',
  'EUR_ESTR_OIS',
  'GBP_SONIA_OIS',
  'JPY_OIS',
  'AUD_OIS',
  'CAD_OIS',
  'USD_TIPS',
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
const KNOWN_BLOOMBERG_FIELDS = [
  'YLD_YTM_MID',
  'YLD_YTM_LAST',
  'PX_MID',
  'PX_LAST',
  'PX_BID',
  'PX_ASK',
];

const READ_ONLY_KEYS = new Set([
  'tool_name',
  'operator_name',
  'output_field',
]);

/** Top-level entry point — returns ordered descriptors for one node. */
export function deriveControlsForStage(
  node: NodeSummary,
): ParamControlDescriptor[] {
  const raw = (node.params ?? {}) as Record<string, unknown>;
  // Unwrap the substrate's wrapping shape.  When ``raw.params`` is a
  // dict, the actual params dict lives there; otherwise raw itself
  // is the params dict (older shapes / synthetic test fixtures).
  const inner =
    typeof raw.params === 'object' && raw.params !== null
      ? (raw.params as Record<string, unknown>)
      : raw;

  const descriptors: ParamControlDescriptor[] = [];

  // 1. Identity fields — render as read-only descriptors when
  //    present on the wrapping shape.  Skip when the raw blob has
  //    no wrapping (synthetic test data).
  for (const idKey of ['tool_name', 'operator_name', 'output_field']) {
    if (idKey in raw && typeof raw[idKey] === 'string') {
      descriptors.push({
        path: [idKey],
        label: prettyKey(idKey),
        meta: { kind: 'string' },
        currentValue: raw[idKey],
        readOnly: true,
        helpText: identityHelpText(idKey),
      });
    }
  }

  // 2. Walk the inner params dict.  Each entry produces one
  //    descriptor (or several, when the value is a nested dict).
  for (const [key, value] of Object.entries(inner)) {
    if (READ_ONLY_KEYS.has(key)) continue; // already handled above

    if (isPlainObject(value)) {
      // Expand one level — each nested field becomes its own
      // descriptor with a two-segment path.
      for (const [nestedKey, nestedValue] of Object.entries(value)) {
        descriptors.push(
          buildDescriptor(
            [key, nestedKey],
            nestedValue,
            `Inside ${prettyKey(key)}.`,
          ),
        );
      }
      continue;
    }

    descriptors.push(buildDescriptor([key], value));
  }

  return descriptors;
}

// ----------------------------------------------------------------------------
// Internals
// ----------------------------------------------------------------------------

function buildDescriptor(
  path: [string] | [string, string],
  currentValue: unknown,
  parentHelpText?: string,
): ParamControlDescriptor {
  const leaf = path[path.length - 1];
  const kind = classifyKind(leaf, currentValue);
  const meta = buildMeta(kind, leaf, currentValue);
  return {
    path,
    label: prettyKey(leaf),
    helpText: parentHelpText ?? helpForLeaf(leaf, kind),
    meta,
    currentValue,
  };
}

function classifyKind(
  leaf: string,
  value: unknown,
): ParamControlKind {
  const lower = leaf.toLowerCase();

  // Most-specific name patterns first.
  if (lower.includes('curve_family')) return 'curve_family';
  if (lower === 'tenor' || lower.endsWith('_tenor')) return 'tenor';
  if (lower.endsWith('lookback_days') || lower === 'lookback_days')
    return 'lookback_days';
  if (lower.endsWith('window_days') || lower === 'window_days')
    return 'window_days';
  if (lower === 'field_name' || lower.endsWith('_field_name'))
    return 'field_name';
  if (lower === 'threshold' || lower.endsWith('_threshold'))
    return 'threshold';
  if (
    lower === 'start_date' ||
    lower === 'end_date' ||
    lower.endsWith('_date')
  )
    return 'date';
  if (lower.endsWith('_tool_name')) return 'tool_name';
  if (lower.endsWith('_output_field')) return 'output_field';

  // Type-based fallbacks.
  if (typeof value === 'boolean') return 'boolean';
  if (typeof value === 'number') return 'numeric';
  if (isPlainObject(value)) return 'readonly_json';
  return 'string';
}

function buildMeta(
  kind: ParamControlKind,
  leaf: string,
  value: unknown,
): ParamControlMeta {
  switch (kind) {
    case 'curve_family': {
      // Sovereign vs OIS lookup heuristic — purely UX-side, the
      // server doesn't enforce.  Editor uses this hint to filter
      // the dropdown when the slot name implies one or the other.
      const lower = leaf.toLowerCase();
      const allowed: Array<'sovereign' | 'ois'> = [];
      if (lower.includes('ois')) allowed.push('ois');
      if (lower.includes('sovereign')) allowed.push('sovereign');
      // Default to both when ambiguous.
      if (allowed.length === 0) {
        allowed.push('sovereign', 'ois');
      }
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
      // The deriver never produces 'enum' directly — kept here for
      // forward-compat when a future pre-resolved schema is added.
      return { kind, options: [] };
    case 'string':
      return { kind };
    case 'readonly_json':
      return { kind };
  }
}

export function knownCurveFamilies(): string[] {
  return [...KNOWN_CURVE_FAMILIES];
}

export function commonTenors(): string[] {
  return [...COMMON_TENORS];
}

export function knownBloombergFields(): string[] {
  return [...KNOWN_BLOOMBERG_FIELDS];
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

function helpForLeaf(leaf: string, kind: ParamControlKind): string | undefined {
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
  if (lower.startsWith('start_')) return 'Earliest date in the analysis window.';
  if (lower.startsWith('end_')) return 'Latest date in the analysis window.';
  return undefined;
}

function identityHelpText(idKey: string): string {
  switch (idKey) {
    case 'tool_name':
      return 'The primitive this stage runs.  Read-only — changing it would alter the DAG topology.';
    case 'operator_name':
      return 'The operator this stage runs.  Read-only — changing it would alter the DAG topology.';
    case 'output_field':
      return 'The primitive output the bridge lifts as the stage artifact.  Read-only at this surface.';
    default:
      return '';
  }
}
