// ============================================================================
// RichModelWidget — bridge from a Build node to a bespoke model renderer.
// ----------------------------------------------------------------------------
// PR3 — schema-aware persisted-artifact rendering.
//
// History
// -------
// Phase R3 mounted this widget for the persisted workflow surfaces and
// shipped a temporary path: re-run the underlying primitive on widget
// mount via the ``runPrimitive`` service call so the bespoke renderer
// received the full output dict.  That was a stop-gap until the
// backend shipped ``GET /api/v1/artifacts/{hash}/payload``.  The
// payload endpoint exists now (``api/routes/artifacts.py`` PR R5.2);
// PR3 wires the widget to it.
//
// Behaviour after PR3
// -------------------
//   1. Persisted node mounts → ``useArtifactPayload(hash)`` fetches the
//      ``StoredArtifact`` body.  No primitive is re-run.
//   2. Loading state shows a tight spinner; error state shows the
//      typed ``ArtifactPayloadError`` message with a retry button.
//   3. On success, the FULL ``ArtifactPayloadResponse`` is exposed via
//      ``payload`` so payload-aware renderers (PR4) can dispatch on
//      ``artifact_type``.  For backward compatibility with the
//      pre-PR4 ``Renderer`` contract (which takes
//      ``output: Record<string, unknown>``) we also pass
//      ``payload.payload`` as ``output`` so the existing renderers
//      continue to receive a dict.  The body fields will differ from
//      the run-endpoint shape — the persisted artifact is the
//      substrate-canonical body, not the primitive's *Output dict —
//      so renderers that depend on rerun-only fields like
//      ``current_metrics.loadings`` will see absent fields and degrade
//      gracefully.  PR4 owns the payload-aware refactor that closes
//      this gap.
//   4. A small artifact-identity header sits above the Renderer body
//      with the hash short-prefix, artifact_type, and lineage chain
//      length so users can confirm WHICH snapshot the card is
//      reading.  A "persisted snapshot" footer below the body links
//      back to the builder for re-runs.
//
// What this widget DOES NOT do
// ----------------------------
// - Never calls ``runPrimitive``.  Re-running is the explicit job of
//   the model-builder / generic-builder pages where the user clicks
//   "Run".  Persisted rendering MUST be read-only by hash.
// - Doesn't invent payload fields.  If the backend returns 404 or a
//   shape mismatch, the widget renders a clear local error rather
//   than fake content.
// - Doesn't fan failures up the tree.  One failed artifact does not
//   crash the workspace; each widget owns its local error state.
// ============================================================================

import { AlertCircle, FileText, Loader2, RotateCcw } from 'lucide-react';
import type { ComponentType } from 'react';
import { useArtifactPayload } from '@/hooks/useArtifactPayload';
import {
  ArtifactPayloadError,
  type ArtifactPayloadErrorStatus,
} from '@/services/workspaceApi';
import type { ArtifactPayloadResponse } from '@/types/artifacts';
import type { NodeRenderProps } from '@/components/build/lib/nodeRendererRegistry';

/** Renderer contract — every bespoke model renderer accepts the raw
 *  payload body dict.  Matches the legacy
 *  ``model-workspace/renderers/*`` signature so the renderers can be
 *  lifted verbatim without rewrapping; PR4 will extend this to a
 *  payload-aware variant. */
export type RichModelRenderer = ComponentType<{
  output: Record<string, unknown>;
}>;

type Props = NodeRenderProps & {
  /** Bespoke renderer (PcaLoadingsRenderer / RollingRegressionRenderer
   *  / AttributionRenderer) — the visual unit that turns the payload
   *  body into the rich chart suite.  PR4 will swap this for a
   *  payload-aware variant. */
  Renderer: RichModelRenderer;
  /** Display name surfaced on loading / error / identity headers.
   *  Defaults to the artifact_type when the persisted artifact loads. */
  toolDisplayName?: string;
};

export function RichModelWidget({
  node,
  artifact,
  Renderer,
  toolDisplayName,
}: Props) {
  // Source the hash from the node first (canonical), fall back to the
  // artifact summary the parent already loaded.  Same value either
  // way — the parent's ``NodeWidgetCard`` only invokes us when an
  // artifact summary is present.
  const hash = node.artifact_hash ?? artifact?.hash ?? null;
  const { data, isLoading, error, refetch } = useArtifactPayload(hash);

  const displayName =
    toolDisplayName ?? artifact?.artifact_type ?? 'persisted artifact';

  if (!hash) {
    return <NoHashState displayName={displayName} />;
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
    // Hook returned null with no loading / no error — shouldn't
    // happen, but render a stable placeholder rather than a blank
    // body if the hook contract ever drifts.
    return <NoHashState displayName={displayName} />;
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <ArtifactIdentity payload={data} hash={hash} displayName={displayName} />
      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
        <Renderer output={data.payload as Record<string, unknown>} />
      </div>
      <PersistedSnapshotFooter />
    </div>
  );
}

// ----------------------------------------------------------------------------
// Header — artifact identity (hash, type, lineage steps) so the user
// can confirm WHICH snapshot the card reads.  Sits between the parent
// card's chrome and the renderer body.
// ----------------------------------------------------------------------------

function ArtifactIdentity({
  payload,
  hash,
  displayName,
}: {
  payload: ArtifactPayloadResponse;
  hash: string;
  displayName: string;
}) {
  const lineageSteps = Array.isArray(payload.metadata?.lineage?.steps)
    ? payload.metadata.lineage.steps.length
    : 0;
  return (
    <div className="flex shrink-0 items-center justify-between gap-3 border-b border-line-subtle px-5 py-2">
      <div className="flex min-w-0 items-center gap-2">
        <FileText
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
        {lineageSteps} lineage step{lineageSteps === 1 ? '' : 's'}
        {displayName ? ` · ${displayName}` : ''}
      </span>
    </div>
  );
}

// ----------------------------------------------------------------------------
// Footer — clarifies the read-by-hash semantics so a user looking at
// an empty / sparse rich card understands they're seeing the persisted
// snapshot, not a stale or broken render.  Re-runs happen in the
// builder.
// ----------------------------------------------------------------------------

function PersistedSnapshotFooter() {
  return (
    <div className="shrink-0 border-t border-line-subtle px-5 py-2 text-[10px] leading-[1.5] text-fg-faint">
      Persisted snapshot · loaded from{' '}
      <code className="font-mono text-[10px] text-fg-muted">
        /artifacts/{'{hash}'}/payload
      </code>
      . Re-run from the model builder to recompute.
    </div>
  );
}

// ----------------------------------------------------------------------------
// State views — loading / error / no-hash.  Sized to match the parent
// card body so the surrounding chrome (rail, header, provenance footer)
// stays stable across states.
// ----------------------------------------------------------------------------

function LoadingState({ displayName }: { displayName: string }) {
  return (
    <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-2 px-5 py-8 text-fg-muted">
      <Loader2 size={16} className="animate-spin text-ice-300" />
      <span className="text-[11.5px]">
        Loading persisted artifact for {displayName}…
      </span>
    </div>
  );
}

function NoHashState({ displayName }: { displayName: string }) {
  return (
    <div className="flex min-h-0 flex-1 items-start gap-2 px-5 py-4">
      <AlertCircle
        size={14}
        className="mt-0.5 shrink-0 text-amber-300"
        aria-hidden
      />
      <div className="min-w-0">
        <div className="text-[11.5px] font-semibold text-fg-primary">
          No persisted artifact for {displayName}
        </div>
        <div className="mt-1 text-[10.5px] leading-[1.5] text-fg-secondary">
          This node was rendered without an{' '}
          <code className="font-mono text-[10.5px] text-fg-muted">
            artifact_hash
          </code>
          .  Open the Parameters tab to inspect the node configuration, or
          re-run the workspace to populate a persisted artifact.
        </div>
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
      <AlertCircle size={14} className="mt-0.5 shrink-0 text-coral-300" />
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
        <div className="mt-2 text-[10px] leading-[1.5] text-fg-faint">
          {statusHint(status)}
        </div>
        <button
          type="button"
          onClick={onRetry}
          className="mt-3 flex items-center gap-1.5 rounded-md border border-line-soft bg-white/[0.025] px-2.5 py-1 text-[10.5px] font-medium text-fg-secondary transition-colors hover:border-ice-400/35 hover:text-ice-200"
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

function statusHint(status: ArtifactPayloadErrorStatus | null): string {
  switch (status) {
    case 400:
      return 'The artifact hash is malformed (must be a 64-char hex SHA-256).';
    case 404:
      return 'The backend has no persisted artifact for this hash — the workspace may reference a snapshot that was pruned.';
    case 503:
      return 'Object storage is offline.  The artifact metadata exists but the body cannot be read right now.';
    case 500:
      return 'Server-side error reading the artifact.  Retry, or check the backend logs.';
    case -1:
      return 'Network error reaching the API.  Check the connection and retry.';
    default:
      return 'Retry to re-issue the fetch.';
  }
}
