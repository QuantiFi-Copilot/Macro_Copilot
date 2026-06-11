// ============================================================================
// StandardPreviewWidget — shared persisted-artifact card for the standard
// dual-view primitive tools (those that ship buildExtended + buildCompact
// but no bespoke ``preview`` surface).
// ----------------------------------------------------------------------------
// Codex-flag fix.  In a PERSISTED open-DAG workspace (``/workspace/:slug``)
// a primitive node previously fell through to the generic per-artifact-type
// widget — an engineer-flavoured stats strip (obs / finite / first-date).
// This widget reframes the SAME saved artifact in desk terms: tool identity
// + headline value + sparkline + the tool's methodology one-liner, with an
// honest note about the live-only metrics.  One shared renderer, wired to
// every standard tool by the ``widgets/index.ts`` walker — ZERO per-tool
// files.
//
// Determinism (P4) — the load-bearing constraint
// -----------------------------------------------
// This card is READ-ONLY BY HASH.  The body loads via PayloadShell →
// ``useArtifactPayload`` → ``GET /api/v1/artifacts/{hash}/payload`` — the
// content-addressed, frozen snapshot.  It NEVER live-refetches the
// typed-detail endpoint the live ``buildCompact`` uses; doing so would pull
// *today's* numbers into a saved workspace, breaking P4's "open a
// six-month-old workspace, see the same numbers" guarantee (and FP9 —
// the frontend renders the backend's persisted values, it does not
// recompute analytics).
//
// What the saved Series body does NOT contain
// --------------------------------------------
// The substrate's Series bridge lifts a single ``time_series_*`` field per
// ``output_field``; the rich ``current_metrics`` (z-score, period changes,
// regime) are computed at live-run and are NOT in the persisted body
// (see ``persistedModelAdapters.ts`` backend matrix).  We disclose this
// honestly (P5) rather than fabricate it.  Filling those into the saved
// card is the scoped backend follow-up (persist ``current_metrics``).
//
// Pattern lineage
// ---------------
// Generalises the rich-model ``RichModelWidget`` shell (render_shells.md):
// one shared, ``toolName``-parameterised, hash-fetched NodeRenderer, wired
// per-tool by the registry walker.  Reads ``getPrimitiveModule(toolName)``
// for display metadata (the central registry lookup — FP12-safe, same as
// ``persistedModelAdapters``).
// ============================================================================

import { useMemo } from 'react';
import { FlaskConical } from 'lucide-react';
import type { NodeRenderProps } from '@/components/build/lib/nodeRendererRegistry';
import { resolveArtifactTypeRenderer } from '@/components/build/lib/nodeRendererRegistry';
import { getPrimitiveModule } from '@/modules';
import { Sparkline } from '@/components/ui/Sparkline';
import type { ChartTone } from '@/lib/chart';
import type { StageCategory } from '@/components/build/lib/buildTypes';
import type { SeriesPayloadEnvelope } from '@/types/artifacts';
import { PayloadShell } from './PayloadShell';
import {
  classifyArtifactDate,
  formatDate,
  formatNumberWithUnits,
  lastSeriesObservation,
  seriesFiniteCount,
  seriesObservationCount,
} from './artifactFormat';

type Props = NodeRenderProps & {
  /** Backend-canonical tool name.  Defaults to ``node.params.tool_name``
   *  so the registry can register the bare component for every tool. */
  toolName?: string;
};

const TONE_BY_CATEGORY: Record<StageCategory, ChartTone> = {
  input: 'blue',
  transform: 'rates',
  output: 'amber',
};

export function StandardPreviewWidget({
  node,
  artifact,
  category,
  workspace,
  size,
  toolName,
}: Props) {
  const tn = toolName ?? extractToolName(node) ?? '';
  const module = getPrimitiveModule(tn);
  const displayName = module?.displayName ?? prettyToolName(tn);
  const methodology = module?.oneLineSummary ?? null;

  // Non-Series persisted bodies are rare for these tools (they emit
  // Series).  Delegate the body to the generic per-artifact-type renderer
  // but keep the desk identity header + methodology footer so the card
  // reads consistently.
  if (artifact.artifact_type !== 'Series') {
    const Body = resolveArtifactTypeRenderer(artifact.artifact_type);
    return (
      <CardFrame
        displayName={displayName}
        artifactType={artifact.artifact_type}
        hash={artifact.hash}
        methodology={methodology}
      >
        <Body
          node={node}
          artifact={artifact}
          category={category}
          workspace={workspace}
          size={size}
        />
      </CardFrame>
    );
  }

  return (
    <CardFrame
      displayName={displayName}
      artifactType={artifact.artifact_type}
      hash={artifact.hash}
      methodology={methodology}
    >
      <PayloadShell<SeriesPayloadEnvelope>
        artifactHash={node.artifact_hash ?? artifact.hash}
        expectedType="Series"
        displayName={displayName}
        isEmpty={(p) => seriesObservationCount(p) === 0}
        emptyMessage="The saved series has zero observations."
      >
        {(payload) => (
          <DeskSeriesBody payload={payload} category={category} size={size} />
        )}
      </PayloadShell>
    </CardFrame>
  );
}

// ----------------------------------------------------------------------------
// Card frame — desk identity header + methodology / live-metrics footer.
// (The parent NodeWidgetCard already supplies the WidgetCard chrome + the
// stage title + lineage chip + provenance footer; this frame adds the
// desk-oriented provenance + honest disclosure the generic widget lacked.)
// ----------------------------------------------------------------------------

function CardFrame({
  displayName,
  artifactType,
  hash,
  methodology,
  children,
}: {
  displayName: string;
  artifactType: string;
  hash: string;
  methodology: string | null;
  children: React.ReactNode;
}) {
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 items-center gap-2 border-b border-line-subtle px-5 py-1.5">
        <FlaskConical
          size={11}
          strokeWidth={1.75}
          aria-hidden
          className="shrink-0 text-ice-300"
        />
        <span className="kicker truncate text-fg-muted">{displayName}</span>
        <span className="ml-auto shrink-0 font-mono text-[10px] text-fg-faint">
          saved · {artifactType} · {hash.slice(0, 8)}
        </span>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>

      <div className="shrink-0 border-t border-line-subtle px-5 py-2">
        {methodology && (
          <p className="text-[10.5px] leading-[1.5] text-fg-faint">
            {methodology}
          </p>
        )}
        <p className="mt-1 text-[10px] leading-[1.45] text-fg-faint">
          Saved snapshot. Live z-score, period changes &amp; regime are
          computed in the tool&apos;s live Build view.
        </p>
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------------
// Desk Series body — headline value + sparkline (desk register, not the
// generic obs/finite stats strip).
// ----------------------------------------------------------------------------

function DeskSeriesBody({
  payload,
  category,
  size,
}: {
  payload: SeriesPayloadEnvelope;
  category: StageCategory;
  size: 'small' | 'medium' | 'wide' | 'tall';
}) {
  const units = payload.metadata.units;
  const last = useMemo(() => lastSeriesObservation(payload), [payload]);
  const finiteObs = useMemo(() => seriesFiniteCount(payload), [payload]);

  const sparklineData = useMemo(() => {
    const idx = payload.payload.index ?? [];
    const vs = payload.payload.values ?? [];
    const out: { value: number; index: string }[] = [];
    for (let i = 0; i < vs.length; i++) {
      const v = vs[i];
      if (v === null || v === undefined || !Number.isFinite(v)) continue;
      out.push({ value: v, index: idx[i] ?? String(i) });
    }
    return out;
  }, [payload]);

  if (finiteObs === 0) {
    return (
      <div className="px-5 pt-3 pb-4">
        <div className="kicker text-fg-muted">{payload.metadata.series_key}</div>
        <p className="mt-2 text-[11px] leading-[1.5] text-fg-secondary">
          The saved series ran but every observation is null (commonly a
          rolling window shorter than the primitive&apos;s min-periods
          threshold).
        </p>
      </div>
    );
  }

  const tone = TONE_BY_CATEGORY[category];
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="px-5 pt-3 pb-1">
        <div className="kicker text-fg-muted">
          {payload.metadata.series_key}
          {units && (
            <span className="ml-2 normal-case tracking-normal text-fg-faint">
              {units}
            </span>
          )}
        </div>
        {last && (
          <div className="mt-1.5 flex items-baseline gap-2">
            <span className="font-mono text-[20px] tabular-nums text-fg-primary">
              {formatNumberWithUnits(last.value, units)}
            </span>
            <span className="font-mono text-[10px] text-fg-faint">
              {asOfLabel(last.date)}
            </span>
          </div>
        )}
      </div>

      {sparklineData.length >= 2 && (
        <Sparkline
          data={sparklineData}
          tone={tone}
          mode="area"
          height={size === 'small' ? 64 : 104}
        />
      )}
    </div>
  );
}

// ----------------------------------------------------------------------------
// Helpers
// ----------------------------------------------------------------------------

/** "as-of {date}" preamble; suppressed for sentinel / unknown dates. */
function asOfLabel(value: unknown): string {
  const cls = classifyArtifactDate(value);
  if (cls.kind === 'real_date') return `as-of ${formatDate(value)}`;
  return '';
}

/** Pull ``tool_name`` off the node's params (the substrate stamps it on
 *  every PrimitiveNode). */
function extractToolName(node: NodeRenderProps['node']): string | null {
  const raw = (node.params ?? {}) as Record<string, unknown>;
  return typeof raw.tool_name === 'string' && raw.tool_name.length > 0
    ? raw.tool_name
    : null;
}

/** Light prettifier for the rare case a node names a tool with no
 *  registered module (keeps the header from showing a raw snake-case id). */
function prettyToolName(toolName: string): string {
  if (!toolName) return 'Primitive';
  return toolName
    .replace(/_tool$/, '')
    .replace(/^(calculate|get|scan|build|classify|compute)_/, '')
    .split('_')
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ');
}

export default StandardPreviewWidget;
