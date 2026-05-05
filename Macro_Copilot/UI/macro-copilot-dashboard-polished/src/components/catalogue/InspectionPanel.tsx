// ============================================================================
// InspectionPanel
// ----------------------------------------------------------------------------
// Reusable slide-over panel that renders a four-tab inspection layer over
// either a workflow template OR a primitive tool.  The tab structure
// mirrors the model-inspection mock:
//
//   1. What it does           — short prose + key output / use cases
//   2. How it works           — step-by-step methodology + DAG / formula
//   3. Assumptions & Controls — read-only conventions ("knobs") + slot
//                               schema with valid_range / valid_values
//   4. Interpretation         — assumptions + planned_extensions + how to
//                               read the output
//
// The panel is read-only — knob editing is NOT implemented in this PR
// (the configurations surface is a preview of the editor, not a live one).
// ============================================================================

import type { ReactNode } from 'react';
import { useEffect, useRef, useState } from 'react';
import { ChevronLeft, ChevronRight, Sparkles, X } from 'lucide-react';
import type {
  ToolCard,
  ToolConventionDescriptor,
  ToolFieldDescriptor,
  WorkflowTemplateCard,
  SlotDeclaration,
} from '@/types/workflows';
import { cn } from '@/utils/cn';

type InspectionPanelProps = {
  open: boolean;
  onClose: () => void;
  // Either (tool) OR (workflow), not both.
  tool?: ToolCard | null;
  workflow?: WorkflowTemplateCard | null;
  /** Optional CTA — when supplied, shown in the panel footer next to the
   *  tab navigator.  For tools this routes into PrimitiveModelView in the
   *  workspace; for workflows it pre-fills the chat composer with a
   *  starter prompt (handled by the catalogue page). */
  onOpenInWorkspace?: () => void;
  primaryCtaLabel?: string;
};

const TABS = [
  { id: 'what', label: 'What it does' },
  { id: 'how', label: 'How it works' },
  { id: 'controls', label: 'Assumptions & Controls' },
  { id: 'interpret', label: 'Interpretation' },
] as const;

type TabId = (typeof TABS)[number]['id'];

export function InspectionPanel({
  open,
  onClose,
  tool,
  workflow,
  onOpenInWorkspace,
  primaryCtaLabel,
}: InspectionPanelProps) {
  const [tab, setTab] = useState<TabId>('what');
  const dialogRef = useRef<HTMLDivElement>(null);

  // Close on Escape.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  // Reset tab when the panel changes target.
  useEffect(() => {
    setTab('what');
  }, [tool?.tool_name, workflow?.template_id]);

  if (!open) return null;
  const subject = tool ?? workflow ?? null;
  if (!subject) return null;

  const isTool = !!tool;
  const title = isTool ? tool!.tool_name : workflow!.template_id;
  const subtitle = isTool ? tool!.description : workflow!.description;
  const kind = isTool ? 'PRIMITIVE TOOL' : 'WORKFLOW TEMPLATE';
  const bucket = isTool
    ? tool!.category ?? tool!.domain
    : workflow!.archetype;

  return (
    <div className="fixed inset-0 z-50 flex">
      {/* Scrim */}
      <button
        aria-label="Close panel"
        onClick={onClose}
        className="flex-1 bg-ink-950/70 backdrop-blur-sm transition-opacity"
      />
      {/* Slide-over panel */}
      <div
        ref={dialogRef}
        className="relative flex h-full w-[min(720px,92vw)] flex-col overflow-hidden border-l border-line-soft bg-ink-900 shadow-[-12px_0_60px_-12px_rgba(0,0,0,0.7)]"
      >
        <PanelHeader
          kind={kind}
          bucket={bucket}
          title={title}
          subtitle={subtitle}
          onClose={onClose}
        />
        <PanelTabs tab={tab} onChange={setTab} />
        <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
          {tab === 'what' && (
            isTool
              ? <ToolWhatTab tool={tool!} />
              : <WorkflowWhatTab workflow={workflow!} />
          )}
          {tab === 'how' && (
            isTool
              ? <ToolHowTab tool={tool!} />
              : <WorkflowHowTab workflow={workflow!} />
          )}
          {tab === 'controls' && (
            isTool
              ? <ToolControlsTab tool={tool!} />
              : <WorkflowControlsTab workflow={workflow!} />
          )}
          {tab === 'interpret' && (
            isTool
              ? <ToolInterpretTab tool={tool!} />
              : <WorkflowInterpretTab workflow={workflow!} />
          )}
        </div>
        <PanelFooter
          tab={tab}
          onChange={setTab}
          onOpenInWorkspace={onOpenInWorkspace}
          primaryCtaLabel={primaryCtaLabel}
        />
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------------
// Header / tabs / footer chrome
// ----------------------------------------------------------------------------

function PanelHeader({
  kind,
  bucket,
  title,
  subtitle,
  onClose,
}: {
  kind: string;
  bucket?: string | null;
  title: string;
  subtitle: string;
  onClose: () => void;
}) {
  return (
    <header className="border-b border-line-subtle px-6 pt-5 pb-4">
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-3">
          <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-line-soft bg-gradient-to-br from-ice-500/25 to-ice-700/25">
            <Sparkles size={14} className="text-ice-300" />
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h2 className="truncate text-[15px] font-semibold tracking-[-0.01em] text-fg-primary">
                {title}
              </h2>
              {bucket ? (
                <span className="rounded-md border border-line-soft bg-white/[0.02] px-2 py-[2px] text-[10px] font-medium uppercase tracking-[0.1em] text-fg-muted">
                  {bucket}
                </span>
              ) : null}
            </div>
            <p className="mt-1 text-[11.5px] uppercase tracking-[0.14em] text-fg-faint">
              {kind}
            </p>
            <p className="mt-2 text-[12.5px] leading-[1.55] text-fg-secondary">
              {subtitle}
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close"
          className="rounded-md p-1.5 text-fg-muted transition-colors hover:bg-white/[0.05] hover:text-fg-secondary"
        >
          <X size={15} />
        </button>
      </div>
    </header>
  );
}

function PanelTabs({
  tab,
  onChange,
}: {
  tab: TabId;
  onChange: (t: TabId) => void;
}) {
  return (
    <nav className="border-b border-line-subtle px-6">
      <div className="flex gap-5">
        {TABS.map((t) => {
          const active = tab === t.id;
          return (
            <button
              key={t.id}
              type="button"
              onClick={() => onChange(t.id)}
              className={cn(
                'relative -mb-px py-3 text-[12.5px] font-medium tracking-[-0.005em] transition-colors',
                active
                  ? 'text-fg-primary'
                  : 'text-fg-muted hover:text-fg-secondary',
              )}
            >
              {t.label}
              {active && (
                <span className="absolute inset-x-0 -bottom-px h-[2px] rounded-full bg-ice-300" />
              )}
            </button>
          );
        })}
      </div>
    </nav>
  );
}

function PanelFooter({
  tab,
  onChange,
  onOpenInWorkspace,
  primaryCtaLabel,
}: {
  tab: TabId;
  onChange: (t: TabId) => void;
  onOpenInWorkspace?: () => void;
  primaryCtaLabel?: string;
}) {
  const idx = TABS.findIndex((t) => t.id === tab);
  const prev = idx > 0 ? TABS[idx - 1] : null;
  const next = idx < TABS.length - 1 ? TABS[idx + 1] : null;
  return (
    <footer className="flex items-center justify-between gap-3 border-t border-line-subtle px-6 py-4">
      <button
        type="button"
        onClick={() => prev && onChange(prev.id)}
        disabled={!prev}
        className={cn(
          'flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-[11.5px] font-medium transition-colors',
          prev
            ? 'text-fg-secondary hover:bg-white/[0.04] hover:text-fg-primary'
            : 'cursor-not-allowed text-fg-faint',
        )}
      >
        <ChevronLeft size={13} />
        {prev?.label ?? ''}
      </button>

      <div className="flex items-center gap-2">
        {onOpenInWorkspace ? (
          <button
            type="button"
            onClick={onOpenInWorkspace}
            className="flex items-center gap-1.5 rounded-md border border-ice-400/40 bg-gradient-to-b from-ice-500/25 to-ice-700/25 px-3 py-1.5 text-[12px] font-semibold text-ice-100 transition-all hover:border-ice-400/60 hover:from-ice-500/35 hover:to-ice-700/35"
          >
            {primaryCtaLabel ?? 'Open in workspace'}
            <ChevronRight size={12} />
          </button>
        ) : null}
        <button
          type="button"
          onClick={() => next && onChange(next.id)}
          disabled={!next}
          className={cn(
            'flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-[12px] font-semibold transition-all',
            next
              ? 'border-line-soft bg-white/[0.02] text-fg-secondary hover:border-line-strong hover:text-fg-primary'
              : 'cursor-not-allowed border-line-soft bg-white/[0.02] text-fg-faint',
          )}
        >
          Next: {next?.label ?? '—'}
          <ChevronRight size={13} />
        </button>
      </div>
    </footer>
  );
}

// ============================================================================
// TOOL TABS
// ============================================================================

function ToolWhatTab({ tool }: { tool: ToolCard }) {
  // Pull the wire-frozen primary output field from output_fields heuristically:
  // any field whose name starts with "time_series" is a payload candidate.
  const timeSeriesFields = tool.output_fields.filter((f) =>
    f.name.startsWith('time_series'),
  );
  return (
    <div className="space-y-5">
      <Section
        kicker="Identifies"
        title="What this tool answers"
        body={tool.description}
      />
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <CornerCard title="Domain">
          <p className="font-mono text-[12px] uppercase tracking-[0.06em] text-fg-secondary">
            {tool.domain}
          </p>
        </CornerCard>
        <CornerCard title="Category">
          <p className="font-mono text-[12px] uppercase tracking-[0.06em] text-fg-secondary">
            {tool.category ?? '—'}
          </p>
        </CornerCard>
      </div>
      {timeSeriesFields.length > 0 && (
        <CornerCard title="Canonical output fields">
          <ul className="space-y-1.5">
            {timeSeriesFields.map((f) => (
              <li key={f.name} className="flex flex-col gap-0.5">
                <span className="mono text-[11.5px] text-ice-300">{f.name}</span>
                <span className="text-[11px] text-fg-secondary leading-snug">
                  {f.description ?? '(no description)'}
                </span>
              </li>
            ))}
          </ul>
        </CornerCard>
      )}
    </div>
  );
}

function ToolHowTab({ tool }: { tool: ToolCard }) {
  return (
    <div className="space-y-5">
      <Section
        kicker="Step-by-step"
        title="Methodology"
        body={tool.methodology.what_it_does}
      />
      {tool.methodology.assumptions.length > 0 && (
        <CornerCard title="Methodology assumptions">
          <ul className="space-y-1.5">
            {tool.methodology.assumptions.map((a, i) => (
              <li
                key={i}
                className="flex gap-2 text-[12px] leading-[1.5] text-fg-secondary"
              >
                <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-ice-300" />
                <span>{a}</span>
              </li>
            ))}
          </ul>
        </CornerCard>
      )}
    </div>
  );
}

function ToolControlsTab({ tool }: { tool: ToolCard }) {
  return (
    <div className="space-y-5">
      <Section
        kicker="Inputs"
        title="*Input field shape"
        body="The LLM (and the REST clients) bind values into these fields. The validator catches type / value-range errors at execution time."
      />
      <FieldList fields={tool.input_fields} />
      <Section
        kicker="Methodology knobs (read-only preview)"
        title="Conventions from config.yaml"
        body="These are the methodology choices baked into the tool's bundled config.  The PR-level editor that mutates these knobs at runtime is a follow-up — for now this is a transparent preview of every constant the compute layer uses."
      />
      <ConventionList conventions={tool.conventions} />
    </div>
  );
}

function ToolInterpretTab({ tool }: { tool: ToolCard }) {
  return (
    <div className="space-y-5">
      <Section
        kicker="How to read"
        title="Output interpretation"
        body={`Output is a structured response (see the *Output fields below). For wire-frozen TimeSeries fields (units = bps / percent / z_score), the snapshot's current_metrics block matches the last row of the corresponding canonical TimeSeries by construction — the snapshot is never numerically inconsistent with the historical series.`}
      />
      <FieldList fields={tool.output_fields} />
      {tool.methodology.planned_extensions.length > 0 && (
        <CornerCard title="Planned extensions">
          <ul className="space-y-1.5">
            {tool.methodology.planned_extensions.map((e, i) => (
              <li
                key={i}
                className="flex gap-2 text-[11.5px] leading-[1.5] text-fg-muted"
              >
                <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-fg-faint" />
                <span>{e}</span>
              </li>
            ))}
          </ul>
        </CornerCard>
      )}
    </div>
  );
}

// ============================================================================
// WORKFLOW TABS
// ============================================================================

function WorkflowWhatTab({ workflow }: { workflow: WorkflowTemplateCard }) {
  return (
    <div className="space-y-5">
      <Section
        kicker={`Archetype · ${workflow.archetype}`}
        title="What this workflow answers"
        body={workflow.description}
      />
      {workflow.archetype_signature.length > 0 && (
        <CornerCard title="Recognised desk phrasing">
          <ul className="space-y-1.5">
            {workflow.archetype_signature.map((cue, i) => (
              <li
                key={i}
                className="flex gap-2 text-[12px] leading-[1.5] text-fg-secondary"
              >
                <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-ice-300" />
                <span className="italic">"{cue}"</span>
              </li>
            ))}
          </ul>
        </CornerCard>
      )}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <CornerCard title="DAG shape">
          <p className="mono text-[12px] text-fg-secondary">
            {workflow.node_count} nodes · {workflow.edge_count} edges
          </p>
        </CornerCard>
        <CornerCard title="Terminal artifact">
          <p className="mono text-[12px] text-fg-secondary">
            {workflow.terminal_artifact_type}
          </p>
        </CornerCard>
        <CornerCard title="Operators">
          <p className="mono text-[11px] text-fg-secondary leading-snug">
            {workflow.operators_used.length}
          </p>
        </CornerCard>
      </div>
    </div>
  );
}

function WorkflowHowTab({ workflow }: { workflow: WorkflowTemplateCard }) {
  return (
    <div className="space-y-5">
      <Section
        kicker="Composition"
        title="Operators used"
        body="These finance-blind operators compose into the template's DAG.  Each is documented in shared/operators/<name>/config.yaml."
      />
      <CornerCard title="Operator chain">
        <div className="flex flex-wrap gap-1.5">
          {workflow.operators_used.map((op) => (
            <span
              key={op}
              className="mono rounded-md border border-line-soft bg-white/[0.02] px-2 py-[3px] text-[10.5px] text-ice-200"
            >
              {op}
            </span>
          ))}
        </div>
      </CornerCard>
      <CornerCard title="Primitives consumed (slot-driven)">
        <div className="flex flex-wrap gap-1.5">
          {workflow.primitives_used.map((p) => (
            <span
              key={p}
              className="mono rounded-md border border-line-soft bg-white/[0.02] px-2 py-[3px] text-[10.5px] text-fg-secondary"
            >
              {p}
            </span>
          ))}
        </div>
      </CornerCard>
    </div>
  );
}

function WorkflowControlsTab({ workflow }: { workflow: WorkflowTemplateCard }) {
  return (
    <div className="space-y-5">
      <Section
        kicker="Slots"
        title="Caller-fillable surface"
        body="Slots the LLM (or you) bind at run time.  Required slots must be supplied; optional slots fall back to documented defaults.  Type mismatches surface as bind-time errors before any node runs."
      />
      <SlotList slots={workflow.slot_schema} />
    </div>
  );
}

function WorkflowInterpretTab({ workflow }: { workflow: WorkflowTemplateCard }) {
  return (
    <div className="space-y-5">
      <Section
        kicker="How to read"
        title={`Output is a ${workflow.terminal_artifact_type}`}
        body="The terminal artifact is the workflow's deliverable.  Lineage walks back through every operator + primitive call so any number can be traced to its source.  The chat assistant renders a summary card with the structured envelope; the full lineage walk is available in the workspace."
      />
      <CornerCard title="Cues this template matches">
        <ul className="space-y-1.5">
          {workflow.archetype_signature.map((cue, i) => (
            <li
              key={i}
              className="flex gap-2 text-[11.5px] leading-[1.5] text-fg-secondary"
            >
              <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-fg-faint" />
              <span className="italic">"{cue}"</span>
            </li>
          ))}
        </ul>
      </CornerCard>
    </div>
  );
}

// ============================================================================
// SHARED CHROME
// ============================================================================

function Section({
  kicker,
  title,
  body,
}: {
  kicker: string;
  title: string;
  body: string;
}) {
  return (
    <div>
      <p className="kicker text-fg-muted">{kicker}</p>
      <h3 className="mt-1 text-[13.5px] font-semibold tracking-[-0.005em] text-fg-primary">
        {title}
      </h3>
      <p className="mt-2 text-[12.5px] leading-[1.6] text-fg-secondary">{body}</p>
    </div>
  );
}

function CornerCard({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <div className="rounded-lg border border-line-soft bg-white/[0.012] p-4">
      <p className="kicker mb-2 text-fg-muted">{title}</p>
      {children}
    </div>
  );
}

function FieldList({ fields }: { fields: ToolFieldDescriptor[] }) {
  if (fields.length === 0) {
    return (
      <p className="rounded-lg border border-line-soft bg-white/[0.012] px-4 py-3 text-[11.5px] text-fg-muted">
        (no documented fields)
      </p>
    );
  }
  return (
    <div className="space-y-2">
      {fields.map((f) => (
        <div
          key={f.name}
          className="rounded-lg border border-line-soft bg-white/[0.012] p-3"
        >
          <div className="flex items-baseline justify-between gap-3">
            <div className="flex items-baseline gap-2">
              <span className="mono text-[12px] text-ice-200">{f.name}</span>
              <span className="mono text-[10.5px] text-fg-faint">
                : {f.type}
              </span>
            </div>
            <span
              className={cn(
                'mono text-[10px] uppercase tracking-[0.12em]',
                f.required ? 'text-coral-300/80' : 'text-fg-muted',
              )}
            >
              {f.required ? 'required' : 'optional'}
            </span>
          </div>
          {f.default !== undefined && f.default !== null && (
            <p className="mt-1.5 text-[11px] text-fg-muted">
              default ={' '}
              <span className="mono text-fg-secondary">
                {JSON.stringify(f.default)}
              </span>
            </p>
          )}
          {f.description && (
            <p className="mt-1.5 text-[11.5px] leading-[1.5] text-fg-secondary">
              {f.description}
            </p>
          )}
        </div>
      ))}
    </div>
  );
}

function ConventionList({
  conventions,
}: {
  conventions: ToolConventionDescriptor[];
}) {
  if (conventions.length === 0) {
    return (
      <p className="rounded-lg border border-line-soft bg-white/[0.012] px-4 py-3 text-[11.5px] text-fg-muted">
        (no documented conventions)
      </p>
    );
  }
  return (
    <div className="space-y-2">
      {conventions.map((c) => (
        <div
          key={c.name}
          className="rounded-lg border border-line-soft bg-white/[0.012] p-3"
        >
          <div className="flex items-baseline justify-between gap-3">
            <span className="mono text-[12px] text-ice-200">{c.name}</span>
            <span className="mono text-[12px] text-fg-primary">
              {formatScalar(c.value)}
            </span>
          </div>
          {(c.valid_range || c.valid_values) && (
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              {c.valid_range && (
                <span className="mono rounded border border-line-soft bg-white/[0.02] px-1.5 py-0.5 text-[10px] text-fg-muted">
                  range {String(c.valid_range[0])} … {String(c.valid_range[1])}
                </span>
              )}
              {c.valid_values && (
                <span className="mono rounded border border-line-soft bg-white/[0.02] px-1.5 py-0.5 text-[10px] text-fg-muted">
                  one of {c.valid_values.map((v) => String(v)).join(' | ')}
                </span>
              )}
            </div>
          )}
          <p className="mt-1.5 text-[11.5px] leading-[1.5] text-fg-secondary">
            {c.rationale}
          </p>
          <p className="mt-1 text-[10.5px] uppercase tracking-[0.12em] text-fg-faint">
            source · {c.source}
          </p>
        </div>
      ))}
    </div>
  );
}

function SlotList({ slots }: { slots: SlotDeclaration[] }) {
  if (slots.length === 0) {
    return (
      <p className="rounded-lg border border-line-soft bg-white/[0.012] px-4 py-3 text-[11.5px] text-fg-muted">
        (no slots)
      </p>
    );
  }
  return (
    <div className="space-y-2">
      {slots.map((s) => (
        <div
          key={s.name}
          className="rounded-lg border border-line-soft bg-white/[0.012] p-3"
        >
          <div className="flex items-baseline justify-between gap-3">
            <div className="flex items-baseline gap-2">
              <span className="mono text-[12px] text-ice-200">{s.name}</span>
              <span className="mono text-[10.5px] text-fg-faint">
                : {s.type}
              </span>
            </div>
            <span
              className={cn(
                'mono text-[10px] uppercase tracking-[0.12em]',
                s.required ? 'text-coral-300/80' : 'text-fg-muted',
              )}
            >
              {s.required ? 'required' : 'optional'}
            </span>
          </div>
          {s.default !== undefined && s.default !== null && (
            <p className="mt-1.5 text-[11px] text-fg-muted">
              default ={' '}
              <span className="mono text-fg-secondary">
                {JSON.stringify(s.default)}
              </span>
            </p>
          )}
          <p className="mt-1.5 text-[11.5px] leading-[1.5] text-fg-secondary">
            {s.description}
          </p>
        </div>
      ))}
    </div>
  );
}

function formatScalar(v: unknown): string {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'string') return v;
  if (typeof v === 'number' || typeof v === 'boolean') return String(v);
  return JSON.stringify(v);
}
