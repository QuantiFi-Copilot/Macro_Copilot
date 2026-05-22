// ============================================================================
// Reusable control-rail primitives
// ----------------------------------------------------------------------------
// Visual chrome shared by every richer control.  Pulled out so each
// rich control (CurveTenorPicker, MultiTenorPicker, WindowSlider, etc.)
// stays focused on its own behaviour.
// ============================================================================

import { type ReactNode } from 'react';
import { ChevronDown } from 'lucide-react';
import { cn } from '@/utils/cn';

export type ControlFieldProps = {
  label: string;
  required?: boolean;
  /** Compact help text under the control. */
  help?: string;
  /** Right-aligned hint shown next to the label (e.g. type tag). */
  hint?: string;
  children: ReactNode;
};

export function ControlField({
  label,
  required,
  help,
  hint,
  children,
}: ControlFieldProps) {
  return (
    <label className="block">
      <div className="flex items-baseline justify-between gap-2">
        <span className="mono text-[10.5px] text-fg-secondary">
          {label}
          {required ? (
            <span className="ml-1 text-coral-300/80">*</span>
          ) : null}
        </span>
        {hint ? (
          <span className="text-[9px] uppercase tracking-[0.08em] text-fg-faint">
            {hint}
          </span>
        ) : null}
      </div>
      <div className="mt-1">{children}</div>
      {help ? (
        <p className="mt-1 text-[10.5px] leading-snug text-fg-faint">
          {help}
        </p>
      ) : null}
    </label>
  );
}

// ---------------------------------------------------------------------------
// Styled select — appearance-stripped <select> with a chevron overlay.
// ---------------------------------------------------------------------------

export type SelectOption = {
  value: string;
  label?: string;
};

export function StyledSelect({
  value,
  onChange,
  options,
  placeholder,
}: {
  value: string;
  onChange: (next: string) => void;
  options: SelectOption[];
  placeholder?: string;
}) {
  return (
    <div className="relative">
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className={cn(
          'mono w-full appearance-none rounded-md border border-line-soft bg-white/[0.02] px-2.5 py-1.5 pr-7 text-[11.5px] text-fg-primary',
          'transition-colors hover:border-ice-400/40 focus:border-ice-400/60 focus:outline-none',
        )}
      >
        {placeholder ? (
          <option value="" className="bg-surface text-fg-muted">
            {placeholder}
          </option>
        ) : null}
        {options.map((opt) => (
          <option
            key={opt.value}
            value={opt.value}
            className="bg-surface text-fg-primary"
          >
            {opt.label ?? opt.value}
          </option>
        ))}
      </select>
      <ChevronDown
        size={11}
        className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-fg-faint"
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Styled text/number input.
// ---------------------------------------------------------------------------

export function StyledInput({
  value,
  onChange,
  type = 'text',
  placeholder,
  list,
}: {
  value: string;
  onChange: (next: string) => void;
  type?: 'text' | 'number' | 'date';
  placeholder?: string;
  list?: string;
}) {
  return (
    <input
      type={type}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      list={list}
      className={cn(
        'mono w-full rounded-md border border-line-soft bg-white/[0.02] px-2.5 py-1.5 text-[11.5px] text-fg-primary',
        'transition-colors placeholder:text-fg-faint hover:border-ice-400/40 focus:border-ice-400/60 focus:outline-none',
      )}
    />
  );
}

// ---------------------------------------------------------------------------
// Preset chip group — small horizontal pill buttons, used by
// WindowSlider and LookbackSlider.
// ---------------------------------------------------------------------------

export function PresetChipGroup({
  presets,
  value,
  onChange,
  format,
}: {
  presets: number[];
  value: number | null;
  onChange: (n: number) => void;
  format?: (n: number) => string;
}) {
  return (
    <div className="flex flex-wrap items-center gap-1">
      {presets.map((p) => {
        const active = value === p;
        return (
          <button
            key={p}
            type="button"
            onClick={() => onChange(p)}
            className={cn(
              'mono rounded border px-2 py-0.5 text-[10.5px] transition-colors',
              active
                ? 'border-ice-400/40 bg-ice-500/15 text-ice-100'
                : 'border-line-soft bg-white/[0.02] text-fg-secondary hover:border-line-strong hover:text-fg-primary',
            )}
          >
            {format ? format(p) : String(p)}
          </button>
        );
      })}
    </div>
  );
}
