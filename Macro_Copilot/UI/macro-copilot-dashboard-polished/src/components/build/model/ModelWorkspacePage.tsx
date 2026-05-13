// ============================================================================
// ModelWorkspacePage — the model playground for analytical primitives
// ----------------------------------------------------------------------------
// 3-column layout:
//
//   ┌─────────────────────────────────────────────────────────────────┐
//   │ Header — model name · category · status · pin · save · share   │
//   ├──────────────┬─────────────────────────────────┬────────────────┤
//   │              │                                 │                │
//   │ Controls     │  Output canvas (renderer)       │  Methodology   │
//   │ + presets    │  + interpretation cards         │  + lineage     │
//   │ + comparison │                                 │                │
//   │              │                                 │                │
//   └──────────────┴─────────────────────────────────┴────────────────┘
//
// State
// -----
//   - card             : ToolCard from /tools/{name} (input/output schema +
//                         methodology + conventions, source of truth)
//   - form             : raw form state for every input field, indexed by
//                         schema field name; rich controls write structured
//                         shapes here, simple controls write strings
//   - paramsForRun     : marshalled dict the run endpoint expects (computed
//                         from form via ParametersPanel.marshalForm)
//   - runResult        : last successful run envelope OR last failure
//   - pinnedRuns       : in-memory pinned runs for side-by-side comparison
//   - presetSeq        : monotonic counter; bumping it triggers the saved
//                         presets panel to re-read localStorage
// ============================================================================

import { useEffect, useMemo, useState } from 'react';
import { Bookmark, FlaskConical, Pin, Wrench } from 'lucide-react';
import { useTool } from '@/hooks/useWorkflows';
import { runPrimitive } from '@/services/workflowsApi';
import { savePreset } from '@/services/modelPresets';
import {
  getModelMetadata,
  paramHintFor,
  type ModelMetadata,
} from '@/lib/modelRegistry';
import type { PrimitiveRunResult, ToolCard, ToolFieldDescriptor } from '@/types/workflows';
import { cn } from '@/utils/cn';
import {
  defaultFormValue,
  marshalForm,
  ParametersPanel,
  sortFields,
  type FieldValue,
  type FormState,
} from './ParametersPanel';
import { MethodologyPanel } from './MethodologyPanel';
import { OutputCanvas } from './OutputCanvas';
import { InterpretationCards } from './InterpretationCards';
import { SavedPresetsPanel } from './SavedPresetsPanel';
import { ComparisonStrip, type PinnedRun } from './ComparisonStrip';
import { LineagePanel } from './LineagePanel';

type ModelWorkspacePageProps = {
  toolName: string;
  /** URL-supplied initial overrides (per-field, scalar values only). */
  initialParams: Record<string, string>;
};

export function ModelWorkspacePage({
  toolName,
  initialParams,
}: ModelWorkspacePageProps) {
  const { data: card, isLoading, error } = useTool(toolName);

  if (error) {
    return (
      <div className="px-6 py-10">
        <div className="card flex items-start gap-3 px-5 py-4">
          <span className="text-coral-300">!</span>
          <div>
            <div className="text-[12.5px] font-semibold text-fg-primary">
              Failed to load model
            </div>
            <div className="mt-1 text-[11.5px] text-fg-secondary">
              {error.message}
            </div>
          </div>
        </div>
      </div>
    );
  }

  if (isLoading || !card) {
    return (
      <div className="px-6 py-10">
        <div className="card flex h-[320px] items-center justify-center text-[12px] text-fg-muted">
          Loading model…
        </div>
      </div>
    );
  }

  return <ModelWorkspaceBody card={card} initialParams={initialParams} />;
}

// ---------------------------------------------------------------------------

function ModelWorkspaceBody({
  card,
  initialParams,
}: {
  card: ToolCard;
  initialParams: Record<string, string>;
}) {
  const meta = getModelMetadata(card.tool_name);

  const [form, setForm] = useState<FormState>(() => seedForm(card, initialParams));

  // When the tool changes (e.g. via deep link to a different primitive), reset state.
  useEffect(() => {
    setForm(seedForm(card, initialParams));
    setRunResult(null);
    setRunError(null);
    setPinnedRuns([]);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [card.tool_name]);

  const [isRunning, setIsRunning] = useState(false);
  const [runResult, setRunResult] = useState<PrimitiveRunResult | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const [pinnedRuns, setPinnedRuns] = useState<PinnedRun[]>([]);
  const [presetSeq, setPresetSeq] = useState(0);

  const paramsForRun = useMemo(
    () =>
      marshalForm(form, card.input_fields, (n) => paramHintFor(card.tool_name, n).control),
    [form, card.input_fields, card.tool_name],
  );

  const handleRun = async () => {
    setIsRunning(true);
    setRunError(null);
    try {
      const res = await runPrimitive(card.tool_name, paramsForRun);
      setRunResult(res);
    } catch (e) {
      setRunError(e instanceof Error ? e.message : String(e));
    } finally {
      setIsRunning(false);
    }
  };

  const handleSavePreset = () => {
    const title = window.prompt(
      'Save preset · enter a name',
      defaultPresetTitle(meta, paramsForRun),
    );
    if (!title) return;
    savePreset({
      toolName: card.tool_name,
      title,
      params: paramsForRun,
    });
    setPresetSeq((s) => s + 1);
  };

  const handlePinRun = () => {
    if (!runResult || !runResult.ok) return;
    const id = `pin_${Math.random().toString(36).slice(2, 8)}_${Date.now()}`;
    setPinnedRuns((rs) => [
      {
        id,
        label: defaultPresetTitle(meta, paramsForRun),
        params: paramsForRun,
        output: runResult.output,
        ranAt: new Date().toISOString(),
      },
      ...rs,
    ]);
  };

  const loadPreset = (params: Record<string, unknown>, runImmediately = false) => {
    setForm(formFromParams(card, params));
    if (runImmediately) {
      // Defer to next tick so the form state writes through before the run
      // closes over its dependencies.
      setTimeout(() => {
        void (async () => {
          setIsRunning(true);
          setRunError(null);
          try {
            const res = await runPrimitive(card.tool_name, params);
            setRunResult(res);
          } catch (e) {
            setRunError(e instanceof Error ? e.message : String(e));
          } finally {
            setIsRunning(false);
          }
        })();
      }, 0);
    }
  };

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden">
      <ModelHeader
        card={card}
        meta={meta}
        canPin={!!runResult && runResult.ok}
        onPin={handlePinRun}
      />

      <div className="grid min-h-0 flex-1 grid-cols-1 overflow-hidden lg:grid-cols-[320px_minmax(0,1fr)_320px]">
        {/* Left rail — controls + presets + comparison */}
        <aside className="space-y-5 overflow-y-auto border-r border-line-subtle bg-white/[0.008] px-5 py-5">
          <SectionTitle icon={<Wrench size={12} />} title="Controls" />
          <ParametersPanel
            card={card}
            form={form}
            onChange={setForm}
            onRun={handleRun}
            onSavePreset={handleSavePreset}
            isRunning={isRunning}
          />

          <div className="border-t border-line-subtle pt-4">
            <SectionTitle icon={<Bookmark size={12} />} title="Saved presets" />
            <div className="mt-2.5">
              <SavedPresetsPanel
                toolName={card.tool_name}
                refreshKey={presetSeq}
                onLoad={(p, run) => loadPreset(p.params, !!run)}
                isRunning={isRunning}
              />
            </div>
          </div>

          <div className="border-t border-line-subtle pt-4">
            <SectionTitle icon={<Pin size={12} />} title="Pinned runs" />
            <div className="mt-2.5">
              <ComparisonStrip
                pinnedRuns={pinnedRuns}
                activeOutput={runResult?.ok ? runResult.output : null}
                onLoadParams={(p) => loadPreset(p)}
                onUnpin={(id) =>
                  setPinnedRuns((rs) => rs.filter((r) => r.id !== id))
                }
              />
            </div>
          </div>
        </aside>

        {/* Centre canvas */}
        <section className="overflow-y-auto px-6 py-5">
          <OutputCanvas
            card={card}
            result={runResult}
            isRunning={isRunning}
            errorMessage={runError}
          />
          {runResult?.ok ? (
            <div className="mt-5 space-y-5">
              <InterpretationCards toolName={card.tool_name} />
            </div>
          ) : null}
        </section>

        {/* Right rail — methodology + lineage */}
        <aside className="space-y-5 overflow-y-auto border-l border-line-subtle bg-white/[0.008] px-5 py-5">
          <SectionTitle icon={<FlaskConical size={12} />} title="Methodology" />
          <MethodologyPanel card={card} />

          {runResult?.ok ? (
            <div className="border-t border-line-subtle pt-4">
              <SectionTitle icon={<Wrench size={12} />} title="Lineage · this run" />
              <div className="mt-2.5">
                <LineagePanel card={card} params={paramsForRun} />
              </div>
            </div>
          ) : null}
        </aside>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Header
// ---------------------------------------------------------------------------

function ModelHeader({
  card,
  meta,
  canPin,
  onPin,
}: {
  card: ToolCard;
  meta: ModelMetadata | null;
  canPin: boolean;
  onPin: () => void;
}) {
  const title = meta?.displayName ?? humanLabel(card.tool_name.replace(/_tool$/, ''));
  const summary = meta?.oneLineSummary ?? card.description ?? card.methodology.what_it_does;
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
          {meta ? (
            <span className="rounded-md border border-ice-400/20 bg-ice-500/10 px-1.5 py-[1px] text-[9.5px] uppercase tracking-[0.07em] text-ice-200">
              {meta.category}
            </span>
          ) : null}
          <span className="rounded-md border border-line-soft bg-white/[0.02] px-1.5 py-[1px] mono text-[9.5px] uppercase tracking-[0.07em] text-fg-muted">
            {card.tool_name}
          </span>
        </div>
        <h1 className="mt-1.5 text-[19px] font-semibold tracking-[-0.01em] text-fg-primary">
          {title}
        </h1>
        <p className="mt-1 max-w-3xl text-[12px] leading-[1.55] text-fg-secondary">
          {summary}
        </p>
      </div>
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={onPin}
          disabled={!canPin}
          className={cn(
            'flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-[12px] font-semibold transition-colors',
            canPin
              ? 'border-ice-400/30 bg-ice-500/10 text-ice-100 hover:border-ice-400/50 hover:bg-ice-500/20'
              : 'cursor-not-allowed border-line-soft bg-white/[0.02] text-fg-faint',
          )}
          title={canPin ? 'Pin this run for side-by-side comparison' : 'Run the model first to enable pinning'}
        >
          <Pin size={12} />
          Pin
        </button>
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

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function humanLabel(snake: string): string {
  return snake
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function seedForm(card: ToolCard, initialParams: Record<string, string>): FormState {
  const out: FormState = {};
  const visible = sortFields(card.input_fields, new Set());
  const meta = getModelMetadata(card.tool_name);
  const registryDefaults = meta?.defaultParams ?? {};

  for (const f of visible) {
    const hint = paramHintFor(card.tool_name, f.name);

    // Priority order, highest first:
    //   1. URL override (scalar fields only — nested shapes can't ride the URL)
    //   2. Registry-supplied defaultParams (structured + scalar)
    //   3. Pydantic schema default (from ToolCard.input_fields[].default)
    const isScalarControl =
      hint.control !== 'series_spec' &&
      hint.control !== 'series_spec_list' &&
      hint.control !== 'multi_tenor';

    if (isScalarControl && initialParams[f.name] !== undefined) {
      out[f.name] = initialParams[f.name];
      continue;
    }
    if (registryDefaults[f.name] !== undefined) {
      out[f.name] = registryDefaults[f.name] as FieldValue;
      continue;
    }
    out[f.name] = defaultFormValue(f, hint.control);
  }
  return out;
}

/** Round-trip a marshalled params dict back into a FormState — used when
 *  loading a saved preset or a pinned run. */
function formFromParams(
  card: ToolCard,
  params: Record<string, unknown>,
): FormState {
  const out: FormState = {};
  for (const f of card.input_fields as ToolFieldDescriptor[]) {
    const hint = paramHintFor(card.tool_name, f.name);
    const v = params[f.name];
    if (v === undefined) {
      out[f.name] = defaultFormValue(f, hint.control);
      continue;
    }
    // Structured shapes pass through unchanged; scalars stringify so the
    // text/number inputs see them.
    if (
      hint.control === 'series_spec' ||
      hint.control === 'series_spec_list' ||
      hint.control === 'multi_tenor'
    ) {
      out[f.name] = v as FieldValue;
    } else {
      out[f.name] = String(v);
    }
  }
  return out;
}

function defaultPresetTitle(
  meta: ModelMetadata | null,
  params: Record<string, unknown>,
): string {
  const display = meta?.displayName ?? 'Model';
  // Best-effort label assembly from common params.
  const bits: string[] = [display];
  const target = params['target_spec'] as Record<string, unknown> | undefined;
  if (target?.curve_family && target?.tenor) {
    bits.push(`${target.curve_family} ${target.tenor}`);
  }
  const curve = params['curve_family'];
  if (typeof curve === 'string') bits.push(curve);
  const window = params['regression_window_days'];
  if (typeof window === 'number') bits.push(`${window}d window`);
  const lookback = params['lookback_days'];
  if (typeof lookback === 'number') bits.push(`${lookback}d lookback`);
  return bits.slice(0, 4).join(' · ');
}
