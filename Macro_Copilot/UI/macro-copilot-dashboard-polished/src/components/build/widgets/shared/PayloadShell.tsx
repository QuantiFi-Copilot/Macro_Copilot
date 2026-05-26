// ============================================================================
// PayloadShell — the loading / error / empty / no-hash shell shared
// by every PR4 payload-backed widget.
// ----------------------------------------------------------------------------
// Wraps a child render that takes a typed ``ArtifactPayloadResponse``
// and surfaces a consistent set of local states:
//
//   - no-hash             : the node summary lacked an ``artifact_hash``.
//                           No fetch fires; the card explains the gap.
//   - loading             : PR3 hook is in flight; show a tight spinner.
//   - error               : typed ``ArtifactPayloadError`` (status 400 /
//                           404 / 500 / 503 / -1).  Surface the
//                           message + a Retry button that drives the
//                           hook's ``refetch``.  One failed payload
//                           never escapes the widget body.
//   - type-mismatch       : the payload loaded successfully but its
//                           ``artifact_type`` doesn't match what the
//                           widget renders (defensive — e.g. the
//                           registry registered ``SeriesWidget`` for
//                           ``'Series'`` but the row claims to be a
//                           Panel).  Shows a clean fallback rather
//                           than crashing.
//   - empty               : payload loaded with no inspectable body
//                           (e.g. EventSet with 0 events, Series with
//                           0 observations).  Caller decides whether
//                           this is "honest zero" or "needs a hint";
//                           shell renders a small empty caption + a
//                           hook for caller-supplied detail copy.
//
// Why a wrapper rather than inline per-widget logic
// -------------------------------------------------
// Every payload-backed widget needs the same state machine.  Inlining
// it 7 times in 60-line files produces drift; centralising here keeps
// the visual register identical across widgets, and a future addition
// (e.g. a "stale" badge if the artifact is older than X) lands in one
// place.
//
// Renderer contract
// -----------------
// Children of ``PayloadShell`` are typed against the expected envelope
// variant.  TypeScript's discriminated-union narrowing means the
// child closure sees the exact metadata + payload shape it expects;
// no in-body ``isXPayload`` guards are needed.
// ============================================================================

import { AlertCircle, FileQuestion, Loader2, RotateCcw } from 'lucide-react';
import { useArtifactPayload } from '@/hooks/useArtifactPayload';
import {
  ArtifactPayloadError,
  type ArtifactPayloadErrorStatus,
} from '@/services/workspaceApi';
import type {
  ArtifactPayloadResponse,
  ArtifactType,
} from '@/types/artifacts';

interface Props<T extends ArtifactPayloadResponse> {
  /** Hash to load (canonical: ``node.artifact_hash``).  Null /
   *  undefined → no-hash state. */
  artifactHash: string | null | undefined;
  /** Expected ``artifact_type`` discriminator.  When the loaded
   *  payload doesn't match, the shell renders a type-mismatch state
   *  rather than handing the wrong shape to the renderer. */
  expectedType: ArtifactType;
  /** Caller-supplied display label for log / error copy.  Defaults to
   *  ``expectedType`` when omitted. */
  displayName?: string;
  /** Predicate that returns true when the payload is "empty" for this
   *  artifact kind.  EventSet: 0 events.  Series: 0 observations.
   *  Panel: 0 rows.  Etc.  When omitted, no empty branch is taken. */
  isEmpty?: (payload: T) => boolean;
  /** Caller-supplied empty-state copy.  Defaults to a generic "no
   *  inspectable contents" message. */
  emptyMessage?: string;
  /** Render fn for the success path.  Receives the typed envelope.
   *  Pure-presentational: no side effects allowed in here. */
  children: (payload: T) => React.ReactNode;
}

export function PayloadShell<T extends ArtifactPayloadResponse>({
  artifactHash,
  expectedType,
  displayName,
  isEmpty,
  emptyMessage,
  children,
}: Props<T>) {
  const { data, isLoading, error, refetch } = useArtifactPayload(artifactHash);
  const label = displayName ?? expectedType;

  if (!artifactHash) {
    return <NoHashState label={label} />;
  }
  if (isLoading) {
    return <LoadingState label={label} />;
  }
  if (error) {
    return (
      <ErrorState
        label={label}
        hash={artifactHash}
        error={error}
        onRetry={refetch}
      />
    );
  }
  if (!data) {
    return <NoHashState label={label} />;
  }
  if (data.artifact_type !== expectedType) {
    return (
      <TypeMismatchState
        label={label}
        hash={artifactHash}
        expected={expectedType}
        got={data.artifact_type}
      />
    );
  }
  // Defensive narrowing — TypeScript's discriminator narrowing doesn't
  // carry the generic ``T`` through the ``expectedType`` check, so we
  // cast once here.  The runtime check above guarantees the cast is
  // safe.
  const typed = data as unknown as T;
  if (isEmpty && isEmpty(typed)) {
    return <EmptyState label={label} message={emptyMessage} />;
  }
  return <>{children(typed)}</>;
}

// ----------------------------------------------------------------------------
// State views
// ----------------------------------------------------------------------------

function LoadingState({ label }: { label: string }) {
  return (
    <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-2 px-5 py-8 text-fg-muted">
      <Loader2 size={14} className="animate-spin text-ice-300" />
      <span className="text-[11px]">Loading {label}…</span>
    </div>
  );
}

function NoHashState({ label }: { label: string }) {
  return (
    <div className="flex min-h-0 flex-1 items-start gap-2 px-5 py-4">
      <FileQuestion
        size={13}
        className="mt-0.5 shrink-0 text-amber-300"
        aria-hidden
      />
      <div className="min-w-0">
        <div className="text-[11.5px] font-semibold text-fg-primary">
          No persisted artifact for {label}
        </div>
        <div className="mt-1 text-[10.5px] leading-[1.5] text-fg-secondary">
          The node didn&apos;t persist an{' '}
          <code className="font-mono text-[10.5px] text-fg-muted">
            artifact_hash
          </code>
          ; re-run the workspace to populate one.
        </div>
      </div>
    </div>
  );
}

function ErrorState({
  label,
  hash,
  error,
  onRetry,
}: {
  label: string;
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
          Couldn&apos;t load {label} payload
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

function TypeMismatchState({
  label,
  hash,
  expected,
  got,
}: {
  label: string;
  hash: string;
  expected: string;
  got: string;
}) {
  return (
    <div className="flex min-h-0 flex-1 items-start gap-2 px-5 py-4">
      <AlertCircle size={13} className="mt-0.5 shrink-0 text-amber-300" />
      <div className="min-w-0">
        <div className="text-[11.5px] font-semibold text-fg-primary">
          {label} renderer received an unexpected payload type
        </div>
        <div className="mt-1 font-mono text-[10px] text-fg-faint">
          {hash.slice(0, 12)}…
        </div>
        <div className="mt-2 text-[10.5px] leading-[1.5] text-fg-secondary">
          The widget expected a{' '}
          <code className="font-mono text-fg-secondary">{expected}</code>{' '}
          artifact but the payload came back as{' '}
          <code className="font-mono text-fg-secondary">{got}</code>.  This is
          usually a workspace-summary / artifact-type mismatch in the
          backend; report the workspace slug for investigation.
        </div>
      </div>
    </div>
  );
}

function EmptyState({
  label,
  message,
}: {
  label: string;
  message: string | undefined;
}) {
  return (
    <div className="flex min-h-0 flex-1 items-start gap-2 px-5 py-4">
      <FileQuestion
        size={13}
        className="mt-0.5 shrink-0 text-fg-faint"
        aria-hidden
      />
      <div className="min-w-0">
        <div className="text-[11.5px] font-semibold text-fg-primary">
          {label} loaded with no inspectable contents
        </div>
        <div className="mt-1 text-[10.5px] leading-[1.5] text-fg-secondary">
          {message ??
            'The artifact ran and persisted, but its payload body is empty.'}
        </div>
      </div>
    </div>
  );
}

function statusLabel(status: ArtifactPayloadErrorStatus | null): string {
  if (status === null) return 'fetch error';
  if (status === -1) return 'network error';
  return `HTTP ${status}`;
}
