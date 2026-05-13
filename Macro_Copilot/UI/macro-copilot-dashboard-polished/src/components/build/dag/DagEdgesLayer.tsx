// ============================================================================
// DagEdgesLayer — SVG overlay drawing the workflow's edges.
// ----------------------------------------------------------------------------
// Renders the ``DagModel.edges`` as cubic-Bezier paths between the
// right-edge of each source card and the left-edge of each target
// card.  Layout is absolute over the canvas; the SVG dimensions are
// the same as the parent canvas div so coordinates align 1:1.
//
// Visual register matches the existing ``EdgeConnector``:
//   - violet lineage stroke
//   - small terminal arrowhead
//   - optional slot label rendered at the curve midpoint when the
//     "Labels on" control is active
//
// Edge styling
// ------------
//   - normal              → solid violet
//   - back-edge (cycle)   → dashed amber, surfaces alongside the
//                            cycle warning banner
//   - selected-source     → brighter ice stroke when the edge's
//                            source or target is the selected node
// ============================================================================

import { useMemo } from 'react';
import type { DagModel } from './lib/buildDagModel';

const ARROW_LENGTH = 6;
const ARROW_WIDTH = 5;
/** Approximate height of the node card so we anchor edges at the
 *  vertical centre.  Kept here (rather than imported from DagView)
 *  so the SVG layer stays self-contained. */
const NODE_PORT_INSET_Y = 78;

type Props = {
  model: DagModel;
  /** Pixel coordinates of the top-left corner of each node card. */
  nodeCoords: Map<string, { x: number; y: number }>;
  cardWidth: number;
  laneHeight: number;
  canvasWidth: number;
  canvasHeight: number;
  showLabels: boolean;
  selectedNodeId: string | null;
};

export function DagEdgesLayer({
  model,
  nodeCoords,
  cardWidth,
  laneHeight,
  canvasWidth,
  canvasHeight,
  showLabels,
  selectedNodeId,
}: Props) {
  const paths = useMemo(() => {
    const out: Array<{
      id: string;
      d: string;
      labelPos: { x: number; y: number };
      slot: string;
      isBack: boolean;
      isSelected: boolean;
    }> = [];
    for (const edge of model.edges) {
      const from = nodeCoords.get(edge.from);
      const to = nodeCoords.get(edge.to);
      if (!from || !to) continue;

      const fromAnchor = {
        x: from.x + cardWidth,
        y: from.y + NODE_PORT_INSET_Y,
      };
      const toAnchor = {
        x: to.x - ARROW_LENGTH, // leave room for the arrowhead
        y: to.y + NODE_PORT_INSET_Y,
      };

      // Horizontal-bias Bezier: control points pull the curve to be
      // mostly horizontal even when source + target are in different
      // lanes.  This keeps the edge close to the rank line.
      const dx = Math.max(40, (toAnchor.x - fromAnchor.x) * 0.45);
      const d = [
        `M ${fromAnchor.x} ${fromAnchor.y}`,
        `C ${fromAnchor.x + dx} ${fromAnchor.y},`,
        `${toAnchor.x - dx} ${toAnchor.y},`,
        `${toAnchor.x} ${toAnchor.y}`,
      ].join(' ');

      const labelX = (fromAnchor.x + toAnchor.x) / 2;
      const labelY = (fromAnchor.y + toAnchor.y) / 2 - 6;

      const isSelected =
        selectedNodeId !== null &&
        (selectedNodeId === edge.from || selectedNodeId === edge.to);

      out.push({
        id: `${edge.from}->${edge.to}::${edge.slotName}`,
        d,
        labelPos: { x: labelX, y: labelY },
        slot: edge.slotName,
        isBack: edge.isBackEdge,
        isSelected,
      });
    }
    return out;
  }, [model.edges, nodeCoords, cardWidth, selectedNodeId]);

  return (
    <svg
      className="pointer-events-none absolute inset-0"
      width={canvasWidth}
      height={canvasHeight}
      viewBox={`0 0 ${canvasWidth} ${canvasHeight}`}
      aria-hidden
    >
      <defs>
        <marker
          id="dag-arrow-normal"
          viewBox="0 0 10 10"
          refX="9"
          refY="5"
          markerWidth={ARROW_WIDTH}
          markerHeight={ARROW_WIDTH}
          orient="auto-start-reverse"
        >
          <path d="M 0 0 L 10 5 L 0 10 z" fill="rgba(155,140,255,0.75)" />
        </marker>
        <marker
          id="dag-arrow-selected"
          viewBox="0 0 10 10"
          refX="9"
          refY="5"
          markerWidth={ARROW_WIDTH}
          markerHeight={ARROW_WIDTH}
          orient="auto-start-reverse"
        >
          <path d="M 0 0 L 10 5 L 0 10 z" fill="rgba(122,162,255,0.95)" />
        </marker>
        <marker
          id="dag-arrow-back"
          viewBox="0 0 10 10"
          refX="9"
          refY="5"
          markerWidth={ARROW_WIDTH}
          markerHeight={ARROW_WIDTH}
          orient="auto-start-reverse"
        >
          <path d="M 0 0 L 10 5 L 0 10 z" fill="rgba(243,183,85,0.85)" />
        </marker>
      </defs>

      {paths.map((p) => {
        const stroke = p.isBack
          ? 'rgba(243,183,85,0.7)'
          : p.isSelected
            ? 'rgba(122,162,255,0.85)'
            : 'rgba(155,140,255,0.55)';
        const marker = p.isBack
          ? 'url(#dag-arrow-back)'
          : p.isSelected
            ? 'url(#dag-arrow-selected)'
            : 'url(#dag-arrow-normal)';
        const dash = p.isBack ? '4 3' : undefined;
        return (
          <g key={p.id}>
            <path
              d={p.d}
              fill="none"
              stroke={stroke}
              strokeWidth={p.isSelected ? 1.6 : 1.2}
              strokeDasharray={dash}
              markerEnd={marker}
            />
            {showLabels && p.slot.length > 0 && (
              <g>
                <rect
                  x={p.labelPos.x - measureLabelWidth(p.slot) / 2 - 3}
                  y={p.labelPos.y - 7}
                  width={measureLabelWidth(p.slot) + 6}
                  height={11}
                  rx={2}
                  fill="rgba(12,14,21,0.92)"
                  stroke="rgba(148,163,184,0.18)"
                  strokeWidth={0.5}
                />
                <text
                  x={p.labelPos.x}
                  y={p.labelPos.y + 1}
                  textAnchor="middle"
                  fontSize={9}
                  fontFamily="ui-monospace, monospace"
                  fill={p.isSelected ? '#A6C8FF' : '#9B8CFF'}
                >
                  {p.slot}
                </text>
              </g>
            )}
          </g>
        );
      })}

      {/* Reserved-mention suppress: lane height + laneHeight is used by
       *  upstream sibling computation so the typed props don't drift
       *  if a future refactor anchors edges at lane-relative positions. */}
      <g data-lane-height={laneHeight} />
    </svg>
  );
}

/** Cheap glyph-width estimate for the slot-name label background.
 *  Monospace + 9px → ~5.4px per character; round up so the
 *  background never under-shoots. */
function measureLabelWidth(s: string): number {
  return Math.ceil(s.length * 5.6);
}
