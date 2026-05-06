// ============================================================================
// WindowSlider + LookbackSlider — numeric inputs with preset chip strips
// ----------------------------------------------------------------------------
// Two visually-similar controls that bind a single integer parameter:
//   - WindowSlider   → trading-day window length (typically 22..504)
//   - LookbackSlider → calendar-day display lookback (typically 365..3650)
// Both expose preset chips for the canonical desk values plus a free-text
// number input for non-preset values.
// ============================================================================

import {
  ControlField,
  PresetChipGroup,
  StyledInput,
} from './ControlPrimitives';

function NumberSlider({
  label,
  help,
  hint,
  presets,
  value,
  onChange,
  formatPreset,
  min,
  max,
}: {
  label: string;
  help?: string;
  hint: string;
  presets: number[];
  value: string;
  onChange: (next: string) => void;
  formatPreset?: (n: number) => string;
  min?: number;
  max?: number;
}) {
  const numericValue = Number(value);
  const finite = Number.isFinite(numericValue) ? numericValue : null;
  return (
    <ControlField label={label} help={help} hint={hint}>
      <div className="space-y-1.5">
        <PresetChipGroup
          presets={presets}
          value={finite}
          onChange={(n) => onChange(String(n))}
          format={formatPreset}
        />
        <StyledInput
          value={value}
          onChange={onChange}
          type="number"
          placeholder={`${min ?? '...'}–${max ?? '...'}`}
        />
      </div>
    </ControlField>
  );
}

export function WindowSlider(props: {
  label: string;
  help?: string;
  presets: number[];
  value: string;
  onChange: (next: string) => void;
}) {
  return (
    <NumberSlider
      label={props.label}
      help={props.help}
      hint="trading days"
      presets={props.presets}
      value={props.value}
      onChange={props.onChange}
      min={10}
      max={2520}
      formatPreset={(n) => {
        if (n === 22) return '1m';
        if (n === 60) return '60d';
        if (n === 126) return '6m';
        if (n === 252) return '1y';
        if (n === 504) return '2y';
        return `${n}d`;
      }}
    />
  );
}

export function LookbackSlider(props: {
  label: string;
  help?: string;
  presets: number[];
  value: string;
  onChange: (next: string) => void;
}) {
  return (
    <NumberSlider
      label={props.label}
      help={props.help}
      hint="calendar days"
      presets={props.presets}
      value={props.value}
      onChange={props.onChange}
      min={30}
      max={7300}
      formatPreset={(n) => {
        if (n === 365) return '1y';
        if (n === 730) return '2y';
        if (n === 1095) return '3y';
        if (n === 1825) return '5y';
        if (n === 3650) return '10y';
        return `${n}d`;
      }}
    />
  );
}
