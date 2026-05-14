// ============================================================================
// RichModelWidget — payload-shape-correct persisted-model card.
// ----------------------------------------------------------------------------
// PR1 (new plan) — closes the rich-renderer / persisted-artifact shape
// mismatch.
//
// The pre-PR1 widget passed ``data.payload`` (the substrate-canonical
// ``StoredArtifact.payload`` body) to a legacy ``Renderer`` prop —
// usually a model-builder renderer like ``PcaLoadingsRenderer`` /
// ``RollingRegressionRenderer`` that expects the LIVE run-endpoint
// ``*Output`` dict shape (``output.current_metrics.loadings``,
// ``output.time_series_factors``, etc.).  Persisted Series artifacts
// don't contain those fields — the substrate's Series bridge lifts a
// single ``time_series_*`` field per ``output_field``.  Result: the
// renderer's defensive ``?? []`` fall-backs swallowed the absence and
// rendered an empty body.
//
// New behaviour (this file)
// -------------------------
// 1. Load the artifact payload by hash via PR3's hook (unchanged).
// 2. Run the per-tool adapter (``adaptModelArtifact``) over the loaded
//    payload.  The adapter classifies the result into one of three
//    closed variants:
//      - ``persisted_series``         — body is the expected Series;
//                                        render the generic
//                                        SeriesWidget body via the
//                                        per-type registry
//                                        (``resolveArtifactTypeRenderer``)
//                                        — NOT via the per-tool entry
//                                        (avoids infinite recursion
//                                        through ``PcaPreviewWidget`` →
//                                        this widget).
//      - ``shape_mismatch``           — body is some other artifact
//                                        type; render the per-type
//                                        body for THAT type + a
//                                        callout explaining what was
//                                        expected.
//      - ``pure_snapshot_unavailable``— tool emits no time_series
//                                        field (attribution,
//                                        half-life); render a clear
//                                        "snapshot view not
//                                        persistable" callout instead
//                                        of an empty body.
// 3. Render an artifact-identity header + a "what's NOT in the saved
//    snapshot" detail-unavailable callout per the adapter.
//
// The ``Renderer`` prop is DROPPED.  Every renderer needs different
// per-shape adaptation (the live-run renderers stay alive in
// ``model/OutputCanvas`` for the model-builder Run flow), so a single
// generic prop is structurally wrong.
//
// What this widget does NOT do
// ----------------------------
// - Never calls ``runPrimitive``.  Re-running is the explicit job of
//   the model-builder / generic-builder pages where the user clicks
//   "Run".  Persisted rendering MUST be read-only by hash.
// - Doesn't fan failures up the tree.  One failed artifact does not
//   crash the workspace; each widget owns its local error state.
// ============================================================================

import { AlertCircle, FlaskConical, Lock, RotateCcw } from 'lucide-react';
import { useArtifactPayload } from '@/hooks/useArtifactPayload';
import {
  ArtifactPayloadError,
  type ArtifactPayloadErrorStatus,
} from '@/services/workspaceApi';
import type { ArtifactPayloadResponse } from '@/types/artifacts';
import type { NodeRenderProps } from '@/components/build/lib/nodeRendererRegistry';
import { resolveArtifactTypeRenderer } from '@/components/build/lib/nodeRendererRegistry';
import {
  adaptModelArtifact,
  getModelAdapter,
  type AdaptedModelView,
  type ModelAdapter,
} from './persistedModelAdapters';

type Props = NodeRenderProps & {
  /** Backend-canonical tool name for the persisted node — drives
   *  ``getModelAdapter``.  Defaults to ``node.params.tool_name`` so
   *  callers can omit it for the common case. */
  toolName?: string;
};

export function RichModelWidget({
  node,
  artifact,
  category,
  workspace,
  size,
  toolName,
}: Props) {
  // Hash comes from the canonical ``node.artifact_hash`` first, with
  // ``artifact.hash`` as a fallback (parent only invokes us when an
  // artifact summary is present).
  const hash = node.artifact_hash ?? artifact?.hash ?? null;
  const { data, isLoading, error, refetch } = useArtifactPayload(hash);

  // Resolve the per-tool adapter even before the payload lands so the
  // loading / no-hash states can name the tool correctly.
  const effectiveToolName = toolName ?? extractToolName(node) ?? '';
  const adapter = getModelAdapter(effectiveToolName);
  const displayName = adapter.displayName;

  if (!hash) {
    return <NoHashState adapter={adapter} />;
  }
  if (isLoading) {
    return <LoadingState displayName={displayName} />;
  }
  if (error) {
    return (
      <ErrorState
        displayName={displayName}
        hash={hash}
        error={error}
        onRetry={refetch}
      />
    );
  }
  if (!data) {
    return <NoHashState adapter={adapter} />;
  }

  // Run the adapter — pure dispatch on (toolName, payload.artifact_type).
  const view = adaptModelArtifact({ toolName: effectiveToolName, payload: data });

  return (
    <div className="flex h-full min-h-0 flex-col">
      <ArtifactIdentity payload={data} hash={hash} adapter={adapter} />

      <div className="min-h-0 flex-1 overflow-y-auto">
        <AdaptedBody
          view={view}
          node={node}
          artifact={artifact}
          category={category}
          workspace={workspace}
          size={size}
        />
      </div>

      <DetailUnavailable adapter={adapter} />
    </div>
  );
}

// ----------------------------------------------------------------------------
// Body dispatcher — picks the right rendering per adapter view variant.
// ----------------------------------------------------------------------------

function AdaptedBody({
  view,
  node,
  artifact,
  category,
  workspace,
  size,
}: {
  view: AdaptedModelView;
} & Pick<NodeRenderProps, 'node' | 'artifact' | 'category' | 'workspace' | 'size'>) {
  switch (view.kind) {
    case 'persisted_series':
      // Delegate to the GENERIC per-artifact-type body (SeriesWidget
      // for the Series case).  ``resolveArtifactTypeRenderer`` skips
      // the per-tool registry entry on purpose — using the per-tool
      // entry (e.g. PcaPreviewWidget) would recurse back into THIS
      // widget.  SeriesWidget reads the same hash and uses its
      // payload cache hit (PR3) so there's no extra fetch.
      return <DelegatedTypedBody
        artifactType={view.payload.artifact_type}
        node={node}
        artifact={artifact}
        category={category}
        workspace={workspace}
        size={size}
      />;
    case 'shape_mismatch':
      return (
        <div className="flex flex-col gap-3 px-5 py-4">
          <ShapeMismatchCallout
            adapter={view.adapter}
            expected={view.expected}
            got={view.got}
          />
          <DelegatedTypedBody
            artifactType={view.payload.artifact_type}
            node={node}
            artifact={artifact}
            category={category}
            workspace={workspace}
            size={size}
          />
        </div>
      );
    case 'pure_snapshot_unavailable':
      return <PureSnapshotUnavailable adapter={view.adapter} />;
  }
}

/** Resolves the per-ARTIFACT-TYPE renderer (NOT the per-tool one) and
 *  mounts it.  This is the critical bypass: the per-tool registry
 *  entry for ``(Series, calculate_pca_yield_curve_tool)`` points BACK
 *  at this widget — using it here would infinite-loop. */
function DelegatedTypedBody({
  artifactType,
  node,
  artifact,
  category,
  workspace,
  size,
}: {
  artifactType: string;
} & Pick<NodeRenderProps, 'node' | 'artifact' | 'category' | 'workspace' | 'size'>) {
  const Renderer = resolveArtifactTypeRenderer(artifactType);
  return (
    <Renderer
      node={node}
      artifact={artifact}
      category={category}
      workspace={workspace}
      size={size}
    />
  );
}

// ----------------------------------------------------------------------------
// Header — artifact identity (hash, type, lineage steps).
// ----------------------------------------------------------------------------

function ArtifactIdentity({
  payload,
  hash,
  adapter,
}: {
  payload: ArtifactPayloadResponse;
  hash: string;
  adapter: ModelAdapter;
}) {
  const lineageSteps = Array.isArray(payload.metadata?.lineage?.steps)
    ? payload.metadata.lineage.steps.length
    : 0;
  return (
    <div className="flex shrink-0 flex-col gap-1 border-b border-line-subtle px-5 py-2">
      <div className="flex items-baseline justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <FlaskConical
            size={11}
            strokeWidth={1.75}
            aria-hidden
            className="shrink-0 text-ice-300"
          />
          <span className="kicker shrink-0 text-fg-muted">
            persisted · {payload.artifact_type}
          </span>
          <span className="truncate font-mono text-[10px] text-fg-faint">
            {hash.slice(0, 12)}…
          </span>
        </div>
        <span className="shrink-0 font-mono text-[10px] text-fg-faint">
          {lineageSteps} lineage step{lineageSteps === 1 ? '' : 's'} ·{' '}
          {adapter.displayName}
        </span>
      </div>
      <p className="text-[10.5px] leading-[1.45] text-fg-faint">
        {adapter.persistedRole.headline} — {adapter.persistedRole.description}
      </p>
    </div>
  );
}

// ----------------------------------------------------------------------------
// Detail-unavailable footer — honest list of rich-fields NOT in body.
// ----------------------------------------------------------------------------

function DetailUnavailable({ adapter }: { adapter: ModelAdapter }) {
  if (adapter.detailUnavailable.length === 0) return null;
  return (
    <div className="shrink-0 border-t border-line-subtle px-5 py-2 text-[10px] leading-[1.5] text-fg-faint">
      <div className="font-mono uppercase tracking-[0.1em] text-fg-muted">
        Not in this saved snapshot
      </div>
      <ul className="mt-1 flex flex-col gap-0.5">
        {adapter.detailUnavailable.map((d) => (
          <li key={d} className="flex items-baseline gap-1.5">
            <span className="text-fg-faint">·</span>
            <span>{d}</span>
          </li>
        ))}
      </ul>
      {adapter.builderHint && (
        <p className="mt-1.5 text-[10px] text-fg-secondary">
          {adapter.builderHint}
        </p>
      )}
    </div>
  );
}

// ----------------------------------------------------------------------------
// Per-variant fallback panels
// ----------------------------------------------------------------------------

function ShapeMismatchCallout({
  adapter,
  expected,
  got,
}: {
  adapter: ModelAdapter;
  expected: string;
  got: string;
}) {
  return (
    <div className="card flex items-start gap-2 border-amber-400/30 bg-amber-500/[0.04] px-3 py-2">
      <AlertCircle
        size={12}
        className="mt-0.5 shrink-0 text-amber-300"
        aria-hidden
      />
      <div className="min-w-0">
        <div className="text-[11px] font-semibold text-amber-200">
          Unexpected persisted artifact type for {adapter.displayName}
        </div>
        <p className="mt-0.5 text-[10.5px] leading-[1.5] text-fg-secondary">
          The persisted body came back as{' '}
          <code className="font-mono text-fg-secondary">{got}</code>; this
          tool normally persists as{' '}
          <code className="font-mono text-fg-secondary">{expected}</code>.
          Rendering the body via the generic{' '}
          <code className="font-mono text-fg-secondary">{got}</code>{' '}
          renderer below.
        </p>
      </div>
    </div>
  );
}

function PureSnapshotUnavailable({ adapter }: { adapter: ModelAdapter }) {
  return (
    <div className="flex min-h-0 flex-1 items-start gap-2 px-5 py-4">
      <Lock size={12} className="mt-0.5 shrink-0 text-fg-faint" aria-hidden />
      <div className="min-w-0">
        <div className="text-[11.5px] font-semibold text-fg-primary">
          {adapter.displayName} · snapshot view not persistable today
        </div>
        <p className="mt-1 text-[10.5px] leading-[1.55] text-fg-secondary">
          {adapter.persistedRole.description}
        </p>
        {adapter.builderHint && (
          <p className="mt-2 text-[10.5px] leading-[1.55] text-fg-secondary">
            {adapter.builderHint}
          </p>
        )}
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------------
// Loading / error / no-hash states (same look as PR3).
// ----------------------------------------------------------------------------

function LoadingState({ displayName }: { displayName: string }) {
  return (
    <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-2 px-5 py-8 text-fg-muted">
      <RotateCcw size={14} className="animate-spin text-ice-300" />
      <span className="text-[11.5px]">
        Loading persisted artifact for {displayName}…
      </span>
    </div>
  );
}

function NoHashState({ adapter }: { adapter: ModelAdapter }) {
  return (
    <div className="flex min-h-0 flex-1 items-start gap-2 px-5 py-4">
      <AlertCircle
        size={13}
        className="mt-0.5 shrink-0 text-amber-300"
        aria-hidden
      />
      <div className="min-w-0">
        <div className="text-[11.5px] font-semibold text-fg-primary">
          No persisted artifact for {adapter.displayName}
        </div>
        <p className="mt-1 text-[10.5px] leading-[1.5] text-fg-secondary">
          This node was rendered without an{' '}
          <code className="font-mono text-fg-muted">artifact_hash</code>.{' '}
          {adapter.builderHint ||
            'Re-run the workspace to populate a persisted artifact.'}
        </p>
      </div>
    </div>
  );
}

function ErrorState({
  displayName,
  hash,
  error,
  onRetry,
}: {
  displayName: string;
  hash: string;
  error: ArtifactPayloadError | Error;
  onRetry: () => void;
}) {
  const status: ArtifactPayloadErrorStatus | null =
    error instanceof ArtifactPayloadError ? error.status : null;
  return (
    <div className="flex min-h-0 flex-1 items-start gap-2 px-5 py-4">
      <AlertCircle size={13} className="mt-0.5 shrink-0 text-coral-300" />
      <div className="min-w-0">
        <div className="text-[11.5px] font-semibold text-fg-primary">
          Couldn&apos;t load persisted artifact for {displayName}
        </div>
        <div className="mt-1 font-mono text-[10px] text-fg-faint">
          {hash.slice(0, 12)}… · {statusLabel(status)}
        </div>
        <div className="mt-2 text-[10.5px] leading-[1.5] text-fg-secondary">
          {error.message}
        </div>
        <button
          type="button"
          onClick={onRetry}
          className="mt-2.5 flex items-center gap-1.5 rounded-md border border-line-soft bg-white/[0.025] px-2.5 py-1 text-[10.5px] font-medium text-fg-secondary transition-colors hover:border-ice-400/35 hover:text-ice-200"
        >
          <RotateCcw size={10} strokeWidth={1.75} aria-hidden />
          <span>Retry</span>
        </button>
      </div>
    </div>
  );
}

function statusLabel(status: ArtifactPayloadErrorStatus | null): string {
  if (status === null) return 'fetch error';
  if (status === -1) return 'network error';
  return `HTTP ${status}`;
}

// ----------------------------------------------------------------------------
// Helper — pull ``tool_name`` off the node's params (substrate stamps
// it on every PrimitiveNode).
// ----------------------------------------------------------------------------

function extractToolName(node: NodeRenderProps['node']): string | null {
  const raw = (node.params ?? {}) as Record<string, unknown>;
  if (typeof raw.tool_name === 'string') return raw.tool_name;
  return null;
}
