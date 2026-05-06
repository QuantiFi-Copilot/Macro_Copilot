// ============================================================================
// SeriesSpecPicker — curve_family + tenor + (optional) field_name combo
// ----------------------------------------------------------------------------
// Renders a single SeriesSpec dict as three coupled controls.  Used by
// rolling_regression target_spec, beta_adjusted_spread target/hedge specs,
// half_life series_spec, and any other primitive whose input schema embeds
// the canonical SeriesSpec shape.
//
// Output shape (passed to the run endpoint):
//   { curve_family: string, tenor: string, field_name?: string }
// ============================================================================

import {
  ALL_CURVES,
  CANONICAL_FIELDS,
  CANONICAL_TENORS,
} from './CurveAndTenor';
import {
  ControlField,
  StyledSelect,
} from './ControlPrimitives';

export type SeriesSpecValue = {
  curve_family?: string;
  tenor?: string;
  field_name?: string | null;
};

export function SeriesSpecPicker({
  label,
  required,
  help,
  value,
  onChange,
}: {
  label: string;
  required?: boolean;
  help?: string;
  value: SeriesSpecValue;
  onChange: (next: SeriesSpecValue) => void;
}) {
  const update = <K extends keyof SeriesSpecValue>(
    key: K,
    v: SeriesSpecValue[K],
  ) => onChange({ ...value, [key]: v });

  return (
    <div className="rounded-md border border-line-soft bg-white/[0.012] px-2.5 py-2.5">
      <div className="flex items-baseline justify-between">
        <span className="mono text-[10.5px] text-fg-secondary">
          {label}
          {required ? (
            <span className="ml-1 text-coral-300/80">*</span>
          ) : null}
        </span>
        <span className="text-[9px] uppercase tracking-[0.08em] text-fg-faint">
          SeriesSpec
        </span>
      </div>

      <div className="mt-2 grid grid-cols-2 gap-2">
        <ControlField label="curve">
          <StyledSelect
            value={value.curve_family ?? ''}
            onChange={(v) => update('curve_family', v)}
            options={ALL_CURVES}
            placeholder="select curve"
          />
        </ControlField>
        <ControlField label="tenor">
          <StyledSelect
            value={value.tenor ?? ''}
            onChange={(v) => update('tenor', v)}
            options={CANONICAL_TENORS.map((t) => ({ value: t }))}
            placeholder="select tenor"
          />
        </ControlField>
      </div>

      <div className="mt-2">
        <ControlField label="field (optional)">
          <StyledSelect
            value={value.field_name ?? ''}
            onChange={(v) => update('field_name', v === '' ? null : v)}
            options={CANONICAL_FIELDS}
          />
        </ControlField>
      </div>

      {help ? (
        <p className="mt-2 text-[10.5px] leading-snug text-fg-faint">
          {help}
        </p>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// SeriesSpecListPicker — a repeating SeriesSpecPicker for `regressor_specs`.
// ---------------------------------------------------------------------------

export function SeriesSpecListPicker({
  label,
  help,
  value,
  onChange,
}: {
  label: string;
  help?: string;
  value: SeriesSpecValue[];
  onChange: (next: SeriesSpecValue[]) => void;
}) {
  const items = value.length === 0 ? [{}] : value;
  const update = (idx: number, next: SeriesSpecValue) => {
    const copy = [...items];
    copy[idx] = next;
    onChange(copy);
  };
  const remove = (idx: number) => {
    onChange(items.filter((_, i) => i !== idx));
  };
  const add = () => onChange([...items, {}]);

  return (
    <div className="space-y-2">
      <div className="flex items-baseline justify-between">
        <span className="mono text-[10.5px] text-fg-secondary">{label}</span>
        <button
          type="button"
          onClick={add}
          className="rounded border border-line-soft bg-white/[0.02] px-2 py-0.5 text-[10.5px] text-fg-secondary transition-colors hover:border-ice-400/40 hover:text-fg-primary"
        >
          + add
        </button>
      </div>
      <div className="space-y-2">
        {items.map((spec, idx) => (
          <div key={idx} className="relative">
            <SeriesSpecPicker
              label={`regressor #${idx + 1}`}
              value={spec}
              onChange={(v) => update(idx, v)}
            />
            {items.length > 1 ? (
              <button
                type="button"
                onClick={() => remove(idx)}
                className="absolute right-2 top-2 rounded border border-line-soft bg-white/[0.02] px-1.5 py-0.5 text-[9.5px] text-fg-muted transition-colors hover:border-coral-400/40 hover:text-coral-300"
              >
                remove
              </button>
            ) : null}
          </div>
        ))}
      </div>
      {help ? (
        <p className="mt-1 text-[10.5px] leading-snug text-fg-faint">
          {help}
        </p>
      ) : null}
    </div>
  );
}
