// ============================================================================
// expandedView.tsx — Shared click-to-expand modal infrastructure for the
// multi-tool DAG (Stage D).
// ----------------------------------------------------------------------------
// Per docs_revamped/03_standards/rendering_density.md §3.3 + §10 the modal
// mounting + breadcrumb is SHARED infrastructure; per-tool compact cards
// only call ``onExpand``.  This module provides:
//
//   - ``ExpandedViewProvider`` — wraps the DAG canvas; holds the open
//     request + renders the modal.
//   - ``useOpenExtendedView()`` — the hook every compact node uses to
//     open its tool's extended view (``open(decoded, originNodeId)``).
//   - The modal itself — mounts the OWNING module's ``surfaces.buildExtended``
//     (registry lookup by toolName; GENERIC — no tool hardcoding), hydrated
//     with the node's params, with a breadcrumb back to the query, ESC /
//     click-outside dismissal.
//
// Local-edit isolation: the modal seeds LOCAL params state from the node and
// passes ``onParamsChange`` to the extended view, so editing controls inside
// the modal stays local and does NOT navigate the global URL (which would
// replace the multi-tool DAG context behind the modal).  On close the DAG is
// intact and the originating node is highlighted (``lastOpenedNodeId``).
//
// Focused-mode isolation: the extended view calls ``useRequestFocusedMode``;
// we wrap it in its OWN nested ``FocusedModeProvider`` so its request talks to
// a throwaway scope and does NOT clobber the DAG page's focused-mode state
// when the modal unmounts.
// ============================================================================

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import { ArrowLeft, X } from 'lucide-react';
import { getPrimitiveModule } from '@/modules';
import { FocusedModeProvider } from '@/components/build/lib/focusedMode';
import { GenericPrimitiveBuilder } from '../primitive/GenericPrimitiveBuilder';
import type { DecodedPrimitive } from '../primitive/contextDecoder';

interface OpenRequest {
  decoded: DecodedPrimitive;
  originNodeId: number;
}

interface ExpandedViewContextValue {
  /** Open the extended view for a decoded node.  ``originNodeId`` is the
   *  DAG node id (== source tool index) so the canvas can highlight it on
   *  return. */
  open: (decoded: DecodedPrimitive, originNodeId: number) => void;
  close: () => void;
  /** Node id of the most-recently-opened modal — the canvas reads this to
   *  highlight the originating node after the modal closes.  ``null`` until
   *  the first open. */
  lastOpenedNodeId: number | null;
}

const ExpandedViewCtx = createContext<ExpandedViewContextValue | null>(null);

/** Compact cards call this to open their tool's extended view in the shared
 *  modal.  Throws if used outside ``ExpandedViewProvider`` (a wiring bug). */
export function useOpenExtendedView(): ExpandedViewContextValue {
  const ctx = useContext(ExpandedViewCtx);
  if (!ctx) {
    throw new Error(
      'useOpenExtendedView must be used within an <ExpandedViewProvider> ' +
        '(the multi-tool DAG canvas wraps the grid in one).',
    );
  }
  return ctx;
}

/** Tolerant variant for cards that render BOTH inside and outside an
 *  ``ExpandedViewProvider`` (the persisted-slug node cards predate the
 *  expand affordance and also mount in provider-less unit-test
 *  contexts).  Returns ``null`` outside a provider — the card simply
 *  omits its expand arrow. */
export function useOpenExtendedViewOptional(): ExpandedViewContextValue | null {
  return useContext(ExpandedViewCtx);
}

export function ExpandedViewProvider({
  queryLabel,
  contextNote,
  children,
}: {
  /** Originating query / prompt — shown in the modal breadcrumb
   *  ("← Back to <query>").  Falls back to a generic label when absent. */
  queryLabel?: string;
  /** Optional honesty banner rendered inside the modal under the
   *  breadcrumb.  The persisted-slug page sets this to disclose that
   *  the expanded view is a LIVE re-query (today's numbers) while the
   *  saved card behind it stays frozen, read-only by hash (P4/P5). */
  contextNote?: string;
  children: ReactNode;
}) {
  const [request, setRequest] = useState<OpenRequest | null>(null);
  const [lastOpenedNodeId, setLastOpenedNodeId] = useState<number | null>(null);

  const open = useCallback(
    (decoded: DecodedPrimitive, originNodeId: number) => {
      setRequest({ decoded, originNodeId });
      setLastOpenedNodeId(originNodeId);
    },
    [],
  );
  const close = useCallback(() => setRequest(null), []);

  const value = useMemo<ExpandedViewContextValue>(
    () => ({ open, close, lastOpenedNodeId }),
    [open, close, lastOpenedNodeId],
  );

  return (
    <ExpandedViewCtx.Provider value={value}>
      {children}
      {request && (
        <ExpandedViewModal
          request={request}
          queryLabel={queryLabel}
          contextNote={contextNote}
          onClose={close}
        />
      )}
    </ExpandedViewCtx.Provider>
  );
}

function truncateLabel(label: string, max = 64): string {
  return label.length > max ? `${label.slice(0, max - 1)}…` : label;
}

function ExpandedViewModal({
  request,
  queryLabel,
  contextNote,
  onClose,
}: {
  request: OpenRequest;
  queryLabel?: string;
  contextNote?: string;
  onClose: () => void;
}) {
  // Local params — seeded from the node, updated by the extended view's
  // controls via onParamsChange.  Re-seed when a DIFFERENT node is opened.
  const [localParams, setLocalParams] = useState<Record<string, string>>(
    request.decoded.params,
  );
  useEffect(() => {
    setLocalParams(request.decoded.params);
  }, [request]);

  // ESC closes the modal (mirrors WidgetCatalogModal convention).
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onClose]);

  // GENERIC dispatch — look up the owning module by toolName and mount its
  // extended Build surface.  No per-tool branching: any module that ships
  // buildExtended gets the modal for free.
  const mod = getPrimitiveModule(request.decoded.toolName);
  const Extended = mod?.surfaces?.buildExtended ?? null;

  // Decoded with the locally-edited params so the extended view re-fetches
  // when controls change inside the modal.  All DecodedPrimitive variants
  // share ``params: Record<string,string>`` so the spread preserves ``kind``.
  const decodedForView = {
    ...request.decoded,
    params: localParams,
  } as DecodedPrimitive;

  const backLabel = queryLabel
    ? `Back to "${truncateLabel(queryLabel)}"`
    : 'Back to comparison';

  return (
    <div
      className="fixed inset-0 z-[60] flex flex-col"
      aria-modal
      role="dialog"
    >
      {/* Backdrop — click outside to dismiss. */}
      <div
        className="absolute inset-0 bg-ink-900/70 backdrop-blur-md"
        onClick={onClose}
        aria-hidden
      />

      {/* Modal card — large, hosts the full extended canvas. */}
      <div className="relative z-10 mx-auto my-3 flex h-[94vh] w-[min(1280px,96vw)] flex-col overflow-hidden rounded-[14px] bg-[linear-gradient(180deg,rgba(255,255,255,0.03),rgba(255,255,255,0.008)_60%),rgba(14,16,22,0.94)] shadow-[0_32px_80px_-20px_rgba(0,0,0,0.7),inset_0_1px_0_rgba(255,255,255,0.05),inset_0_0_0_1px_rgba(148,163,184,0.10)]">
        {/* Breadcrumb header. */}
        <div className="flex shrink-0 items-center justify-between gap-3 border-b border-line-subtle px-4 py-2.5">
          <button
            type="button"
            onClick={onClose}
            className="flex items-center gap-1.5 rounded-md px-2 py-1 text-[12px] font-medium text-fg-secondary transition-colors hover:bg-white/[0.04] hover:text-fg-primary"
          >
            <ArrowLeft size={13} strokeWidth={2} aria-hidden />
            {backLabel}
          </button>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close expanded view"
            className="flex h-7 w-7 items-center justify-center rounded-md text-fg-secondary transition-colors hover:bg-white/[0.04] hover:text-fg-primary"
          >
            <X size={14} aria-hidden />
          </button>
        </div>

        {/* Honesty banner (persisted-slug contexts) — discloses that
            this expanded view is a LIVE re-query while the saved card
            behind it stays frozen, read-only by hash (P4/P5). */}
        {contextNote && (
          <div className="flex shrink-0 items-center gap-2 border-b border-amber-400/15 bg-amber-400/[0.04] px-4 py-1.5">
            <span className="kicker text-amber-300">LIVE RE-QUERY</span>
            <span className="text-[11px] leading-snug text-amber-200/80">
              {contextNote}
            </span>
          </div>
        )}

        {/* Body — mirrors the single-tool Build dispatch
            (VirtualPrimitiveCanvas): a migrated tool's bespoke extended view,
            else the schema-driven builder for any runnable tool, else an
            honest "no Build surface" message.  Expanding ANY node always does
            something useful — never a dead-end. */}
        <div className="min-h-0 flex-1 overflow-auto">
          {Extended ? (
            // 1. Dual-view migrated tool — its own extended canvas, in its OWN
            // focused-mode scope so its requestFocus doesn't clobber the DAG
            // page's focused-mode state when the modal closes.  Edits stay
            // local via onParamsChange (don't navigate the global URL).
            <FocusedModeProvider>
              <Extended
                toolName={request.decoded.toolName}
                params={localParams}
                decoded={decodedForView}
                askHandoff={false}
                onParamsChange={setLocalParams}
              />
            </FocusedModeProvider>
          ) : request.decoded.kind === 'generic_builder' ? (
            // 2. Runnable tool without a bespoke view → the universal
            // schema-driven builder (configure + run in-place).  ``embedded``
            // suppresses its URL-sync so it doesn't replace the DAG context
            // behind the modal.  This is the working Build surface for every
            // tool not yet migrated to the dual-view contract.
            <GenericPrimitiveBuilder
              toolName={request.decoded.toolName}
              initialParams={request.decoded.paramsStructured}
              embedded
            />
          ) : (
            // 3. Paused / workflow-incompatible tool — no Build run path.
            <div className="flex h-full items-center justify-center p-8 text-center text-[12.5px] text-fg-secondary">
              <span>
                <code className="font-mono text-fg-primary">
                  {request.decoded.toolName}
                </code>{' '}
                is known to the backend but has no Build run surface yet —
                explore it in Ask while it's wired in.
              </span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
