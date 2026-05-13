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

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { AlertCircle, RefreshCw } from 'lucide-react';
import { useCopilotContext } from '@/context/CopilotContext';
import { useWorkspaceDetail } from '@/hooks/useWorkspaceDetail';
import { WorkspacesSidebar } from './sidebar/WorkspacesSidebar';
import { WorkspaceCopilotRail } from './copilot-rail/WorkspaceCopilotRail';
import { BuildEmptyState } from './empty/BuildEmptyState';
import { BuildBuilding } from './building/BuildBuilding';
import { BuildCompleted } from './completed/BuildCompleted';
import { WorkspaceOverridesProvider } from './lib/workspaceOverridesContext';
import { VirtualPrimitiveCanvas } from './primitive/VirtualPrimitiveCanvas';
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

  // Phase R1.3 — when Ask hands off a single-primitive analysis via
  // ``/workspace?context=<encoded>``, BuildShell short-circuits the
  // empty-state / building-state machine and mounts a virtual primitive
  // canvas.  The canvas fetches the matching typed-detail endpoint and
  // renders the result (Spread / CrossMarket / Butterfly / Yield /
  // Regime / Scanner / Forward).  No workspace is persisted — this is
  // the supervisor-turn handoff path.
  const [searchParams] = useSearchParams();
  const contextParam = searchParams.get('context');

  // ``pendingPrompt`` carries the user's last empty-state submission
  // so the building view can echo it back.  Cleared once we navigate
  // away to the slug-bound view (component unmounts) — no manual
  // teardown needed.
  const [pendingPrompt, setPendingPrompt] = useState<string | null>(null);

  // Watch the message buffer for the workflow_result that carries a
  // persisted workspace.slug.  When we see one — AND it belongs to
  // the turn we kicked off via ``pendingPrompt`` — we navigate to
  // the slug-bound view.
  //
  // We key on "the most recent assistant message with a non-null
  // workflow.workspace.slug".  Browser refresh between submit and
  // result loses ``pendingPrompt`` (component unmounts) but that's
  // benign — the workspace is persisted; the user can re-open it
  // from the sidebar.
  useEffect(() => {
    if (!pendingPrompt) return;
    const lastAssistant = [...messages]
      .reverse()
      .find((m) => m.role === 'assistant');
    const slugFromResult = lastAssistant?.workflow?.workspace?.slug ?? null;
    if (slugFromResult) {
      navigate(`/workspace/${slugFromResult}`, { replace: false });
    }
  }, [messages, pendingPrompt, navigate]);

  const handleSend = useCallback(
    (content: string) => {
      const trimmed = content.trim();
      if (!trimmed) return;
      setPendingPrompt(trimmed);
      sendMessage(trimmed);
    },
    [sendMessage],
  );

  // Building mode is gated on EITHER a pending prompt with no result
  // yet OR ``isThinking`` from the global session.  Once a workflow
  // result lands AND it carries a workspace.slug, the effect above
  // navigates away and this component unmounts.  When the result
  // lands WITHOUT a slug (persistence failed), we fall back to the
  // empty state with a soft error caption — the user can still
  // re-send.
  const isBuilding = pendingPrompt != null && isThinking;
  const composerDisabled =
    connectionStatus !== 'ready' || isThinking;

  // Pick the centre-column canvas: virtual primitive (Ask handoff) >
  // building (prompt in flight) > empty state.  The order matters —
  // when the user clicks "Open in Build" mid-thinking, the context
  // param wins so the handoff target is rendered immediately.
  let canvas: React.ReactNode;
  if (contextParam) {
    canvas = <VirtualPrimitiveCanvas contextParam={contextParam} />;
  } else if (isBuilding) {
    canvas = <BuildBuilding prompt={pendingPrompt!} />;
  } else {
    canvas = (
      <BuildEmptyState
        onSend={handleSend}
        composerDisabled={composerDisabled}
      />
    );
  }

  return (
    <BuildShellLayout
      canvas={canvas}
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

  // Slug-bound shell — wrap in the overrides provider so the
  // Parameters tab AND the copilot rail share one queue.  The
  // provider needs a concrete workspace to bind its fork pipeline
  // to; gate the wrap on the detail being loaded.
  if (detail) {
    return (
      <WorkspaceOverridesProvider workspace={detail}>
        <BuildShellLayout
          canvas={<BuildCompleted detail={detail} replay={replay} />}
          copilotRail={
            <WorkspaceCopilotRail
              mode="completed"
              workspaceTitle={workspaceTitle}
              workspace={detail}
            />
          }
        />
      </WorkspaceOverridesProvider>
    );
  }

  // Loading / error fall-through — render the layout without the
  // overrides provider since there's no workspace to bind to yet.
  return (
    <BuildShellLayout
      canvas={
        error ? (
          <ErrorCanvas slug={slug} message={String(error.message)} />
        ) : (
          <LoadingCanvas />
        )
      }
      copilotRail={
        <WorkspaceCopilotRail
          mode="completed"
          workspaceTitle={workspaceTitle}
          workspace={null}
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
