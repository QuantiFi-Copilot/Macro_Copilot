// ============================================================================
// DagView — branch-aware workflow anatomy view.
// ----------------------------------------------------------------------------
// PR6 — replaces the pre-PR6 linear strip with a faithful branch-aware
// rendering driven by ``buildDagModel(detail)``.  Rendering shape:
//
//   ┌── controls bar (density · labels · fit) ─────────────────────┐
//   │ ┌── warning banner(s) (cycle / missing edges / orphans) ──┐  │
//   │ ┌── DAG canvas ─────────────────────────────────────────┐ │  │
//   │ │  SVG edge layer (Bezier curves, slot labels)            │ │
//   │ │  CSS grid: rows = lanes, cols = ranks                   │ │
//   │ │  Stage cards positioned via gridRow/gridColumn          │ │
//   │ └─────────────────────────────────────────────────────────┘ │
//   │ ┌── selected-node inspector (collapsible) ────────────────┐  │
//   │ │  node identity · params · inputs · outputs · artifact   │  │
//   │ └─────────────────────────────────────────────────────────┘  │
//   └──────────────────────────────────────────────────────────────┘
//
// Why a hand-rolled layout (no react-flow / dagre / elkjs)
// --------------------------------------------------------
// The workflow templates this surface targets (event_study,
// regime_conditioned_relationship, multi-primitive Ask handoffs)
// max out at ~10 nodes / 3 lanes / 6 ranks.  A full graph library
// would add ~150 KB to the bundle for a use case we can solve in
// ~300 lines of deterministic layout.  See ``buildDagModel.ts`` for
// the algorithm.
//
// Density
// -------
// Two density modes — ``comfortable`` (default, wider cards) and
// ``compact`` (denser, narrower cards) — toggle via the controls
// bar.  The grid's cell size is the only thing that changes; node
// cards reflow automatically.
// ============================================================================

import { useCallback, useMemo, useRef, useState } from 'react';
import { Maximize2, Tag, TagsIcon } from 'lucide-react';
import type { WorkspaceDetail } from '@/services/workspaceApi';
import { cn } from '@/utils/cn';
import { StageCard } from './StageCard';
import { DagEdgesLayer } from './DagEdgesLayer';
import { DagInspector } from './DagInspector';
import { DagWarningsBanner } from './DagWarningsBanner';
import { AuditHeader } from './AuditHeader';
import { buildDagModel, type DagModel } from './lib/buildDagModel';

type Density = 'compact' | 'comfortable';

const CARD_WIDTH: Record<Density, number> = {
  compact: 200,
  comfortable: 240,
};
const LANE_HEIGHT: Record<Density, number> = {
  compact: 156,
  comfortable: 184,
};
const COLUMN_GAP: Record<Density, number> = {
  compact: 80,
  comfortable: 112,
};
const LANE_GAP: Record<Density, number> = {
  compact: 32,
  comfortable: 40,
};
/** Horizontal padding inside the canvas — leaves room for the
 *  source-side edge dot + the terminal arrowhead. */
const CANVAS_PADDING_X = 28;
const CANVAS_PADDING_Y = 24;

type Props = {
  detail: WorkspaceDetail;
};

export function DagView({ detail }: Props) {
  const [density, setDensity] = useState<Density>('comfortable');
  const [showLabels, setShowLabels] = useState<boolean>(true);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(
    detail.focus_node ?? null,
  );

  const model = useMemo(() => buildDagModel(detail), [detail]);

  // Phase D / D9 — surface the run's bounded self-correction as a DAG
  // warning.  Derived from the persisted run_audit sidecar, NOT from
  // topology (buildDagModel stays a pure function of nodes + edges).
  const warnings = useMemo(
    () =>
      (detail.run_audit?.recompose_trace?.length ?? 0) > 0
        ? ([...model.warnings, 'self_corrected'] as const)
        : model.warnings,
    [model, detail.run_audit],
  );

  // Compute pixel positions for each node.  The grid below is
  // styled with fixed track sizes, so the SVG edge layer can read
  // these positions to draw Bezier curves between matching centres.
  const { canvasWidth, canvasHeight, nodeCoords } = useMemo(
    () =>
      computeCanvasLayout({
        model,
        cardWidth: CARD_WIDTH[density],
        laneHeight: LANE_HEIGHT[density],
        columnGap: COLUMN_GAP[density],
        laneGap: LANE_GAP[density],
        paddingX: CANVAS_PADDING_X,
        paddingY: CANVAS_PADDING_Y,
      }),
    [model, density],
  );

  // Scroll the canvas to the selected node when the user picks one.
  const canvasRef = useRef<HTMLDivElement>(null);
  const handleFit = useCallback(() => {
    if (canvasRef.current) {
      canvasRef.current.scrollTo({
        left: 0,
        top: 0,
        behavior: 'smooth',
      });
    }
  }, []);

  const selectedModelNode = useMemo(() => {
    if (selectedNodeId === null) return null;
    return (
      model.nodes.find((n) => n.node.node_id === selectedNodeId) ?? null
    );
  }, [model, selectedNodeId]);

  if (model.nodes.length === 0) {
    return <EmptyDag />;
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <DagControls
        density={density}
        onDensityChange={setDensity}
        showLabels={showLabels}
        onLabelsChange={setShowLabels}
        onFit={handleFit}
        rankCount={model.ranks}
        laneCount={model.lanes}
        nodeCount={model.nodes.length}
      />

      <AuditHeader audit={detail.run_audit} />

      <DagWarningsBanner warnings={[...warnings]} />

      <div className="relative min-h-0 flex-1 overflow-hidden">
        {/* Scroll container for the canvas. */}
        <div
          ref={canvasRef}
          className="relative h-full w-full overflow-auto"
          style={{
            // The viewBox-style backdrop is enough; we don't need a
            // separate grid behind the SVG.
          }}
        >
          <div
            className="relative"
            style={{
              width: canvasWidth,
              height: canvasHeight,
            }}
          >
            <DagEdgesLayer
              model={model}
              nodeCoords={nodeCoords}
              cardWidth={CARD_WIDTH[density]}
              laneHeight={LANE_HEIGHT[density]}
              canvasWidth={canvasWidth}
              canvasHeight={canvasHeight}
              showLabels={showLabels}
              selectedNodeId={selectedNodeId}
            />

            {/* Node layer — absolutely-positioned cards over the SVG. */}
            {model.nodes.map((modelNode) => {
              const coord = nodeCoords.get(modelNode.node.node_id);
              if (!coord) return null;
              const isSelected =
                selectedNodeId === modelNode.node.node_id;
              return (
                <div
                  key={modelNode.node.node_id}
                  className={cn(
                    'absolute z-[1] transition-shadow duration-150 ease-sleek',
                    isSelected &&
                      'ring-2 ring-ice-400/50 ring-offset-2 ring-offset-transparent rounded-[14px]',
                  )}
                  style={{
                    left: coord.x,
                    top: coord.y,
                    width: CARD_WIDTH[density],
                  }}
                >
                  <button
                    type="button"
                    onClick={() =>
                      setSelectedNodeId((curr) =>
                        curr === modelNode.node.node_id
                          ? null
                          : modelNode.node.node_id,
                      )
                    }
                    aria-pressed={isSelected}
                    className="block w-full text-left"
                  >
                    <StageCard
                      node={modelNode.node}
                      workspace={detail}
                      index={
                        // Display the topological index for context — same
                        // 1-based numbering the pre-PR6 strip used.
                        model.nodes
                          .slice()
                          .sort(rankLaneOrder)
                          .findIndex(
                            (n) =>
                              n.node.node_id === modelNode.node.node_id,
                          ) + 1
                      }
                      isTerminal={modelNode.isTerminal}
                    />
                  </button>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      <DagInspector
        modelNode={selectedModelNode}
        model={model}
        audit={detail.run_audit}
        onClose={() => setSelectedNodeId(null)}
      />
    </div>
  );
}

// ----------------------------------------------------------------------------
// Pixel-layout calculator
// ----------------------------------------------------------------------------

interface CanvasLayout {
  canvasWidth: number;
  canvasHeight: number;
  nodeCoords: Map<string, { x: number; y: number }>;
}

function computeCanvasLayout(args: {
  model: DagModel;
  cardWidth: number;
  laneHeight: number;
  columnGap: number;
  laneGap: number;
  paddingX: number;
  paddingY: number;
}): CanvasLayout {
  const {
    model,
    cardWidth,
    laneHeight,
    columnGap,
    laneGap,
    paddingX,
    paddingY,
  } = args;
  const nodeCoords = new Map<string, { x: number; y: number }>();

  const columnPitch = cardWidth + columnGap;
  const lanePitch = laneHeight + laneGap;

  for (const modelNode of model.nodes) {
    const x = paddingX + modelNode.rank * columnPitch;
    const y = paddingY + modelNode.lane * lanePitch;
    nodeCoords.set(modelNode.node.node_id, { x, y });
  }

  const canvasWidth =
    paddingX * 2 +
    Math.max(1, model.ranks) * cardWidth +
    Math.max(0, model.ranks - 1) * columnGap;
  const canvasHeight =
    paddingY * 2 +
    Math.max(1, model.lanes) * laneHeight +
    Math.max(0, model.lanes - 1) * laneGap;

  return { canvasWidth, canvasHeight, nodeCoords };
}

function rankLaneOrder(
  a: { rank: number; lane: number },
  b: { rank: number; lane: number },
): number {
  if (a.rank !== b.rank) return a.rank - b.rank;
  return a.lane - b.lane;
}

// ----------------------------------------------------------------------------
// Controls bar
// ----------------------------------------------------------------------------

function DagControls({
  density,
  onDensityChange,
  showLabels,
  onLabelsChange,
  onFit,
  rankCount,
  laneCount,
  nodeCount,
}: {
  density: Density;
  onDensityChange: (d: Density) => void;
  showLabels: boolean;
  onLabelsChange: (v: boolean) => void;
  onFit: () => void;
  rankCount: number;
  laneCount: number;
  nodeCount: number;
}) {
  return (
    <div className="flex shrink-0 items-center justify-between gap-3 border-b border-line-subtle px-6 py-3">
      <div>
        <h2 className="kicker text-fg-muted">DAG · workflow anatomy</h2>
        <p className="mt-0.5 text-[11px] text-fg-faint">
          {nodeCount} {nodeCount === 1 ? 'stage' : 'stages'} ·{' '}
          {rankCount} {rankCount === 1 ? 'rank' : 'ranks'} ·{' '}
          {laneCount} {laneCount === 1 ? 'lane' : 'lanes'}
        </p>
      </div>
      <div className="flex items-center gap-1.5">
        <ControlToggle
          active={showLabels}
          onClick={() => onLabelsChange(!showLabels)}
          icon={showLabels ? <Tag size={11} /> : <TagsIcon size={11} />}
          label={showLabels ? 'Labels on' : 'Labels off'}
          title="Show / hide edge slot names"
        />
        <ControlToggle
          active={density === 'compact'}
          onClick={() =>
            onDensityChange(density === 'compact' ? 'comfortable' : 'compact')
          }
          icon={null}
          label={density === 'compact' ? 'Compact' : 'Comfortable'}
          title="Toggle density"
        />
        <button
          type="button"
          onClick={onFit}
          title="Scroll back to the start of the DAG"
          className="flex items-center gap-1 rounded-md border border-line-soft bg-white/[0.025] px-2 py-1 text-[10.5px] font-medium text-fg-secondary transition-colors hover:border-ice-400/35 hover:text-ice-200"
        >
          <Maximize2 size={11} strokeWidth={1.75} aria-hidden />
          <span>Fit</span>
        </button>
      </div>
    </div>
  );
}

function ControlToggle({
  active,
  onClick,
  icon,
  label,
  title,
}: {
  active: boolean;
  onClick: () => void;
  icon: React.ReactNode | null;
  label: string;
  title: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      className={cn(
        'flex items-center gap-1 rounded-md border px-2 py-1 text-[10.5px] font-medium transition-colors',
        active
          ? 'border-ice-400/35 bg-ice-500/10 text-ice-200'
          : 'border-line-soft bg-white/[0.025] text-fg-secondary hover:border-ice-400/30 hover:text-ice-200',
      )}
    >
      {icon}
      <span>{label}</span>
    </button>
  );
}

function EmptyDag() {
  return (
    <div className="px-6 py-10">
      <div className="card flex h-[200px] items-center justify-center text-[12px] text-fg-muted">
        This workspace has no persisted nodes.
      </div>
    </div>
  );
}
