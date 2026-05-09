// ============================================================================
// ToolDetailDrawer — slide-in panel from the right, full per-tool detail
// ----------------------------------------------------------------------------
// Sections, top to bottom:
//   1. Header — kicker (cat · bucket), title, mono ref, sub-agent badge
//   2. What it does — expanded one_liner from manifest
//   3. Bucket rationale — why it's deterministic / statistical
//   4. PM-overridable controls — the conventions a user can set at
//      call time, with their default values (if surfaced in
//      pm_overridable list).  V1 just shows the names; V2 will pull
//      defaults from the runtime config.yaml.
//   5. Implementation — collapsed list of file paths
//   6. Related tools — clickable chips that swap the drawer's tool
//   7. Where it's used — workflow/UI surfaces
//   8. References — academic citations
//   9. CTA row — Try in Ask · Open in Build
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
  ExternalLink,
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
import { cn } from '@/utils/cn';

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

      {/* PM-overridable conventions */}
      <Section title={`CONTROLS · ${tool.pm_overridable.length} PM-OVERRIDABLE`}>
        {tool.pm_overridable.length === 0 ? (
          <p className="text-[12.5px] leading-[1.55] text-fg-muted">
            All conventions are YAML-locked for this tool. The PM exposes
            no override path in V1 — the methodology is fully
            deterministic given the input parameters.
          </p>
        ) : (
          <ul className="space-y-1.5">
            {tool.pm_overridable.map((c) => (
              <li
                key={c}
                className="flex items-center gap-2.5 rounded-md bg-white/[0.022] px-3 py-2 ring-1 ring-line-soft"
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
      </Section>

      {/* Implementation */}
      <Section title="IMPLEMENTATION">
        <ul className="space-y-1">
          <ImplRow label="MCP server" value={tool.implementation.mcp_server} />
          <ImplRow label="Tool fn" value={tool.implementation.tool_function} />
          <ImplRow label="Schema" value={tool.implementation.schema_} />
          <ImplRow label="Compute" value={tool.implementation.compute} />
          <ImplRow label="Config" value={tool.implementation.config} />
        </ul>
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
            // Drop a templated prompt into Ask's composer via the
            // existing custom-event channel.  Same channel the legacy
            // ChatDrawer uses; AskPage's Composer listens for it.
            window.dispatchEvent(
              new CustomEvent('copilot:set-input', {
                detail: `Run ${tool.implementation.tool_function} with default params and explain the output.`,
              }),
            );
            navigate('/ask');
          }}
          className="flex h-9 flex-1 items-center justify-center gap-1.5 rounded-md text-[12.5px] font-medium text-fg-secondary ring-1 ring-line-soft transition-colors hover:bg-white/[0.025] hover:text-fg-primary"
        >
          <ArrowUpRight size={12} />
          <span>Try in Ask</span>
        </button>
        <button
          type="button"
          onClick={() => {
            // Build/Workspace integration is V2 — for now route the
            // user to the existing /workspace surface.  When Build
            // ships proper deep-linking, swap this for an URL with
            // pre-filled tool params.
            navigate('/workspace');
          }}
          className="composer-send-active flex h-9 flex-1 items-center justify-center gap-1.5 rounded-md text-[12.5px] font-medium"
        >
          <ArrowRight size={12} />
          <span>Open in Build</span>
        </button>
      </div>
    </div>
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

function ImplRow({ label, value }: { label: string; value: string }) {
  return (
    <li className="flex items-center gap-2 rounded-md bg-white/[0.014] px-2.5 py-1.5 ring-1 ring-line-subtle">
      <span className="w-[80px] shrink-0 text-[10px] font-medium uppercase tracking-[0.14em] text-fg-faint">
        {label}
      </span>
      <span className="min-w-0 flex-1 truncate font-mono text-[11px] tracking-[-0.005em] text-fg-secondary">
        {value}
      </span>
      <ExternalLink size={10} className="shrink-0 text-fg-faint" />
    </li>
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

function prettyTitle(name: string): string {
  const stripped = name.replace(/^(calculate|get|scan|classify)_/, '');
  return stripped
    .split('_')
    .map((p) => p.charAt(0).toUpperCase() + p.slice(1))
    .join(' ');
}
