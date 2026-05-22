// ============================================================================
// stageDisplay.ts — UI-friendly rendering helpers for workspace nodes.
// ----------------------------------------------------------------------------
// Pure functions that translate the substrate's wire shapes into
// strings the Build UI renders.  Lives separately from the React
// components so the same helpers can be unit-tested without React.
//
//   - ``prettyStageTitle``    : tool / operator name → "Calculate Curve
//                              Spread" style display title.
//   - ``stageKindLabel``      : ``"primitive"`` / ``"operator"`` → "Primitive"
//                              / "Operator" with a sane fallback.
//   - ``stageParamRows``      : extract a compact list of (label, value)
//                              rows from a node's ``params`` blob, for
//                              the inline params summary on each stage
//                              card (matches Mockup B's stage cards).
// ============================================================================

import type { NodeSummary } from '@/services/workspaceApi';

/** Convert a tool / operator name (lower-snake) into Title Case.
 *  Strips the legacy ``calculate_`` / ``get_`` / ``build_`` /
 *  ``compute_`` prefixes plus the trailing ``_tool`` suffix so the
 *  display title stays terse — ``calculate_curve_spread_tool`` →
 *  ``Curve Spread`` rather than ``Calculate Curve Spread Tool``. */
export function prettyStageTitle(name: string | null | undefined): string {
  if (!name) return 'Unknown node';
  let s = String(name);
  // Trailing ``_tool``.
  s = s.replace(/_tool$/i, '');
  // Common leading verbs the tool naming convention uses.
  s = s.replace(/^(calculate|compute|get|build|classify|run)_/i, '');
  // Snake → space.
  s = s.replace(/_/g, ' ').trim();
  // Special-case the substrate's ``OIS`` / ``UST`` / ``TIPS`` / etc.
  // tokens — uppercase them inside the title.
  const ACRONYMS = new Set(['ois', 'ust', 'tips', 'pca', 'ar', 'ols', 'sql']);
  return s
    .split(/\s+/)
    .map((tok) => {
      const lower = tok.toLowerCase();
      if (ACRONYMS.has(lower)) return lower.toUpperCase();
      return lower.charAt(0).toUpperCase() + lower.slice(1);
    })
    .join(' ');
}

/** ``"primitive"`` / ``"operator"`` → ``Primitive`` / ``Operator``.
 *  Used as the kicker on each stage card.  Falls back to the raw
 *  kind string when the substrate emits a future kind we don't
 *  recognise — better to show ``"composite"`` than ``"Unknown"``. */
export function stageKindLabel(kind: string | null | undefined): string {
  if (!kind) return 'Node';
  return kind.charAt(0).toUpperCase() + kind.slice(1);
}

/** One row in the params summary on a stage card. */
export interface StageParamRow {
  label: string;
  value: string;
}

/** Extract a compact list of ``(label, value)`` rows from a node's
 *  ``params`` dict for the inline params summary on each stage card.
 *
 *  Ordering rules:
 *    1. ``tool_name``, ``operator_name``, ``output_field`` and other
 *       schema-identifier fields are SKIPPED — those identify the
 *       node, not its bound params.
 *    2. Nested ``params`` (from the substrate's
 *       ``_workflow_node_params_for_storage`` shape) is unwrapped
 *       one level so the user sees the actual binding rather than a
 *       JSON blob.
 *    3. Scalar values render directly; lists render as comma-joined
 *       strings truncated to ~3 items; nested dicts render as
 *       ``"{<n> fields}"`` — the user can click into the node for
 *       the full view.
 *    4. ``maxRows`` (default 5) caps the visible row count so a
 *       wide-param node doesn't bloat its card.
 */
export function stageParamRows(
  node: Pick<NodeSummary, 'params'>,
  options?: { maxRows?: number },
): StageParamRow[] {
  const maxRows = options?.maxRows ?? 5;
  const raw = node.params ?? {};
  // Unwrap the nested ``params`` shape produced by
  // ``_workflow_node_params_for_storage`` so the display reads the
  // actual binding, not the metadata wrapper.
  const inner =
    raw && typeof raw === 'object' && 'params' in raw
      ? ((raw as Record<string, unknown>)['params'] as
          | Record<string, unknown>
          | undefined)
      : raw;

  if (!inner || typeof inner !== 'object') return [];

  const SKIP_KEYS = new Set(['tool_name', 'operator_name', 'output_field']);
  const rows: StageParamRow[] = [];

  for (const [key, value] of Object.entries(
    inner as Record<string, unknown>,
  )) {
    if (SKIP_KEYS.has(key)) continue;
    if (rows.length >= maxRows) break;
    rows.push({
      label: prettyParamKey(key),
      value: prettyParamValue(value),
    });
  }
  return rows;
}

function prettyParamKey(key: string): string {
  return key
    .split('_')
    .map((tok) => tok.charAt(0).toUpperCase() + tok.slice(1).toLowerCase())
    .join(' ');
}

function prettyParamValue(value: unknown): string {
  if (value == null) return '—';
  if (typeof value === 'number') {
    // Use up to 4 significant figures; integers stay as integers.
    return Number.isInteger(value) ? String(value) : value.toPrecision(4);
  }
  if (typeof value === 'string') return value;
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  if (Array.isArray(value)) {
    const shown = value.slice(0, 3).map((v) => prettyParamValue(v)).join(', ');
    const extra = value.length > 3 ? ` … +${value.length - 3}` : '';
    return `[${shown}${extra}]`;
  }
  if (typeof value === 'object') {
    const keys = Object.keys(value as Record<string, unknown>);
    return `{${keys.length} field${keys.length === 1 ? '' : 's'}}`;
  }
  return String(value);
}
