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
import { AlertCircle, ChevronLeft, ChevronRight, RefreshCw } from 'lucide-react';
import { useCopilotContext } from '@/context/CopilotContext';
import { useWorkspaceDetail } from '@/hooks/useWorkspaceDetail';
import { WorkspacesSidebar } from './sidebar/WorkspacesSidebar';
import { WorkspaceCopilotRail } from './copilot-rail/WorkspaceCopilotRail';
import { BuildEmptyState } from './empty/BuildEmptyState';
import { BuildBuilding } from './building/BuildBuilding';
import { BuildCompleted } from './completed/BuildCompleted';
import { WorkspaceOverridesProvider } from './lib/workspaceOverridesContext';
import {
  FocusedModeProvider,
  resolvePanelVisibility,
  useFocusedMode,
} from './lib/focusedMode';
import { VirtualPrimitiveCanvas } from './primitive/VirtualPrimitiveCanvas';
// Stage D — the generic multi-tool DAG page replaces the legacy
// MultiPrimitiveCanvas for ≥2-tool contexts.  Registry-driven (renders each
// tool's surfaces.buildCompact); tool-agnostic.
import { MultiToolDagCanvas } from './multitool/MultiToolDagCanvas';
import { decodePrimitiveList } from './primitive/contextDecoder';
import { isAskHandoff } from './primitive/handoffSignal';
import { WorkflowStatusCanvas } from './workflow/WorkflowStatusCanvas';
// Side-effect import — populates the node renderer registry before
// any NodeWidgetCard renders.  Must happen at module-init time so
// ``resolveNodeRenderer`` returns real renderers, not the fallback.
import '@/components/build/widgets';

export function BuildShell() {
  const { slug } = useParams<{ slug: string }>();
  const navigate = useNavigate();

  // FocusedModeProvider wraps both shell variants so per-tool
  // surfaces (mounted deep inside either variant's canvas) can
  // request focused mode via ``useRequestFocusedMode``.  See
  // docs_revamped/03_standards/rendering_density.md §2.1 — the
  // extended view owns the WHOLE canvas; the sidebar + rail
  // around it are shell furniture that collapses on request.
  return (
    <FocusedModeProvider>
      {slug ? <SlugBoundShell slug={slug} /> : <SlugFreeShell navigate={navigate} />}
    </FocusedModeProvider>
  );
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
  //
  // Phase R4 (retired chassis, kept deep-link) —
  // ``/workspace?builder=<tool_name>`` opens the single-tool module-
  // first canvas for the given tool.  Extra URL params other than
  // ``builder`` are forwarded as initial form values so deep-links can
  // pre-fill the controls.
  const [searchParams] = useSearchParams();
  const contextParam = searchParams.get('context');
  const builderParam = searchParams.get('builder');
  // PR1 — explicit workflow-status handoff.  When a workflow turn from
  // Ask has a recognised ``template_id`` but no persisted workspace
  // slug (paused template / persistence failed / etc.),
  // ``ActionRow.resolveBuildHref`` routes here with ``?workflow=<id>``
  // so we can surface an honest "workflow paused / unavailable" card
  // instead of the empty Build shell.
  const workflowParam = searchParams.get('workflow');
  const workflowStatusParam = searchParams.get('workflow_status');
  const initialBuilderParams: Record<string, string> = {};
  const RESERVED_URL_KEYS = new Set([
    'builder',
    'context',
    'workflow',
    'workflow_status',
  ]);
  searchParams.forEach((value, key) => {
    if (!RESERVED_URL_KEYS.has(key)) {
      initialBuilderParams[key] = value;
    }
  });

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

  // Pick the centre-column canvas:
  //   builder (explicit tile / Library click)
  //     > workflow-status card (PR1, paused / unavailable workflow
  //       without persisted slug)
  //     > virtual primitive single-card (one Ask tool)
  //     > virtual primitive multi-card (N Ask tools, R6.3)
  //     > building (prompt in flight)
  //     > empty state
  //
  // Builder wins over context because if the user explicitly asked
  // for a tool builder the URL says so directly.  Workflow-status
  // wins over context because the workflow handoff happens when a
  // template_id is recognised but the slug didn't materialise — we
  // want the honest paused / unavailable card, not the per-tool
  // primitive canvas (which would only render the supervisor's
  // workspace_context, hiding the workflow's status).
  let canvas: React.ReactNode;
  if (builderParam !== null) {
    // G-3.5 — the legacy BuilderCanvas chassis is retired.  The
    // ``?builder=<tool>`` deep-link survives for old bookmarks /
    // share links, but it now routes through the SAME module-first
    // single-tool canvas the ``?context=`` path uses: the owning
    // module's ``surfaces.buildExtended`` when it ships the dual-view
    // contract (every module does today), the schema-driven generic
    // builder for any other runnable tool, and the honest unsupported
    // card otherwise.  Extra URL params still pre-fill the controls.
    const syntheticContext = encodeURIComponent(
      JSON.stringify({
        tools: [{ tool: builderParam, params: initialBuilderParams }],
        tool_count: 1,
      }),
    );
    canvas = <VirtualPrimitiveCanvas contextParam={syntheticContext} />;
  } else if (workflowParam !== null) {
    canvas = (
      <WorkflowStatusCanvas
        templateId={workflowParam}
        status={workflowStatusParam}
      />
    );
  } else if (contextParam) {
    canvas = (
      <ContextCanvasRouter
        contextParam={contextParam}
        searchParams={searchParams}
      />
    );
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
// R6.3 — single vs multi canvas selector
// ----------------------------------------------------------------------------
//
// One place to make the single vs multi decision so it stays consistent
// regardless of how the user landed on the route (Library deep-link,
// Ask handoff, manual URL paste).  The priority chain is:
//
//   1. Multiple tool calls (≥2) → MultiToolDagCanvas (Stage-D generic
//      multi-tool DAG page: query header + node/edge strip + a cards body
//      that renders each tool's surfaces.buildCompact via the registry).
//   2. Otherwise → VirtualPrimitiveCanvas (single-card module-first
//      dispatch + the decode-error path for unrecognised tools).

function ContextCanvasRouter({
  contextParam,
  searchParams,
}: {
  contextParam: string;
  /** PR-B-β — full ``URLSearchParams`` so we can read the
   *  ``handoff=ask`` marker.  Library-blank opens lack the marker →
   *  existing silent-defaults behaviour is preserved. */
  searchParams: URLSearchParams;
}) {
  // Compute the handoff origin ONCE at the router level — every
  // downstream surface inherits the same value so the visible
  // behaviour stays consistent across the page.
  const askHandoff = isAskHandoff(searchParams);
  const list = decodePrimitiveList(contextParam);
  if (list.length > 1) {
    return <MultiToolDagCanvas contextParam={contextParam} />;
  }
  return (
    <VirtualPrimitiveCanvas
      contextParam={contextParam}
      askHandoff={askHandoff}
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
  // Focused mode (per docs_revamped/03_standards/rendering_density.md):
  //   - Default: 3-column grid (sidebar | canvas | rail)
  //   - Focused: canvas-only with small edge-toggle buttons letting the
  //     user re-open either panel on demand
  //   - User overrides per panel are respected even in focused mode
  const focusCtx = useFocusedMode();
  const isFocused = focusCtx?.isFocused ?? false;
  const showSidebar = resolvePanelVisibility(
    isFocused,
    focusCtx?.sidebarOverride ?? null,
  );
  const showRail = resolvePanelVisibility(
    isFocused,
    focusCtx?.railOverride ?? null,
  );

  // Compute grid template based on which panels are visible.
  const sidebarCol = showSidebar ? 'clamp(220px, 16vw, 280px)' : '0px';
  const railCol = showRail ? 'clamp(300px, 22vw, 360px)' : '0px';
  const gridTemplateColumns = `${sidebarCol} minmax(0, 1fr) ${railCol}`;

  return (
    <div
      className="relative grid h-full min-h-0 w-full transition-[grid-template-columns] duration-200 ease-out"
      style={{ gridTemplateColumns }}
    >
      {showSidebar ? <WorkspacesSidebar /> : <div aria-hidden />}
      <main className="relative min-h-0 min-w-0 overflow-hidden">
        {canvas}
        {/* Edge toggle buttons — only render when focused mode is
            active OR a user has explicitly hidden a panel.  Otherwise
            the buttons are hidden so the default 3-col layout stays
            chrome-free. */}
        {focusCtx && (
          <EdgeTogglesOverlay
            showSidebar={showSidebar}
            showRail={showRail}
            onToggleSidebar={focusCtx.toggleSidebar}
            onToggleRail={focusCtx.toggleRail}
            visible={isFocused || focusCtx.sidebarOverride === false || focusCtx.railOverride === false}
          />
        )}
      </main>
      {showRail ? copilotRail : <div aria-hidden />}
    </div>
  );
}

/** Floating edge toggle buttons — appear on the left/right edges of
 *  the canvas in focused mode to let the user re-open hidden panels.
 *  Buttons render INSIDE the canvas main element (absolutely
 *  positioned) so they ride along with the canvas width and don't
 *  consume their own grid track. */
function EdgeTogglesOverlay({
  showSidebar,
  showRail,
  onToggleSidebar,
  onToggleRail,
  visible,
}: {
  showSidebar: boolean;
  showRail: boolean;
  onToggleSidebar: () => void;
  onToggleRail: () => void;
  visible: boolean;
}) {
  if (!visible) return null;
  return (
    <>
      <button
        type="button"
        onClick={onToggleSidebar}
        title={showSidebar ? 'Hide workspaces sidebar' : 'Show workspaces sidebar'}
        aria-label={showSidebar ? 'Hide workspaces sidebar' : 'Show workspaces sidebar'}
        className="absolute left-0 top-1/2 z-20 flex h-12 w-5 -translate-y-1/2 items-center justify-center rounded-r-md border border-l-0 border-line-subtle bg-bg-elevated/85 text-fg-muted backdrop-blur transition-colors hover:bg-bg-elevated hover:text-fg-secondary"
      >
        {showSidebar ? (
          <ChevronLeft size={12} strokeWidth={1.75} />
        ) : (
          <ChevronRight size={12} strokeWidth={1.75} />
        )}
      </button>
      <button
        type="button"
        onClick={onToggleRail}
        title={showRail ? 'Hide copilot rail' : 'Show copilot rail'}
        aria-label={showRail ? 'Hide copilot rail' : 'Show copilot rail'}
        className="absolute right-0 top-1/2 z-20 flex h-12 w-5 -translate-y-1/2 items-center justify-center rounded-l-md border border-r-0 border-line-subtle bg-bg-elevated/85 text-fg-muted backdrop-blur transition-colors hover:bg-bg-elevated hover:text-fg-secondary"
      >
        {showRail ? (
          <ChevronRight size={12} strokeWidth={1.75} />
        ) : (
          <ChevronLeft size={12} strokeWidth={1.75} />
        )}
      </button>
    </>
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
