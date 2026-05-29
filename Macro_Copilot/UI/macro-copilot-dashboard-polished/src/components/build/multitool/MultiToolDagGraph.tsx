// ============================================================================
// MultiToolDagGraph — the layered flow-graph view of a multi-tool query.
// ----------------------------------------------------------------------------
// Stage D (DAG tab).  Renders the ``DagModel`` as a real topological DAG:
// a synthetic QUERY root fanning out to the tool calls, laid out in ranks
// ("levels") with greedy lane assignment, connected by cubic-Bezier edges
// with arrowheads.  Parallel queries show a clean fan-out (query → A, B, C);
// multi-step queries show the hierarchy (query → A, B → C) across LEVEL 1 /
// LEVEL 2 / LEVEL 3 columns.
//
// Tool-agnostic: node boxes derive their labels from the module registry; a
// click opens that tool's extended view in the shared modal.  The graph is a
// STRUCTURAL map (what called what) — the data lives on the Cards tab; this
// view answers "how does the query hang together".
//
// Composition mirrors the workflow DAG (src/components/build/dag): absolute-
// positioned node boxes over an SVG edge layer sized to the same canvas.
// ============================================================================

import { useMemo } from 'react';
import { ArrowUpRight, Sparkles } from 'lucide-react';
import { cn } from '@/utils/cn';
import type { DagModel } from './dagModel';
import { layoutDag, ROOT_ID, type LaidOutNode } from './dagLayout';
import { compactParamSummary, nodeDisplayName } from './nodeLabels';
import { useOpenExtendedView } from './expandedView';

const ARROW_LEN = 7;

export function MultiToolDagGraph({
  model,
  queryLabel,
}: {
  model: DagModel;
  /** Originating prompt — shown in the query-root node when present. */
  queryLabel?: string;
}) {
  const layout = useMemo(() => layoutDag(model), [model]);
  const { open, lastOpenedNodeId } = useOpenExtendedView();
  const { geom } = layout;

  const coordById = useMemo(() => {
    const m = new Map<number, LaidOutNode>();
    for (const n of layout.nodes) m.set(n.id, n);
    return m;
  }, [layout.nodes]);

  if (layout.nodes.length === 0) {
    return (
      <div className="flex h-full items-center justify-center text-[12.5px] text-fg-muted">
        Nothing to map.
      </div>
    );
  }

  // Per-rank column headers (QUERY · LEVEL 1 · LEVEL 2 …).
  const levelHeaders = Array.from({ length: layout.ranks }, (_, rank) => ({
    rank,
    x: geom.padX + rank * (geom.nodeW + geom.colGap),
    label: rank === 0 ? 'QUERY' : `LEVEL ${rank}`,
  }));

  return (
    <div className="relative h-full min-h-0 flex-1 overflow-auto bg-white/[0.008]">
      <div
        className="relative"
        style={{ width: layout.canvasWidth, height: layout.canvasHeight }}
      >
        {/* Per-level column headers. */}
        {levelHeaders.map((h) => (
          <div
            key={`hdr-${h.rank}`}
            className="absolute kicker text-fg-faint"
            style={{ left: h.x, top: 18, width: geom.nodeW }}
          >
            {h.label}
          </div>
        ))}

        {/* Edge layer. */}
        <DagEdges layout={layout} coordById={coordById} />

        {/* Node layer. */}
        {layout.nodes.map((laid) => {
          if (laid.kind === 'root') {
            return (
              <QueryRootNode
                key="root"
                x={laid.x}
                y={laid.y}
                width={geom.nodeW}
                height={geom.nodeH}
                queryLabel={queryLabel}
              />
            );
          }
          const node = laid.node;
          if (!node) return null;
          return (
            <ToolNode
              key={node.id}
              x={laid.x}
              y={laid.y}
              width={geom.nodeW}
              height={geom.nodeH}
              displayName={nodeDisplayName(node.toolName)}
              summary={compactParamSummary(node.params)}
              errored={node.status === 'error'}
              domain={node.domain}
              callMeta={node.callMeta}
              active={node.id === lastOpenedNodeId}
              onOpen={() => open(node.decoded, node.id)}
            />
          );
        })}
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------------
// SVG edge layer — cubic-Bezier curves with arrowheads.
// ----------------------------------------------------------------------------

function DagEdges({
  layout,
  coordById,
}: {
  layout: ReturnType<typeof layoutDag>;
  coordById: Map<number, LaidOutNode>;
}) {
  const { geom } = layout;
  const paths = useMemo(() => {
    const out: Array<{
      id: string;
      d: string;
      isRoot: boolean;
      isBack: boolean;
      label: string;
      labelPos: { x: number; y: number };
    }> = [];
    for (const edge of layout.edges) {
      const from = coordById.get(edge.from);
      const to = coordById.get(edge.to);
      if (!from || !to) continue;
      const fromAnchor = { x: from.x + geom.nodeW, y: from.y + geom.nodeH / 2 };
      const toAnchor = { x: to.x - ARROW_LEN, y: to.y + geom.nodeH / 2 };
      const dx = Math.max(40, (toAnchor.x - fromAnchor.x) * 0.45);
      const d = [
        `M ${fromAnchor.x} ${fromAnchor.y}`,
        `C ${fromAnchor.x + dx} ${fromAnchor.y},`,
        `${toAnchor.x - dx} ${toAnchor.y},`,
        `${toAnchor.x} ${toAnchor.y}`,
      ].join(' ');
      out.push({
        id: `${edge.from}->${edge.to}`,
        d,
        isRoot: edge.isRootEdge,
        isBack: edge.isBackEdge,
        label: edge.label,
        labelPos: {
          x: (fromAnchor.x + toAnchor.x) / 2,
          y: (fromAnchor.y + toAnchor.y) / 2 - 6,
        },
      });
    }
    return out;
  }, [layout.edges, coordById, geom]);

  return (
    <svg
      className="pointer-events-none absolute inset-0"
      width={layout.canvasWidth}
      height={layout.canvasHeight}
      viewBox={`0 0 ${layout.canvasWidth} ${layout.canvasHeight}`}
      aria-hidden
    >
      <defs>
        <marker
          id="mt-dag-arrow"
          viewBox="0 0 10 10"
          refX="9"
          refY="5"
          markerWidth={5}
          markerHeight={5}
          orient="auto-start-reverse"
        >
          <path d="M 0 0 L 10 5 L 0 10 z" fill="rgba(155,140,255,0.8)" />
        </marker>
        <marker
          id="mt-dag-arrow-back"
          viewBox="0 0 10 10"
          refX="9"
          refY="5"
          markerWidth={5}
          markerHeight={5}
          orient="auto-start-reverse"
        >
          <path d="M 0 0 L 10 5 L 0 10 z" fill="rgba(243,183,85,0.85)" />
        </marker>
      </defs>
      {paths.map((p) => {
        if (p.isRoot) {
          // Query fan-out: light, dashed, no arrowhead — it's "spawned by",
          // not a data dependency.
          return (
            <path
              key={p.id}
              d={p.d}
              fill="none"
              stroke="rgba(148,163,184,0.30)"
              strokeWidth={1}
              strokeDasharray="3 3"
            />
          );
        }
        const stroke = p.isBack
          ? 'rgba(243,183,85,0.7)'
          : 'rgba(155,140,255,0.6)';
        return (
          <g key={p.id}>
            <path
              d={p.d}
              fill="none"
              stroke={stroke}
              strokeWidth={1.4}
              strokeDasharray={p.isBack ? '4 3' : undefined}
              markerEnd={p.isBack ? 'url(#mt-dag-arrow-back)' : 'url(#mt-dag-arrow)'}
            />
            {p.label && (
              <g>
                <rect
                  x={p.labelPos.x - measureLabelWidth(p.label) / 2 - 3}
                  y={p.labelPos.y - 7}
                  width={measureLabelWidth(p.label) + 6}
                  height={12}
                  rx={2}
                  fill="rgba(12,14,21,0.92)"
                  stroke="rgba(148,163,184,0.18)"
                  strokeWidth={0.5}
                />
                <text
                  x={p.labelPos.x}
                  y={p.labelPos.y + 1.5}
                  textAnchor="middle"
                  fontSize={9}
                  fontFamily="ui-monospace, monospace"
                  fill="#9B8CFF"
                >
                  {p.label}
                </text>
              </g>
            )}
          </g>
        );
      })}
    </svg>
  );
}

function measureLabelWidth(s: string): number {
  return Math.ceil(s.length * 5.6);
}

// ----------------------------------------------------------------------------
// Node boxes.
// ----------------------------------------------------------------------------

function QueryRootNode({
  x,
  y,
  width,
  height,
  queryLabel,
}: {
  x: number;
  y: number;
  width: number;
  height: number;
  queryLabel?: string;
}) {
  return (
    <div
      className="absolute z-[1] flex flex-col justify-center gap-1.5 rounded-[12px] border border-ice-400/30 bg-ice-400/[0.07] px-4 py-3"
      style={{ left: x, top: y, width, minHeight: height }}
    >
      <span className="inline-flex items-center gap-1.5 kicker text-ice-200">
        <Sparkles size={11} strokeWidth={2} aria-hidden />
        Your query
      </span>
      <span className="line-clamp-2 text-[11.5px] leading-snug text-fg-secondary">
        {queryLabel ? `“${queryLabel}”` : 'Stitched into the tool calls →'}
      </span>
    </div>
  );
}

function ToolNode({
  x,
  y,
  width,
  height,
  displayName,
  summary,
  errored,
  domain,
  callMeta,
  active,
  onOpen,
}: {
  x: number;
  y: number;
  width: number;
  height: number;
  displayName: string;
  summary: string;
  errored: boolean;
  domain?: string;
  callMeta?: { n: number; m: number };
  active: boolean;
  onOpen: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onOpen}
      title={`Open ${displayName} in full view`}
      className={cn(
        'group absolute z-[1] flex flex-col gap-1.5 rounded-[12px] border px-3.5 py-3 text-left transition-colors',
        active
          ? 'border-ice-400/45 bg-ice-400/[0.08] ring-1 ring-ice-400/30'
          : 'border-line-soft bg-[rgba(16,18,25,0.85)] hover:border-line-strong hover:bg-white/[0.03]',
      )}
      style={{ left: x, top: y, width, minHeight: height }}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="flex min-w-0 items-center gap-1.5">
          <span
            aria-hidden
            className={cn(
              'h-1.5 w-1.5 shrink-0 rounded-full',
              errored
                ? 'bg-coral-300 shadow-[0_0_6px_rgba(248,113,113,0.6)]'
                : 'bg-ice-300 shadow-[0_0_6px_rgba(122,162,255,0.6)]',
            )}
          />
          <span className="truncate text-[12.5px] font-medium tracking-[-0.005em] text-fg-primary">
            {displayName}
          </span>
        </span>
        <ArrowUpRight
          size={13}
          strokeWidth={2}
          aria-hidden
          className="shrink-0 text-fg-faint transition-colors group-hover:text-ice-200"
        />
      </div>
      {summary && (
        <span className="truncate font-mono text-[10.5px] text-fg-muted">
          {summary}
        </span>
      )}
      <div className="mt-auto flex items-center gap-1.5">
        {domain && (
          <span className="rounded-full bg-white/[0.05] px-1.5 py-[1px] text-[9px] uppercase tracking-[0.06em] text-fg-faint">
            {domain}
          </span>
        )}
        {callMeta && (
          <span className="rounded-full bg-white/[0.06] px-1.5 py-[1px] font-mono text-[9px] text-fg-faint">
            call {callMeta.n}/{callMeta.m}
          </span>
        )}
        {errored && (
          <span className="rounded-full bg-coral-400/[0.12] px-1.5 py-[1px] text-[9px] uppercase tracking-[0.06em] text-coral-300">
            errored
          </span>
        )}
      </div>
    </button>
  );
}
