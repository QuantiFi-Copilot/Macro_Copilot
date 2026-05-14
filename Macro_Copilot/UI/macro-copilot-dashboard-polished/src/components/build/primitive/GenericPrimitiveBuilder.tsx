// ============================================================================
// GenericPrimitiveBuilder — schema-driven Build surface for any runnable
//                            primitive without a bespoke typed view.
// ----------------------------------------------------------------------------
// PR2 — Schema-Driven Primitive Builder Surface.
//
// Why
// ---
// Before PR2 the only way a primitive landed on a real "configure +
// run" surface was either a hand-written typed view (Spread,
// CrossMarket, Butterfly, Yield, Regime, Scanner, Forward) or a
// rich-model entry in ``modelRegistry`` (PCA, rolling regression,
// half-life, beta-adjusted spread, attribution).  Everything in
// between — OIS spreads, swap spread, breakeven, sovereign yield
// panel, financing rate, zscore_custom, OIS rate level — fell into
// the PR1 ``unsupported_known`` card with no way to actually run the
// tool from Build.
//
// PR2 plugs that gap with a single component that:
//   1. Fetches the primitive's ``ToolCard`` (input/output schema +
//      methodology + conventions) from ``GET /api/v1/tools/{name}``.
//   2. Renders a ``ParametersPanel`` form generated from the
//      ``input_fields`` schema; the model registry's ``paramHintFor``
//      with the new ``inferFieldControl`` fallback drives control
//      selection (curve/tenor dropdowns, lookback sliders, date
//      pickers, scalar inputs).
//   3. Seeds the form from URL ``?context=`` params (Ask hand-off)
//      OR from the schema defaults (Library deep-link).
//   4. Runs the primitive via ``POST /api/v1/tools/{name}/run`` when
//      the user clicks Run; renders the result via the shared
//      ``OutputCanvas`` which falls through to ``AutoRenderer`` for
//      tools without a bespoke output component (KPI strip +
//      time-series mini-charts).
//   5. Mirrors the current form state into the URL on every change so
//      bookmark / refresh / share preserves the configuration.
//
// What this is NOT
// ----------------
// Not a replacement for the existing typed views (those still ship
// polished charts).  Not a replacement for the rich ``ModelWorkspacePage``
// (that has presets, pinned-run comparison, lineage panel — heavy
// machinery PR2 doesn't need yet).  Not a payload-backed widget host
// (PR4 owns that).  This is the minimum surface that turns "tool is
// known but Build can't run it" into "tool is known AND Build can
// configure + run it."
// ============================================================================

import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { AlertCircle, Loader2, FlaskConical, Wrench } from 'lucide-react';
import { useTool } from '@/hooks/useWorkflows';
import { runPrimitive } from '@/services/workflowsApi';
import { paramHintFor } from '@/lib/modelRegistry';
import type {
  PrimitiveRunResult,
  ToolCard,
  ToolFieldDescriptor,
} from '@/types/workflows';
import {
  defaultFormValue,
  marshalForm,
  ParametersPanel,
  sortFields,
  type FieldValue,
  type FormState,
} from '../model/ParametersPanel';
import { OutputCanvas } from '../model/OutputCanvas';
import { MethodologyPanel } from '../model/MethodologyPanel';

type Props = {
  /** Backend-canonical tool name.  ``decodePrimitiveContext`` already
   *  normalised any manifest shorthand into the canonical form before
   *  emitting the ``generic_builder`` variant. */
  toolName: string;
  /** URL-supplied params dict.  Pre-fills the form when the user
   *  arrived via Ask (multi-tool hand-off) or via a deep-link with
   *  explicit field values; empty for a bare Library "Open in Build"
   *  click (form falls through to schema defaults).
   *
   *  PR-B-β widens the value type from ``string`` to ``unknown`` so
   *  Ask hand-offs can carry NESTED params (``series_spec``,
   *  ``regressor_specs`` array, ``tenors`` list, etc.) verbatim
   *  from the workspace_context.  ``seedFormFromSchema`` handles
   *  the per-control-kind shape coercion; the existing scalar-only
   *  callers keep working because every ``Record<string, string>``
   *  is also a ``Record<string, unknown>``. */
  initialParams: Record<string, unknown>;
};

export function GenericPrimitiveBuilder({ toolName, initialParams }: Props) {
  const { data: card, isLoading, error } = useTool(toolName);

  if (error) {
    return <LoadError toolName={toolName} message={error.message} />;
  }
  if (isLoading || !card) {
    return <LoadingCard toolName={toolName} />;
  }
  return (
    <GenericPrimitiveBuilderBody card={card} initialParams={initialParams} />
  );
}

// ----------------------------------------------------------------------------
// Body — assumes the ToolCard is loaded.
// ----------------------------------------------------------------------------

function GenericPrimitiveBuilderBody({
  card,
  initialParams,
}: {
  card: ToolCard;
  initialParams: Record<string, unknown>;
}) {
  const navigate = useNavigate();

  // Form state — seeded from URL params (scalar only; nested shapes
  // can't ride the URL today) → schema default → empty string.
  const [form, setForm] = useState<FormState>(() =>
    seedFormFromSchema(card, initialParams),
  );

  // Re-seed when the user navigates between two different primitives
  // (tool name changes); the marshalled URL → form path is the
  // canonical bookmark / refresh recovery mechanism.
  useEffect(() => {
    setForm(seedFormFromSchema(card, initialParams));
    setRunResult(null);
    setRunError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [card.tool_name]);

  const [isRunning, setIsRunning] = useState(false);
  const [runResult, setRunResult] = useState<PrimitiveRunResult | null>(null);
  const [runError, setRunError] = useState<string | null>(null);

  const paramsForRun = useMemo(
    () =>
      marshalForm(form, card.input_fields, (n) =>
        paramHintFor(card.tool_name, n).control,
      ),
    [form, card.input_fields, card.tool_name],
  );

  // Mirror the current scalar form values into the URL so refresh /
  // bookmark preserves the configuration.  Only scalar fields ride
  // the URL — nested shapes (panel legs etc.) would balloon the
  // query string and aren't deep-linkable today.  This runs on every
  // form change but writes ``replace: true`` so the back button stays
  // useful (one history entry per page nav, not per keystroke).
  useEffect(() => {
    const scalarOnly: Record<string, string> = {};
    for (const f of card.input_fields) {
      const v = form[f.name];
      if (typeof v === 'string' && v !== '') {
        scalarOnly[f.name] = v;
      }
    }
    const encoded = encodeURIComponent(
      JSON.stringify({
        tools: [{ tool: card.tool_name, params: scalarOnly }],
        tool_count: 1,
      }),
    );
    // ``replace: true`` so typing in the form doesn't pollute history.
    navigate(`/workspace?context=${encoded}`, { replace: true });
    // Intentionally exclude ``navigate`` so the effect fires on form
    // changes; ``navigate`` is stable across the lifetime of the
    // router so this is a safe exclusion.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [form, card.tool_name, card.input_fields]);

  const handleRun = async () => {
    setIsRunning(true);
    setRunError(null);
    setRunResult(null);
    try {
      const res = await runPrimitive(card.tool_name, paramsForRun);
      setRunResult(res);
    } catch (e) {
      setRunError(e instanceof Error ? e.message : String(e));
    } finally {
      setIsRunning(false);
    }
  };

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden">
      <Header card={card} />

      <div className="grid min-h-0 flex-1 grid-cols-1 overflow-hidden lg:grid-cols-[320px_minmax(0,1fr)_320px]">
        {/* Left rail — schema-driven controls + Run button */}
        <aside className="space-y-5 overflow-y-auto border-r border-line-subtle bg-white/[0.008] px-5 py-5">
          <SectionTitle icon={<Wrench size={12} />} title="Controls" />
          <ParametersPanel
            card={card}
            form={form}
            onChange={setForm}
            onRun={handleRun}
            isRunning={isRunning}
          />
          <p className="text-[10.5px] leading-[1.5] text-fg-faint">
            Editable inputs derived from this primitive&apos;s schema.  Run
            sends the form to <code className="font-mono">POST
            /api/v1/tools/{card.tool_name}/run</code>.
          </p>
        </aside>

        {/* Centre canvas — schema-driven output via the shared
         *  ``OutputCanvas``.  ``AutoRenderer`` handles every non-model
         *  tool's output shape (KPI strip + time_series panels +
         *  JSON fallback). */}
        <section className="overflow-y-auto px-6 py-5">
          <OutputCanvas
            card={card}
            result={runResult}
            isRunning={isRunning}
            errorMessage={runError}
          />
        </section>

        {/* Right rail — methodology + conventions (read-only) */}
        <aside className="space-y-5 overflow-y-auto border-l border-line-subtle bg-white/[0.008] px-5 py-5">
          <SectionTitle icon={<FlaskConical size={12} />} title="Methodology" />
          <MethodologyPanel card={card} />
        </aside>
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------------
// Header
// ----------------------------------------------------------------------------

function Header({ card }: { card: ToolCard }) {
  return (
    <header className="flex flex-col gap-3 border-b border-line-subtle px-6 py-4 lg:flex-row lg:items-end lg:justify-between">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="kicker text-ice-300/80">{card.domain}</span>
          {card.category ? (
            <span className="rounded-md border border-line-soft bg-white/[0.02] px-1.5 py-[1px] text-[9.5px] uppercase tracking-[0.07em] text-fg-muted">
              {card.category}
            </span>
          ) : null}
          <span className="rounded-md border border-ice-400/25 bg-ice-500/10 px-1.5 py-[1px] text-[9.5px] uppercase tracking-[0.07em] text-ice-200">
            schema builder
          </span>
          <span className="rounded-md border border-line-soft bg-white/[0.02] px-1.5 py-[1px] font-mono text-[9.5px] uppercase tracking-[0.07em] text-fg-muted">
            {card.tool_name}
          </span>
        </div>
        <h1 className="mt-1.5 text-[19px] font-semibold tracking-[-0.01em] text-fg-primary">
          {humanLabel(card.tool_name.replace(/_tool$/, ''))}
        </h1>
        <p className="mt-1 max-w-3xl text-[12px] leading-[1.55] text-fg-secondary">
          {card.description || card.methodology.what_it_does}
        </p>
      </div>
    </header>
  );
}

function SectionTitle({
  icon,
  title,
}: {
  icon: React.ReactNode;
  title: string;
}) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-ice-300">{icon}</span>
      <h2 className="text-[12px] font-semibold uppercase tracking-[0.1em] text-fg-secondary">
        {title}
      </h2>
    </div>
  );
}

// ----------------------------------------------------------------------------
// Loading + error states
// ----------------------------------------------------------------------------

function LoadingCard({ toolName }: { toolName: string }) {
  return (
    <div className="flex h-full min-h-0 items-center justify-center px-6">
      <div className="flex items-center gap-2 text-[12px] text-fg-muted">
        <Loader2 size={13} className="animate-spin text-ice-300" />
        <span>
          Loading schema for{' '}
          <span className="font-mono text-fg-secondary">{toolName}</span>…
        </span>
      </div>
    </div>
  );
}

function LoadError({ toolName, message }: { toolName: string; message: string }) {
  return (
    <div className="flex h-full min-h-0 items-center justify-center px-6">
      <div className="card flex max-w-[520px] items-start gap-3 px-5 py-4">
        <AlertCircle size={16} className="mt-0.5 shrink-0 text-coral-300" />
        <div className="min-w-0">
          <div className="text-[12.5px] font-semibold text-fg-primary">
            Could not load tool schema
          </div>
          <div className="mt-1 font-mono text-[10.5px] text-fg-muted">
            {toolName}
          </div>
          <div className="mt-2 text-[11.5px] leading-[1.5] text-fg-secondary">
            {message}
          </div>
          <div className="mt-2 text-[10.5px] leading-[1.5] text-fg-faint">
            The primitive is registered in the catalogue but the schema
            endpoint returned an error.  This is usually a backend
            connectivity problem — check the API logs.
          </div>
        </div>
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------------
// Form seeding — schema + URL → FormState
// ----------------------------------------------------------------------------

/** Seed the form from (a) URL-supplied scalar params, (b) the schema's
 *  ``input_fields[].default``, falling back to the auto control's
 *  empty value.  Mirrors ``ModelWorkspacePage.seedForm`` but without
 *  the ``modelRegistry.defaultParams`` step — generic primitives don't
 *  ship structured defaults; the schema's per-field default is enough.
 *
 *  Hidden fields (``paramHintFor(...).hidden``) are skipped — same
 *  discipline the rich model builder uses.  */
function seedFormFromSchema(
  card: ToolCard,
  initialParams: Record<string, unknown>,
): FormState {
  const out: FormState = {};
  const visible = sortFields(card.input_fields, hiddenFieldsFor(card));
  for (const f of visible) {
    const hint = paramHintFor(card.tool_name, f.name);
    const raw = initialParams[f.name];
    // PR-B-β — structured-control seeding.  When Ask sends a
    // properly-shaped value for series_spec / series_spec_list /
    // multi_tenor, use it as the initial form value.  Falls back to
    // the schema default when the shape doesn't match (defensive).
    if (raw !== undefined && raw !== null) {
      if (hint.control === 'series_spec' && isPlainObject(raw)) {
        out[f.name] = raw as FormState[string];
        continue;
      }
      if (hint.control === 'series_spec_list' && Array.isArray(raw)) {
        out[f.name] = raw as FormState[string];
        continue;
      }
      if (hint.control === 'multi_tenor' && Array.isArray(raw)) {
        out[f.name] = raw.map((v) => String(v)) as FormState[string];
        continue;
      }
      // Scalar controls: accept any string / number / boolean and
      // coerce to string (the form state expects string scalars).
      const isScalarControl =
        hint.control !== 'series_spec' &&
        hint.control !== 'series_spec_list' &&
        hint.control !== 'multi_tenor';
      if (isScalarControl && isCoercibleScalar(raw)) {
        out[f.name] = String(raw);
        continue;
      }
    }
    out[f.name] = defaultFormValue(f, hint.control);
  }
  return out;
}

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

function isCoercibleScalar(v: unknown): v is string | number | boolean {
  return (
    typeof v === 'string' ||
    typeof v === 'number' ||
    typeof v === 'boolean'
  );
}

function hiddenFieldsFor(card: ToolCard): Set<string> {
  const s = new Set<string>();
  for (const f of card.input_fields as ToolFieldDescriptor[]) {
    if (paramHintFor(card.tool_name, f.name).hidden) s.add(f.name);
  }
  return s;
}

function humanLabel(snake: string): string {
  return snake
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

/** Exported helper used by ``GenericPrimitiveBuilder``'s URL-mirror
 *  effect AND by the multi-card grid's per-tile navigation builder.
 *  Stringifies the scalar form values into the same ``?context=``
 *  shape the decoder reads, so the round-trip is loss-free for the
 *  scalar-only path. */
export function encodeGenericBuilderContext(
  toolName: string,
  params: Record<string, string>,
): string {
  return encodeURIComponent(
    JSON.stringify({
      tools: [{ tool: toolName, params }],
      tool_count: 1,
    }),
  );
}
