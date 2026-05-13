// ============================================================================
// BuildShell — top-level layout for the redesigned /workspace surface.
// ----------------------------------------------------------------------------
// PR A.
//
// Owns the page-level state machine:
//
//   ┌─────────────────────────────────────────────────────────────────────┐
//   │ /workspace            (no slug)            ─────►  BuildEmptyState  │
//   │      ↓                                                              │
//   │   user sends prompt                        ─────►  BuildBuilding    │
//   │      ↓                                                              │
//   │   workflow_result lands with workspace.slug ─────►  navigate to     │
//   │                                                      /workspace/:slug│
//   │      ↓                                                              │
//   │ /workspace/:slug      (slug present)       ─────►  BuildCompleted   │
//   └─────────────────────────────────────────────────────────────────────┘
//
// 3-column grid (workspaces sidebar | canvas | workspace-copilot rail).
// Render shape stays identical across all three canvas modes; only
// the centre column's content swaps.  This makes the sidebar +
// copilot rail feel like persistent app furniture, matching how Ask
// keeps its ThreadsRail / ContextRail mounted across turns.
//
// PR A's copilot rail is read-only; the empty-state path consumes
// the shared ``CopilotContext`` for routing (same WebSocket as Ask).
// ============================================================================

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { AlertCircle, RefreshCw } from 'lucide-react';
import { useCopilotContext } from '@/context/CopilotContext';
import { useWorkspaceDetail } from '@/hooks/useWorkspaceDetail';
import type { CopilotMessage } from '@/types/copilot';
import { WorkspacesSidebar } from './sidebar/WorkspacesSidebar';
import { WorkspaceCopilotRail } from './copilot-rail/WorkspaceCopilotRail';
import { BuildEmptyState } from './empty/BuildEmptyState';
import { BuildBuilding } from './building/BuildBuilding';
import { BuildResultStalled } from './building/BuildResultStalled';
import { BuildCompleted } from './completed/BuildCompleted';
// Side-effect import — populates the node renderer registry before
// any NodeWidgetCard renders.  Must happen at module-init time so
// ``resolveNodeRenderer`` returns real renderers, not the fallback.
import '@/components/build/widgets';

export function BuildShell() {
  const { slug } = useParams<{ slug: string }>();
  const navigate = useNavigate();

  // Slug-present → completed mode.  Slug-absent → empty | building
  // (depending on whether a prompt is in flight).
  if (slug) {
    return <SlugBoundShell slug={slug} />;
  }
  return <SlugFreeShell navigate={navigate} />;
}

// ----------------------------------------------------------------------------
// /workspace (no slug)  — empty + building modes
// ----------------------------------------------------------------------------

function SlugFreeShell({
  navigate,
}: {
  navigate: ReturnType<typeof useNavigate>;
}) {
  const { messages, sendMessage, connectionStatus, isThinking } =
    useCopilotContext();

  // ``pendingPrompt`` carries the user's last empty-state submission
  // so the building view can echo it back.  We also stash the ID of
  // the streaming assistant message that came back so the navigation
  // effect can latch onto the right turn (and not be confused by
  // older messages still in the buffer).
  const [pendingPrompt, setPendingPrompt] = useState<string | null>(null);
  // "Refine and retry" parks the previous prompt here so the empty
  // state mounts with it pre-filled in the composer.  Cleared on the
  // next send.
  const [composerSeed, setComposerSeed] = useState<string | undefined>(
    undefined,
  );
  // ID of the assistant turn we're currently tracking.  Set on the
  // first re-render after sendMessage; consumed by the navigation
  // effect + the stall renderer.  Stays sticky across re-renders so
  // the user doesn't see flicker when the message buffer rebroadcasts.
  const trackedMessageIdRef = useRef<string | null>(null);
  // ID of a turn we've already handled (navigated OR stalled).  Once
  // set, the navigation effect skips it so re-renders don't loop.
  const [resolvedMessageId, setResolvedMessageId] = useState<string | null>(
    null,
  );

  const handleSend = useCallback(
    (content: string) => {
      const trimmed = content.trim();
      if (!trimmed) return;
      setPendingPrompt(trimmed);
      setComposerSeed(undefined);
      trackedMessageIdRef.current = null;
      setResolvedMessageId(null);
      sendMessage(trimmed);
    },
    [sendMessage],
  );

  const handleDismiss = useCallback(() => {
    setPendingPrompt(null);
    setComposerSeed(undefined);
    trackedMessageIdRef.current = null;
    setResolvedMessageId(null);
  }, []);

  const handleRefine = useCallback(() => {
    // Re-seed the empty-state composer with the original prompt so
    // the user can edit it and try again without retyping.
    setComposerSeed(pendingPrompt ?? undefined);
    setPendingPrompt(null);
    trackedMessageIdRef.current = null;
    setResolvedMessageId(null);
  }, [pendingPrompt]);

  // Find the most recent NON-STREAMING assistant message.  Streaming
  // messages are still being assembled (workflow.workspace may not
  // yet be populated), so we wait for the turn to settle before
  // making a navigation / stall decision.
  const lastSettledAssistant: CopilotMessage | null = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      const m = messages[i];
      if (m.role === 'assistant' && !m.isStreaming) return m;
    }
    return null;
  }, [messages]);

  // Find the streaming assistant message — used by BuildBuilding to
  // show live per-tool trace chips + the workflow route decision.
  const streamingAssistant: CopilotMessage | null = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      const m = messages[i];
      if (m.role === 'assistant' && m.isStreaming) return m;
    }
    return null;
  }, [messages]);

  // Latch onto the FIRST settled assistant message that arrives
  // after sendMessage.  Without latching, the find() above would
  // also match an OLDER assistant message in the buffer (e.g. one
  // left over from a previous Ask turn) and short-circuit the stall
  // detection.
  useEffect(() => {
    if (!pendingPrompt) return;
    if (!lastSettledAssistant) return;
    if (trackedMessageIdRef.current == null) {
      trackedMessageIdRef.current = lastSettledAssistant.id;
    }
  }, [pendingPrompt, lastSettledAssistant]);

  // Navigation + stall decision.  Fires once per settled tracked turn.
  useEffect(() => {
    if (!pendingPrompt) return;
    if (!lastSettledAssistant) return;
    if (lastSettledAssistant.id !== trackedMessageIdRef.current) return;
    if (resolvedMessageId === lastSettledAssistant.id) return;

    const slug = lastSettledAssistant.workflow?.workspace?.slug ?? null;
    if (slug) {
      // Happy path: navigate to the slug-routed view.
      navigate(`/workspace/${encodeURIComponent(slug)}`, { replace: false });
      return;
    }
    // No slug — mark as resolved so the stall canvas renders below.
    setResolvedMessageId(lastSettledAssistant.id);
  }, [pendingPrompt, lastSettledAssistant, resolvedMessageId, navigate]);

  // Canvas selection:
  //   - mid-run (pendingPrompt set + isThinking)                 → BuildBuilding
  //   - settled with slug                                         → already navigated; render building view briefly
  //   - settled without slug (resolvedMessageId matches)         → BuildResultStalled
  //   - default                                                  → BuildEmptyState
  const stalledMessage =
    pendingPrompt &&
    lastSettledAssistant &&
    resolvedMessageId === lastSettledAssistant.id
      ? lastSettledAssistant
      : null;
  const isBuilding =
    pendingPrompt != null && isThinking && stalledMessage == null;
  const composerDisabled =
    connectionStatus !== 'ready' || isThinking;

  return (
    <BuildShellLayout
      canvas={
        isBuilding ? (
          <BuildBuilding
            prompt={pendingPrompt!}
            message={streamingAssistant}
          />
        ) : stalledMessage ? (
          <BuildResultStalled
            prompt={pendingPrompt!}
            message={stalledMessage}
            onRefine={handleRefine}
            onDismiss={handleDismiss}
          />
        ) : (
          <BuildEmptyState
            onSend={handleSend}
            composerDisabled={composerDisabled}
            composerSeed={composerSeed}
          />
        )
      }
      copilotRail={<WorkspaceCopilotRail mode="empty" />}
    />
  );
}

// ----------------------------------------------------------------------------
// /workspace/:slug — completed mode
// ----------------------------------------------------------------------------

function SlugBoundShell({ slug }: { slug: string }) {
  const { detail, replay, isLoading, error } = useWorkspaceDetail(slug);

  const workspaceTitle = useMemo(() => {
    if (!detail) return undefined;
    return detail.name?.trim() || undefined;
  }, [detail]);

  return (
    <BuildShellLayout
      canvas={
        error ? (
          <ErrorCanvas slug={slug} message={String(error.message)} />
        ) : isLoading || !detail ? (
          <LoadingCanvas />
        ) : (
          <BuildCompleted detail={detail} replay={replay} />
        )
      }
      copilotRail={
        <WorkspaceCopilotRail
          mode="completed"
          workspaceTitle={workspaceTitle}
          workspace={detail}
        />
      }
    />
  );
}

// ----------------------------------------------------------------------------
// 3-column layout primitive — shared by both modes.
// ----------------------------------------------------------------------------

function BuildShellLayout({
  canvas,
  copilotRail,
}: {
  canvas: React.ReactNode;
  copilotRail: React.ReactNode;
}) {
  return (
    <div
      className="grid h-full min-h-0 w-full"
      style={{
        gridTemplateColumns:
          'clamp(220px, 16vw, 280px) minmax(0, 1fr) clamp(300px, 22vw, 360px)',
      }}
    >
      <WorkspacesSidebar />
      <main className="min-h-0 min-w-0 overflow-hidden">{canvas}</main>
      {copilotRail}
    </div>
  );
}

// ----------------------------------------------------------------------------
// Canvas-side loading + error states.
// ----------------------------------------------------------------------------

function LoadingCanvas() {
  return (
    <div className="flex h-full min-h-0 items-center justify-center p-8">
      <div className="flex items-center gap-2 text-[12px] text-fg-muted">
        <RefreshCw size={12} className="animate-spin" />
        <span>Loading workspace…</span>
      </div>
    </div>
  );
}

function ErrorCanvas({ slug, message }: { slug: string; message: string }) {
  return (
    <div className="flex h-full min-h-0 items-center justify-center p-8">
      <div className="card max-w-[480px] flex items-start gap-3 px-5 py-4">
        <AlertCircle size={16} className="mt-0.5 shrink-0 text-coral-300" />
        <div className="min-w-0">
          <div className="text-[12.5px] font-semibold text-fg-primary">
            Could not load workspace
          </div>
          <div className="mt-1 font-mono text-[10.5px] text-fg-muted">
            /{slug}
          </div>
          <div className="mt-2 text-[11.5px] leading-[1.5] text-fg-secondary">
            {message}
          </div>
        </div>
      </div>
    </div>
  );
}
