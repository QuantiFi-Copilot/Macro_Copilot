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
// PR5 — Bloomberg observation-field options.  Renders for every
// descriptor whose name ends with ``field_name`` (see
// ``inferFieldControl`` in modelRegistry.ts).  The list spans both the
// sovereign yield mnemonics (YLD_*) and the OIS / generic price
// mnemonics (PX_*); the inferer doesn't try to second-guess which
// half applies to which tool — closer to the truth is to show the
// full set and let the user pick what their primitive's config.yaml
// declared as the default.  Schema-supplied ``field.examples`` win
// when present so a backend-locked enumeration overrides this list.
// ---------------------------------------------------------------------------

const BLOOMBERG_FIELD_OPTIONS: Array<{ value: string; label?: string }> = [
  { value: 'YLD_YTM_MID', label: 'YLD_YTM_MID · mid yield-to-maturity' },
  { value: 'YLD_YTM_BID', label: 'YLD_YTM_BID · bid yield' },
  { value: 'YLD_YTM_ASK', label: 'YLD_YTM_ASK · ask yield' },
  { value: 'YLD_CNV_MID', label: 'YLD_CNV_MID · conventional yield' },
  { value: 'PX_LAST', label: 'PX_LAST · last price' },
  { value: 'PX_MID', label: 'PX_MID · mid price' },
  { value: 'PX_BID', label: 'PX_BID · bid price' },
  { value: 'PX_ASK', label: 'PX_ASK · ask price' },
];

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

// ---------------------------------------------------------------------------
// PR5 — required-field validation
// ---------------------------------------------------------------------------
//
// Pre-PR5 the marshaller silently dropped empty / undefined values
// (correct — Pydantic distinguishes missing-key from empty-string,
// and the canonical wire shape omits unset optionals).  But REQUIRED
// fields with no default rendered as empty inputs that the user
// could submit blank, producing the unhelpful
// ``Field required [type=missing, input_value=...]`` error from the
// backend.  This helper collects the list so the panel can:
//
//   - disable the Run button until the gap is filled, and
//   - surface an inline error under each affected control.
//
// Pure function — no React imports — so any caller (the typed
// preset surfaces, future automation) can audit a FormState without
// rendering anything.

export interface MissingFieldFinding {
  /** ``input_fields[].name`` for the offending field. */
  name: string;
  /** Human-friendly label (snake-cased name with capitals) for
   *  banner / tooltip copy. */
  label: string;
}

export function missingRequiredFields(
  form: FormState,
  fields: ToolFieldDescriptor[],
  hintFor: (name: string) => ControlKind,
): MissingFieldFinding[] {
  const out: MissingFieldFinding[] = [];
  for (const f of fields) {
    if (!f.required) continue;
    const control = hintFor(f.name);
    if (isFieldFilled(form[f.name], control)) continue;
    out.push({ name: f.name, label: humanLabelFor(f.name) });
  }
  return out;
}

/** True when the form value for a field is non-empty given its
 *  control type.  Pure — used by ``missingRequiredFields`` and the
 *  per-field renderer to flag inline errors. */
function isFieldFilled(value: FieldValue, control: ControlKind): boolean {
  if (value === undefined || value === null) return false;
  if (control === 'series_spec') {
    const spec = value as SeriesSpecValue;
    return marshalSeriesSpec(spec) !== undefined;
  }
  if (control === 'series_spec_list') {
    const arr = (value as SeriesSpecValue[]) ?? [];
    return arr.some((s) => marshalSeriesSpec(s) !== undefined);
  }
  if (control === 'multi_tenor') {
    return ((value as string[]) ?? []).some((s) => s.length > 0);
  }
  if (typeof value === 'string') return value.length > 0;
  // Defensive: an unexpected shape we can't introspect counts as
  // filled — we'd rather let a quirky tool's submission through
  // and let the backend produce the canonical error than block on
  // a false negative here.
  return true;
}

function humanLabelFor(snake: string): string {
  return snake
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase());
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

  // PR5 — collect required fields the user hasn't filled in.  Drives
  // both the Run-button disabled state and the per-control inline
  // error.  Cheap to recompute on every render.  Re-uses the
  // ``hintFor`` helper declared above so we never compute hints
  // twice per render.
  const missing = useMemo(
    () => missingRequiredFields(form, fields, hintFor),
    // ``hintFor`` is closed over ``card.tool_name`` — recompute when
    // the tool or its visible-fields list shifts.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [form, fields, card.tool_name],
  );
  const missingByName = useMemo(() => {
    const s = new Set<string>();
    for (const m of missing) s.add(m.name);
    return s;
  }, [missing]);
  const hasMissing = missing.length > 0;
  const runDisabled = isRunning || hasMissing;
  const runTooltip = hasMissing
    ? `Fill in the required field${missing.length === 1 ? '' : 's'}: ${missing
        .map((m) => m.label)
        .join(', ')}`
    : undefined;

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
              showMissingError={missingByName.has(field.name)}
            />
          );
        })}
      </div>

      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={onRun}
          disabled={runDisabled}
          title={runTooltip}
          className={cn(
            'flex flex-1 items-center justify-center gap-2 rounded-lg border px-3 py-2 text-[12.5px] font-semibold tracking-[-0.005em] transition-all duration-150 ease-sleek',
            isRunning
              ? 'cursor-wait border-line-soft bg-white/[0.02] text-fg-muted'
              : hasMissing
                ? 'cursor-not-allowed border-line-soft bg-white/[0.02] text-fg-muted opacity-70'
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
      {hasMissing && (
        <p className="text-[10.5px] leading-[1.5] text-coral-200">
          Required: {missing.map((m) => m.label).join(', ')}.
        </p>
      )}
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
  showMissingError,
}: {
  field: ToolFieldDescriptor;
  control: ControlKind;
  label: string;
  help?: string;
  presets?: number[];
  value: FieldValue;
  setValue: (v: FieldValue) => void;
  /** PR5 — true iff the panel computed this field as a missing
   *  required input.  When set, the renderer appends a small coral
   *  caption beneath the control so the user can see exactly where
   *  the gap is.  Read-only flag; the panel owns the calculation. */
  showMissingError?: boolean;
}) {
  // PR5 — augment ``help`` with the inline-required error.  Done at
  // the renderer entrance so every control variant inherits the
  // caption without each branch having to re-thread the prop.
  const effectiveHelp = showMissingError
    ? help
      ? `${help} · required`
      : 'Required field — pick a value before running.'
    : help;
  help = effectiveHelp;
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
  if (control === 'field_name') {
    // PR5 — schema-supplied ``field.examples`` wins (a backend-
    // locked enumeration); otherwise we serve the canonical
    // Bloomberg-mnemonic vocabulary so the user can't free-text
    // a typo (``YDL_YTM_MID`` etc.) into a runtime error.
    const opts =
      field.examples && field.examples.length > 0
        ? field.examples.map((e) => ({ value: String(e) }))
        : BLOOMBERG_FIELD_OPTIONS;
    return (
      <ControlField label={label} required={field.required} help={help}>
        <StyledSelect
          value={(value as string) ?? ''}
          onChange={(v) => setValue(v)}
          options={opts}
          placeholder="select field"
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
