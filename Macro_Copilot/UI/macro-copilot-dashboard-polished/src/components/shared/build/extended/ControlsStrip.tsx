// ============================================================================
// shared/build/extended/ControlsStrip.tsx
// ----------------------------------------------------------------------------
// Editable controls row for the extended Build canvas.  Primary controls
// (curve_family, tenor, lookback, field) render inline; controls flagged
// ``advanced: true`` collapse behind an "Advanced ▾" expander.
//
// Each control change calls ``onChange(name, value)`` so the per-tool
// wrapper can re-encode URL state + re-fetch.  Finance-blind — accepts
// any ControlDescriptor list.
// ============================================================================

import { useState } from 'react';
import { ChevronDown, ChevronRight, RotateCcw } from 'lucide-react';
import type { ControlDescriptor } from '../lib/types';

type Props = {
  controls: ReadonlyArray<ControlDescriptor>;
  onChange: (name: string, value: string) => void;
  onReset?: () => void;
};

export function ControlsStrip({ controls, onChange, onReset }: Props) {
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const primary = controls.filter((c) => !c.advanced);
  const advanced = controls.filter((c) => c.advanced);

  return (
    <section className="card flex flex-wrap items-end gap-3 px-5 py-3.5">
      {primary.map((c) => (
        <ControlField key={c.name} control={c} onChange={onChange} />
      ))}

      {advanced.length > 0 && (
        <button
          type="button"
          onClick={() => setAdvancedOpen((v) => !v)}
          className="flex items-center gap-1 self-end rounded-md border border-line-subtle px-2.5 py-1.5 text-[11.5px] text-fg-secondary transition-colors hover:border-line-strong hover:text-fg-primary"
          aria-expanded={advancedOpen}
        >
          {advancedOpen ? (
            <ChevronDown size={12} strokeWidth={1.75} />
          ) : (
            <ChevronRight size={12} strokeWidth={1.75} />
          )}
          Advanced
        </button>
      )}

      {advancedOpen && advanced.map((c) => (
        <ControlField key={c.name} control={c} onChange={onChange} />
      ))}

      {onReset && (
        <button
          type="button"
          onClick={onReset}
          className="ml-auto flex items-center gap-1.5 self-end rounded-md border border-line-subtle px-2.5 py-1.5 text-[11px] text-fg-muted transition-colors hover:border-line-strong hover:text-fg-secondary"
        >
          <RotateCcw size={11} strokeWidth={1.75} />
          Reset to defaults
        </button>
      )}
    </section>
  );
}

function ControlField({
  control,
  onChange,
}: {
  control: ControlDescriptor;
  onChange: (name: string, value: string) => void;
}) {
  const id = `ctrl-${control.name}`;
  return (
    <div className="flex min-w-[120px] flex-col gap-1">
      <label htmlFor={id} className="kicker text-fg-muted">
        {control.label}
      </label>
      {control.kind === 'enum' && control.options ? (
        <select
          id={id}
          value={control.value}
          onChange={(e) => onChange(control.name, e.target.value)}
          className="rounded-md border border-line-subtle bg-bg-elevated px-2.5 py-1.5 text-[12.5px] text-fg-primary focus:border-ice-300 focus:outline-none"
        >
          {control.options.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
      ) : control.kind === 'number' ? (
        <input
          id={id}
          type="number"
          value={control.value}
          min={control.min}
          max={control.max}
          onChange={(e) => onChange(control.name, e.target.value)}
          className="w-[120px] rounded-md border border-line-subtle bg-bg-elevated px-2.5 py-1.5 text-[12.5px] text-fg-primary focus:border-ice-300 focus:outline-none"
        />
      ) : control.kind === 'date' ? (
        // As-of / replay control — native picker + a "Live" clear that
        // mirrors the Monitor's AsOfControl.  Empty value = latest live
        // data; a YYYY-MM-DD value = compute as of that historical date.
        <div className="flex items-center gap-1.5">
          <input
            id={id}
            type="date"
            value={control.value}
            onChange={(e) => onChange(control.name, e.target.value)}
            aria-label={control.label}
            className="rounded-md border border-line-subtle bg-bg-elevated px-2.5 py-1.5 text-[12.5px] text-fg-primary outline-none focus:border-ice-300 [color-scheme:dark]"
          />
          {control.value && (
            <button
              type="button"
              onClick={() => onChange(control.name, '')}
              title="Back to latest (live) data"
              className="rounded px-1.5 py-1 text-[10.5px] font-medium text-fg-muted transition-colors hover:text-fg-primary"
            >
              Live
            </button>
          )}
        </div>
      ) : (
        <input
          id={id}
          type="text"
          value={control.value}
          onChange={(e) => onChange(control.name, e.target.value)}
          className="w-[140px] rounded-md border border-line-subtle bg-bg-elevated px-2.5 py-1.5 text-[12.5px] text-fg-primary focus:border-ice-300 focus:outline-none"
        />
      )}
    </div>
  );
}
