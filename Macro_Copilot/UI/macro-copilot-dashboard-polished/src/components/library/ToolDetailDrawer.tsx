// ============================================================================
// ToolDetailDrawer — slide-in panel from the right, full per-tool detail
// ----------------------------------------------------------------------------
// Sections, top to bottom:
//   1. Header — kicker (cat · bucket), title, mono ref, sub-agent badge
//   2. What it does — expanded one_liner from manifest
//   3. Bucket rationale — why it's deterministic / statistical
//   4. Controls — always-visible section showing user-overridable
//      controls + a placeholder for locked methodology conventions.
//      Future-proofed: when V2 surfaces the full convention table
//      from each tool's config.yaml inline, the rendering shape
//      doesn't change — just the data.
//   5. Related tools — clickable chips that swap the drawer's tool
//   6. Where it's used — workflow/UI surfaces
//   7. References — academic citations
//   8. CTA row — Try in Ask · Open in Build
//
// What's intentionally NOT here:
//   - Implementation file paths (mcp_server.py, schema.py,
//     compute.py, config.yaml).  Those are developer-facing
//     plumbing that leaked into V1 and was removed — a PM doesn't
//     care about Python file locations.  The manifest still carries
//     them in the backend response (canonical source of truth);
//     the UI just doesn't render them.
//
// Slides from the right (~520px wide).  Closing on Esc + on backdrop
// click is wired in the parent (LibraryPage) which owns the open
// state.
// ============================================================================

import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  ArrowRight,
  ArrowUpRight,
  ChevronRight,
  Lock,
  Sliders,
  Sparkles,
  X,
} from 'lucide-react';
import {
  BUCKET_LABELS,
  CATEGORY_LABELS,
  CATEGORY_TONE,
  SUB_AGENT_LABELS,
  type ManifestTool,
} from '@/types/library';
import {
  isKnownBackendTool,
  isRunnablePrimitive,
  isUnsupportedKnownTool,
  normalizeToolName,
} from '@/lib/toolNames';
import { cn } from '@/utils/cn';
import { prettyTitle } from './lib/prettyTitle';

type Props = {
  tool: ManifestTool | null;
  /** Lookup fn for related-tool chip clicks — given a tool_function
   *  name (e.g. "calculate_curve_spread_tool"), return the matching
   *  ManifestTool from the loaded manifest, or null. */
  resolveRelated: (toolFunctionName: string) => ManifestTool | null;
  onOpenRelated: (tool: ManifestTool) => void;
  onClose: () => void;
};

export function ToolDetailDrawer({
  tool,
  resolveRelated,
  onOpenRelated,
  onClose,
}: Props) {
  // Close on Escape
  useEffect(() => {
    if (!tool) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [tool, onClose]);

  if (!tool) return null;

  return (
    <div className="fixed inset-0 z-50 flex" aria-modal role="dialog">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-ink-900/65 backdrop-blur-sm"
        onClick={onClose}
      />
      {/* Drawer panel */}
      <div className="relative ml-auto flex h-full w-full max-w-[560px] flex-col overflow-hidden bg-[linear-gradient(180deg,rgba(255,255,255,0.025),rgba(255,255,255,0.006)_55%),rgba(16,16,16,0.85)] shadow-[-32px_0_60px_-24px_rgba(0,0,0,0.7),inset_1px_0_0_rgba(255,255,255,0.04)]">
        <DrawerHeader tool={tool} onClose={onClose} />
        <DrawerBody
          tool={tool}
          resolveRelated={resolveRelated}
          onOpenRelated={onOpenRelated}
        />
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------------

function DrawerHeader({ tool, onClose }: { tool: ManifestTool; onClose: () => void }) {
  const tone = CATEGORY_TONE[tool.category] ?? 'data';
  const railColor = railColorFor(tone);

  return (
    <div
      className="relative shrink-0 px-6 pb-5 pt-5"
      style={{ ['--rail-color' as string]: railColor }}
    >
      <span aria-hidden className="research-card-rail" />
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <p className="font-mono text-[10px] font-medium uppercase tracking-[0.16em] text-fg-muted">
            {CATEGORY_LABELS[tool.category] ?? tool.category} ·{' '}
            {BUCKET_LABELS[tool.bucket] ?? tool.bucket}
          </p>
          <h2 className="mt-2 text-[22px] font-light leading-[1.1] tracking-[-0.018em] text-fg-primary">
            {prettyTitle(tool.name)}
          </h2>
          <p className="mt-1.5 font-mono text-[12px] tracking-[-0.005em] text-ice-300/80">
            {tool.implementation.tool_function}
          </p>
          <div className="mt-3 flex items-center gap-1.5">
            <Pill>{(SUB_AGENT_LABELS[tool.sub_agent] ?? tool.sub_agent).toUpperCase()}</Pill>
            <Pill muted>RATES</Pill>
          </div>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close"
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-fg-secondary transition-colors hover:bg-white/[0.04] hover:text-fg-primary"
        >
          <X size={14} />
        </button>
      </div>
    </div>
  );
}

function DrawerBody({
  tool,
  resolveRelated,
  onOpenRelated,
}: {
  tool: ManifestTool;
  resolveRelated: (name: string) => ManifestTool | null;
  onOpenRelated: (t: ManifestTool) => void;
}) {
  const navigate = useNavigate();

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      {/* What it does */}
      <Section title="WHAT IT DOES">
        <p className="text-[13.5px] leading-[1.6] text-fg-secondary">
          {tool.one_liner}
        </p>
      </Section>

      {/* Methodology / bucket rationale */}
      <Section title="METHODOLOGY · BUCKET RATIONALE">
        <p className="text-[13px] leading-[1.6] text-fg-secondary">
          {tool.bucket_rationale}
        </p>
      </Section>

      {/* Controls — always rendered.  Two structural sub-sections:
          USER-OVERRIDABLE (the conventions a PM can set per call)
          and LOCKED (the methodology conventions that ship inside
          the tool's config.yaml and aren't user-mutable).  The
          locked subsection is a placeholder in V1 — we know the
          number of locked conventions exists, we just don't
          surface them inline yet.  Future-proof: when V2 plumbs
          the full convention table from config.yaml into the
          manifest endpoint, the rendering shape doesn't change —
          we just feed it more rows. */}
      <Section title="CONTROLS">
        <ControlsSubsection
          label="User-overridable"
          icon={<Sliders size={11} />}
          count={tool.pm_overridable.length}
          tone="active"
          emptyHint="No user-overridable controls in V1 — every convention is locked. The methodology is fully deterministic given the input parameters."
        >
          {tool.pm_overridable.length > 0 && (
            <ul className="space-y-1.5">
              {tool.pm_overridable.map((c) => (
                <li
                  key={c}
                  className="flex items-center gap-2.5 rounded-md bg-white/[0.025] px-3 py-2 ring-1 ring-line-soft"
                >
                  <span className="h-1.5 w-1.5 rounded-full bg-mint-400 shadow-[0_0_4px_rgba(63,214,154,0.5)]" />
                  <span className="font-mono text-[12px] tracking-[-0.005em] text-fg-primary">
                    {c}
                  </span>
                  <span className="ml-auto font-mono text-[10px] uppercase tracking-[0.14em] text-fg-faint">
                    user-set
                  </span>
                </li>
              ))}
            </ul>
          )}
        </ControlsSubsection>

        <div className="h-3" />

        <ControlsSubsection
          label="Locked methodology"
          icon={<Lock size={11} />}
          count={null}
          tone="locked"
          emptyHint="Window length, fill policy, ddof, source-tag distribution, and citations live in the tool's config.yaml. The full convention table — with values, source tags, and per-row rationale — will surface inline here in V2."
        />
      </Section>

      {/* Related tools */}
      {tool.related_tools.length > 0 && (
        <Section title={`RELATED TOOLS · ${tool.related_tools.length}`}>
          <div className="flex flex-wrap gap-1.5">
            {tool.related_tools.map((rt) => {
              const resolved = resolveRelated(rt);
              if (!resolved) {
                // Tool referenced in manifest but not in current
                // catalogue (e.g. cross-agent reference).  Render as
                // disabled mono chip — informational, not clickable.
                return (
                  <span
                    key={rt}
                    title="Not in the current catalogue"
                    className="inline-flex items-center rounded-full bg-white/[0.018] px-2.5 py-1 font-mono text-[10.5px] tracking-[-0.005em] text-fg-faint ring-1 ring-line-subtle"
                  >
                    {rt}
                  </span>
                );
              }
              return (
                <button
                  key={rt}
                  type="button"
                  onClick={() => onOpenRelated(resolved)}
                  className="group inline-flex items-center gap-1 rounded-full bg-white/[0.025] px-2.5 py-1 font-mono text-[10.5px] tracking-[-0.005em] text-ice-200 ring-1 ring-line-soft transition-all hover:bg-ice-400/[0.10] hover:text-ice-100 hover:ring-ice-400/30"
                >
                  <span>{rt}</span>
                  <ChevronRight size={9} className="opacity-60 transition-transform group-hover:translate-x-0.5 group-hover:opacity-100" />
                </button>
              );
            })}
          </div>
        </Section>
      )}

      {/* Where it's used */}
      {tool.workflows.length > 0 && (
        <Section title={`USED IN · ${tool.workflows.length}`}>
          <ul className="space-y-1">
            {tool.workflows.map((w) => (
              <li
                key={w}
                className="flex items-center gap-2 rounded-md px-2 py-1.5 font-mono text-[11.5px] text-fg-secondary"
              >
                <Sparkles size={11} className="text-lineage-300/70" />
                <span>{w}</span>
              </li>
            ))}
          </ul>
        </Section>
      )}

      {/* References */}
      {tool.references.length > 0 && (
        <Section title="REFERENCES">
          <ul className="space-y-1.5">
            {tool.references.map((r, i) => (
              <li
                key={i}
                className="text-[12px] italic leading-[1.55] text-fg-secondary"
              >
                {r}
              </li>
            ))}
          </ul>
        </Section>
      )}

      {/* CTAs — pinned at the bottom of the scrollable area */}
      <div className="sticky bottom-0 mt-2 flex items-center gap-2 border-t border-line-subtle bg-ink-900/80 px-6 py-3.5 backdrop-blur-md">
        <button
          type="button"
          onClick={() => {
            // R6.5 — normalise the tool name before seeding the Ask
            // composer.  The manifest emits the un-prefixed shorthand
            // (e.g. ``half_life_tool``) but the supervisor's MCP-call
            // layer expects the backend-canonical form (``calculate_
            // half_life_tool``).  Without normalisation, the LLM gets
            // a shorthand the resolver doesn't recognise and the call
            // fails at runtime.  Drop a templated prompt into Ask's
            // composer via the existing custom-event channel — same
            // channel the legacy ChatDrawer uses; AskPage's Composer
            // listens for it.
            const canonicalName = normalizeToolName(
              tool.implementation.tool_function,
            );
            window.dispatchEvent(
              new CustomEvent('copilot:set-input', {
                detail: `Run ${canonicalName} with default params and explain the output.`,
              }),
            );
            navigate('/ask');
          }}
          className="flex h-9 flex-1 items-center justify-center gap-1.5 rounded-md text-[12.5px] font-medium text-fg-secondary ring-1 ring-line-soft transition-colors hover:bg-white/[0.025] hover:text-fg-primary"
        >
          <ArrowUpRight size={12} />
          <span>Try in Ask</span>
        </button>
        <OpenInBuildCta tool={tool} />
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------------
// R6.5 — clearer CTA wording per Build destination
// ----------------------------------------------------------------------------
//
// Every known tool gets a deterministic Build destination via the
// ``?context=`` decoder.  The button copy makes the destination
// visible so the user knows what they're about to land on instead of
// relying on the generic "Open in Build" label.

function OpenInBuildCta({ tool }: { tool: ManifestTool }) {
  const navigate = useNavigate();
  // R6.1 — the manifest emits ``tool_function`` in its un-prefixed
  // historical shorthand (``half_life_tool`` etc.), but every internal
  // registry keys on the backend-canonical prefixed form (``calculate_
  // half_life_tool``).  Normalise before lookup so both forms resolve.
  const canonicalName = normalizeToolName(tool.implementation.tool_function);
  // PR1 + PR2 + G-3.5 — every known tool gets a deterministic Build
  // destination.  The decoder maps:
  //   - ``isRunnablePrimitive`` ⇒ ``generic_builder`` (module-first
  //     dispatch mounts the owning module's dual-view Build surface
  //     when it ships one; otherwise the schema-driven builder)
  //   - ``isUnsupportedKnownTool`` ⇒ explicit unsupported-known card
  //   - everything else ⇒ ``?context=`` as a best-effort handoff;
  //     ``VirtualPrimitiveCanvas`` shows the decode-error card only
  //     when the tool name is truly unknown.
  const isGenericBuilder = isRunnablePrimitive(canonicalName);
  const isUnsupported =
    !isGenericBuilder && isUnsupportedKnownTool(canonicalName);
  const isKnown = isKnownBackendTool(canonicalName);

  const handleClick = () => {
    // Every destination rides ``?context=`` with an empty params dict.
    // The decoder picks the right variant downstream so the canvas
    // mounts the matching surface.  For unsupported / truly-unknown
    // tools the canvas surfaces an honest card; for runnable tools the
    // canvas mounts the module's Build surface or the schema-driven
    // form.
    const context = encodeURIComponent(
      JSON.stringify({
        tools: [{ tool: canonicalName, params: {} }],
        tool_count: 1,
      }),
    );
    navigate(`/workspace?context=${context}`);
  };

  // R6.5 + PR1 + PR2 — button copy + tooltip differentiate the
  // destinations so users know what they're about to land on:
  //   - runnable tool → "Open builder"
  //   - unsupported-known → "Open Build (unsupported)"
  let buttonLabel = 'Open in Build';
  let title = 'Opens the Build canvas for this tool';
  if (isGenericBuilder) {
    buttonLabel = 'Open builder';
    title =
      'Opens this tool’s Build surface — editable inputs plus a Run button that executes against the backend';
  } else if (isUnsupported) {
    buttonLabel = 'Open Build (unsupported)';
    title =
      'Build does not have a run path for this tool yet — the card explains the gap and links to Ask';
  } else if (!isKnown) {
    // Truly-unknown manifest entry — should be rare.  Still navigate
    // so the user sees the decode-error card rather than a silent
    // no-op.
    buttonLabel = 'Open in Build';
    title = 'Tool is not recognised by Build; the canvas will explain.';
  }

  return (
    <button
      type="button"
      onClick={handleClick}
      title={title}
      className="composer-send-active flex h-9 flex-1 items-center justify-center gap-1.5 rounded-md text-[12.5px] font-medium"
    >
      <ArrowRight size={12} />
      <span>{buttonLabel}</span>
    </button>
  );
}

// ----------------------------------------------------------------------------

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="border-t border-line-subtle px-6 py-4">
      <p className="mb-2 font-mono text-[10px] font-medium uppercase tracking-[0.16em] text-fg-muted">
        {title}
      </p>
      {children}
    </div>
  );
}

function ControlsSubsection({
  label,
  icon,
  count,
  tone,
  emptyHint,
  children,
}: {
  label: string;
  icon: React.ReactNode;
  /** Pass null when count isn't known (e.g. locked conventions in V1
   *  where the manifest doesn't surface the per-tool config.yaml). */
  count: number | null;
  tone: 'active' | 'locked';
  /** Copy shown when count is 0 / null — frames the absence as
   *  intentional (locked) or future-state (V2), never as "broken". */
  emptyHint: string;
  children?: React.ReactNode;
}) {
  const isEmpty = !children || count === 0;
  const accent =
    tone === 'active'
      ? 'text-mint-300'
      : 'text-fg-muted';

  return (
    <div className="rounded-md bg-white/[0.012] p-3 ring-1 ring-line-subtle">
      <div className="mb-1.5 flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5">
          <span className={`flex h-3.5 w-3.5 items-center justify-center ${accent}`}>
            {icon}
          </span>
          <span className="text-[10.5px] font-medium uppercase tracking-[0.14em] text-fg-secondary">
            {label}
          </span>
        </div>
        <span className="font-mono text-[10px] tracking-[0.02em] text-fg-faint">
          {count === null ? '—' : count}
        </span>
      </div>
      {isEmpty ? (
        <p className="text-[11.5px] leading-[1.55] text-fg-muted">
          {emptyHint}
        </p>
      ) : (
        children
      )}
    </div>
  );
}

function Pill({ children, muted }: { children: React.ReactNode; muted?: boolean }) {
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full px-2 py-px font-mono text-[9.5px] font-medium uppercase tracking-[0.14em]',
        muted
          ? 'bg-white/[0.025] text-fg-muted ring-1 ring-line-soft'
          : 'bg-ice-400/[0.08] text-ice-200 ring-1 ring-ice-400/25',
      )}
    >
      {children}
    </span>
  );
}

function railColorFor(tone: 'data' | 'analysis' | 'anomaly'): string {
  switch (tone) {
    case 'data':     return 'rgba(122, 162, 255, 0.55)';
    case 'analysis': return 'rgba(155, 140, 255, 0.45)';
    case 'anomaly':  return 'rgba(243, 183, 85, 0.55)';
  }
}

