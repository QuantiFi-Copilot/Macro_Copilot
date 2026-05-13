// ============================================================================
// ParametersPanel — schema-driven controls rail
// ----------------------------------------------------------------------------
// Renders one control per input field of the primitive's ToolCard, dispatching
// to a rich control (curve+tenor picker, multi-tenor selector, sliders, etc.)
// based on the model registry's paramHints — falling back to the same
// auto-rendered text/number/boolean inputs the simpler PrimitiveModelView
// uses when no hint is registered.
//
// State shape
// -----------
// The panel keeps two parallel forms of every parameter:
//   - `formValues`: the raw user-facing form state (strings for text/number
//     inputs, structured objects for SeriesSpec / multi-tenor controls)
//   - `paramsForRun`: the marshalled dict that POST /tools/{name}/run expects
//
// Marshalling happens at submission time — the caller calls `getParams()` to
// pull out the runnable shape.  Form state stays unconstrained so the user
// can type partial values without breaking the controls.
// ============================================================================

import { useMemo } from 'react';
import { Loader2, Play } from 'lucide-react';
import type { ToolCard, ToolFieldDescriptor } from '@/types/workflows';
import { paramHintFor, type ControlKind } from '@/lib/modelRegistry';
import { cn } from '@/utils/cn';
import {
  ControlField,
  StyledInput,
  StyledSelect,
} from './controls/ControlPrimitives';
import {
  SeriesSpecPicker,
  SeriesSpecListPicker,
  type SeriesSpecValue,
} from './controls/SeriesSpecPicker';
import { MultiTenorPicker } from './controls/MultiTenorPicker';
import { LookbackSlider, WindowSlider } from './controls/Sliders';
import {
  ALL_CURVES,
  CANONICAL_TENORS,
} from './controls/CurveAndTenor';

// ---------------------------------------------------------------------------
// FormState — the union of "what the controls write" for any one field.
// ---------------------------------------------------------------------------

export type FieldValue =
  | string
  | string[]
  | SeriesSpecValue
  | SeriesSpecValue[]
  | null;

export type FormState = Record<string, FieldValue>;

// ---------------------------------------------------------------------------
// Field-priority sort — same heuristic the simpler PrimitiveModelView uses.
// Required fields first, then a hand-picked priority order, then alpha.
// ---------------------------------------------------------------------------

const PRIORITY = [
  'target_spec',
  'regressor_specs',
  'hedge_spec',
  'series_spec',
  'curve_family',
  'curve_family_1',
  'curve_family_2',
  'tenors',
  'tenor',
  'short_tenor',
  'belly_tenor',
  'long_tenor',
  'target_tenor',
  'forward_start',
  'forward_length',
  'start_date',
  'end_date',
  'regression_window_days',
  'rolling_window_days',
  'lookback_days',
  'n_components',
  'change_frequency',
  'field_name',
];

export function sortFields(
  fields: ToolFieldDescriptor[],
  hidden: Set<string>,
): ToolFieldDescriptor[] {
  const idx = (n: string) => {
    const i = PRIORITY.indexOf(n);
    return i === -1 ? 999 : i;
  };
  return [...fields]
    .filter((f) => !hidden.has(f.name))
    .sort((a, b) => {
      if (a.required !== b.required) return a.required ? -1 : 1;
      return idx(a.name) - idx(b.name);
    });
}

// ---------------------------------------------------------------------------
// Default form value for a field — picks a structured shape for rich
// controls, or a stringified default/example for the auto fall-through.
// ---------------------------------------------------------------------------

export function defaultFormValue(
  field: ToolFieldDescriptor,
  control: ControlKind,
): FieldValue {
  if (control === 'series_spec') return {} as SeriesSpecValue;
  if (control === 'series_spec_list') return [] as SeriesSpecValue[];
  if (control === 'multi_tenor') return [] as string[];

  if (field.default !== null && field.default !== undefined) {
    return String(field.default);
  }
  if (field.examples && field.examples.length > 0) {
    return String(field.examples[0]);
  }
  return '';
}

// ---------------------------------------------------------------------------
// Marshalling — turn a FormState back into a dict the run endpoint accepts.
// ---------------------------------------------------------------------------

function coerceScalar(raw: string, type: string): unknown {
  const t = (type || '').toLowerCase();
  if (raw === '') return undefined;
  if (t.includes('integer') || t === 'int') {
    const n = Number(raw);
    return Number.isFinite(n) ? Math.trunc(n) : raw;
  }
  if (t.includes('number') || t === 'float') {
    const n = Number(raw);
    return Number.isFinite(n) ? n : raw;
  }
  if (t === 'boolean' || t === 'bool') {
    return raw === 'true' || raw === '1' || raw === 'yes';
  }
  return raw;
}

function marshalSeriesSpec(spec: SeriesSpecValue): Record<string, unknown> | undefined {
  const out: Record<string, unknown> = {};
  if (spec.curve_family) out.curve_family = spec.curve_family;
  if (spec.tenor) out.tenor = spec.tenor;
  if (spec.field_name) out.field_name = spec.field_name;
  if (Object.keys(out).length === 0) return undefined;
  return out;
}

export function marshalForm(
  form: FormState,
  fields: ToolFieldDescriptor[],
  hintFor: (name: string) => ControlKind,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const f of fields) {
    const control = hintFor(f.name);
    const v = form[f.name];

    if (v === undefined || v === null) continue;

    if (control === 'series_spec') {
      const spec = marshalSeriesSpec(v as SeriesSpecValue);
      if (spec !== undefined) out[f.name] = spec;
      continue;
    }
    if (control === 'series_spec_list') {
      const arr = (v as SeriesSpecValue[])
        .map(marshalSeriesSpec)
        .filter((x): x is Record<string, unknown> => !!x);
      if (arr.length > 0) out[f.name] = arr;
      continue;
    }
    if (control === 'multi_tenor') {
      const arr = (v as string[]).filter((s) => s.length > 0);
      if (arr.length > 0) out[f.name] = arr;
      continue;
    }

    // Scalar path
    if (typeof v !== 'string') continue;
    if (v === '') continue;
    out[f.name] = coerceScalar(v, f.type);
  }
  return out;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function ParametersPanel({
  card,
  form,
  onChange,
  onRun,
  onSavePreset,
  isRunning,
}: {
  card: ToolCard;
  form: FormState;
  onChange: (next: FormState) => void;
  onRun: () => void;
  onSavePreset?: () => void;
  isRunning: boolean;
}) {
  const hintFor = (name: string) =>
    paramHintFor(card.tool_name, name).control;
  const hidden = useMemo(() => {
    const s = new Set<string>();
    for (const f of card.input_fields) {
      const h = paramHintFor(card.tool_name, f.name);
      if (h.hidden) s.add(f.name);
    }
    return s;
  }, [card.tool_name, card.input_fields]);

  const fields = useMemo(
    () => sortFields(card.input_fields, hidden),
    [card.input_fields, hidden],
  );

  const setField = (name: string, v: FieldValue) =>
    onChange({ ...form, [name]: v });

  return (
    <div className="space-y-4">
      <div className="space-y-3">
        {fields.map((field) => {
          const hint = paramHintFor(card.tool_name, field.name);
          const label = hint.label ?? humanLabel(field.name);
          const help = hint.help ?? field.description ?? undefined;
          return (
            <FieldRenderer
              key={field.name}
              field={field}
              control={hint.control}
              label={label}
              help={help}
              presets={hint.presets}
              value={form[field.name]}
              setValue={(v) => setField(field.name, v)}
            />
          );
        })}
      </div>

      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={onRun}
          disabled={isRunning}
          className={cn(
            'flex flex-1 items-center justify-center gap-2 rounded-lg border px-3 py-2 text-[12.5px] font-semibold tracking-[-0.005em] transition-all duration-150 ease-sleek',
            isRunning
              ? 'cursor-wait border-line-soft bg-white/[0.02] text-fg-muted'
              : 'border-ice-400/40 bg-gradient-to-b from-ice-500/25 to-ice-700/25 text-ice-100 hover:border-ice-400/60 hover:from-ice-500/35 hover:to-ice-700/35',
          )}
        >
          {isRunning ? (
            <>
              <Loader2 size={13} className="animate-spin" />
              Running…
            </>
          ) : (
            <>
              <Play size={12} />
              Run
            </>
          )}
        </button>
        {onSavePreset ? (
          <button
            type="button"
            onClick={onSavePreset}
            disabled={isRunning}
            className="rounded-lg border border-line-soft bg-white/[0.02] px-3 py-2 text-[12px] font-medium text-fg-secondary transition-colors hover:border-line-strong hover:text-fg-primary disabled:opacity-50"
            title="Save current configuration as a preset"
          >
            Save preset
          </button>
        ) : null}
      </div>
    </div>
  );
}

function humanLabel(snake: string): string {
  return snake
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

// ---------------------------------------------------------------------------
// FieldRenderer — picks the right control implementation for the hint.
// ---------------------------------------------------------------------------

function FieldRenderer({
  field,
  control,
  label,
  help,
  presets,
  value,
  setValue,
}: {
  field: ToolFieldDescriptor;
  control: ControlKind;
  label: string;
  help?: string;
  presets?: number[];
  value: FieldValue;
  setValue: (v: FieldValue) => void;
}) {
  if (control === 'series_spec') {
    return (
      <SeriesSpecPicker
        label={label}
        required={field.required}
        help={help}
        value={(value as SeriesSpecValue) ?? {}}
        onChange={(v) => setValue(v)}
      />
    );
  }
  if (control === 'series_spec_list') {
    return (
      <SeriesSpecListPicker
        label={label}
        help={help}
        value={(value as SeriesSpecValue[]) ?? []}
        onChange={(v) => setValue(v)}
      />
    );
  }
  if (control === 'multi_tenor') {
    return (
      <MultiTenorPicker
        label={label}
        help={help}
        value={(value as string[]) ?? []}
        onChange={(v) => setValue(v)}
      />
    );
  }
  if (control === 'curve_family') {
    return (
      <ControlField label={label} required={field.required} help={help}>
        <StyledSelect
          value={(value as string) ?? ''}
          onChange={(v) => setValue(v)}
          options={ALL_CURVES}
          placeholder="select curve"
        />
      </ControlField>
    );
  }
  if (control === 'tenor') {
    return (
      <ControlField label={label} required={field.required} help={help}>
        <StyledSelect
          value={(value as string) ?? ''}
          onChange={(v) => setValue(v)}
          options={CANONICAL_TENORS.map((t) => ({ value: t }))}
          placeholder="select tenor"
        />
      </ControlField>
    );
  }
  if (control === 'window_slider') {
    return (
      <WindowSlider
        label={label}
        help={help}
        presets={presets ?? [22, 60, 126, 252, 504]}
        value={(value as string) ?? ''}
        onChange={(v) => setValue(v)}
      />
    );
  }
  if (control === 'lookback_slider') {
    return (
      <LookbackSlider
        label={label}
        help={help}
        presets={presets ?? [365, 730, 1095, 1825, 3650]}
        value={(value as string) ?? ''}
        onChange={(v) => setValue(v)}
      />
    );
  }
  if (control === 'enum' && field.examples && field.examples.length > 0) {
    const examples = field.examples.map(String);
    return (
      <ControlField label={label} required={field.required} help={help}>
        <StyledSelect
          value={(value as string) ?? ''}
          onChange={(v) => setValue(v)}
          options={examples.map((e) => ({ value: e }))}
        />
      </ControlField>
    );
  }
  if (control === 'date') {
    return (
      <ControlField label={label} required={field.required} help={help}>
        <StyledInput
          type="date"
          value={(value as string) ?? ''}
          onChange={(v) => setValue(v)}
        />
      </ControlField>
    );
  }

  // Auto fall-through (boolean / number / text).
  const t = field.type.toLowerCase();
  const isBool = t === 'boolean' || t === 'bool';
  const isNumber = t.includes('integer') || t.includes('number');

  if (isBool) {
    return (
      <ControlField label={label} required={field.required} help={help}>
        <StyledSelect
          value={(value as string) ?? 'false'}
          onChange={(v) => setValue(v)}
          options={[
            { value: 'true' },
            { value: 'false' },
          ]}
        />
      </ControlField>
    );
  }
  return (
    <ControlField label={label} required={field.required} help={help}>
      <StyledInput
        value={(value as string) ?? ''}
        onChange={(v) => setValue(v)}
        type={isNumber ? 'number' : 'text'}
        placeholder={
          field.default !== null && field.default !== undefined
            ? String(field.default)
            : ''
        }
      />
    </ControlField>
  );
}
