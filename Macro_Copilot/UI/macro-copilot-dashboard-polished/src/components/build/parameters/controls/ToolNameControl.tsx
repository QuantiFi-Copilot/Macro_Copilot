// ============================================================================
// ToolNameControl — dual-mode renderer for ``tool_name`` descriptors.
// ----------------------------------------------------------------------------
// Two distinct callers hit this renderer:
//
//   1. Node-level ``tool_name`` params (from ``deriveControlsForStage``).
//      Topology-locked — changing the tool would mean a different
//      DAG shape, which is a different workflow.  Renders as a styled
//      name chip with a "topology-locked" caption.  ``descriptor.
//      readOnly === true`` flags this case.
//
//   2. PR3 — slot-level ``*_tool_name`` slots (from
//      ``deriveSlotControls``).  These are user-settable through the
//      fork pipeline; the substrate honours them as
//      ``slot_overrides`` and re-binds the stage to the chosen tool
//      on the variant workspace.  When the deriver populates
//      ``meta.allowedTools`` (catalogue loaded), render a
//      ``<select>`` over that list.  When the catalogue hasn't
//      loaded yet, degrade to the read-only chip + a "loading
//      tools…" caption rather than rendering a misleading free-text
//      input.
//
// Choosing between the two modes
// ------------------------------
// We branch on ``descriptor.readOnly`` first (caller-asserted lock)
// and then on whether ``meta.allowedTools`` is non-empty (catalogue-
// loaded fallback).
// ============================================================================

import { cn } from '@/utils/cn';
import {
  registerControl,
} from '../lib/controlRegistry';
import type { ParamControlProps } from '../lib/controlRegistry';
import { ControlShell } from './ControlShell';

const ToolNameControl = ({
  descriptor,
  override,
  onChange,
}: ParamControlProps) => {
  const value =
    override?.value !== undefined
      ? String(override.value)
      : (descriptor.currentValue as string | undefined) ?? '';
  const allowed =
    descriptor.meta.kind === 'tool_name' ? descriptor.meta.allowedTools : undefined;

  // Caller-asserted topology lock → render as a styled name chip with
  // a clear caption (node-level tool_name params live in this branch).
  if (descriptor.readOnly) {
    return (
      <ControlShell
        descriptor={descriptor}
        override={undefined}
      >
        <div className="inline-flex items-center rounded-md border border-line-soft bg-white/[0.01] px-2 py-1 font-mono text-[11.5px] text-fg-secondary">
          {value || '—'}
        </div>
        <p className="mt-1 text-[10px] tracking-[0.02em] text-fg-faint">
          Topology-locked · changing this would alter the DAG shape.
        </p>
      </ControlShell>
    );
  }

  // PR3 — slot-level tool_name selector.  Editable when the catalogue
  // has loaded; degraded read-only when it hasn't (the deriver leaves
  // ``allowedTools`` undefined in that case).
  if (!allowed || allowed.length === 0) {
    return (
      <ControlShell descriptor={descriptor} override={undefined}>
        <div className="inline-flex items-center rounded-md border border-line-soft bg-white/[0.01] px-2 py-1 font-mono text-[11.5px] text-fg-secondary">
          {value || '—'}
        </div>
        <p className="mt-1 text-[10px] tracking-[0.02em] text-fg-faint">
          Loading tool catalogue…
        </p>
      </ControlShell>
    );
  }

  const isMissing = value !== '' && !allowed.includes(value);

  return (
    <ControlShell
      descriptor={descriptor}
      override={override}
      onRevert={override ? () => onChange(undefined) : undefined}
    >
      <select
        value={value}
        onChange={(e) =>
          onChange(e.target.value === '' ? undefined : e.target.value)
        }
        className={cn(
          'w-full rounded-md border border-line-soft bg-white/[0.012] px-2 py-1.5 font-mono text-[11.5px] text-fg-primary focus:border-ice-400/40 focus:outline-none focus:ring-1 focus:ring-ice-400/30',
          isMissing && 'border-coral-400/50 text-coral-100',
        )}
      >
        {value === '' && <option value="">— select tool —</option>}
        {isMissing && (
          <option value={value} disabled>
            {value} (not registered)
          </option>
        )}
        {allowed.map((t) => (
          <option key={t} value={t}>
            {t}
          </option>
        ))}
      </select>
      {isMissing && (
        <p className="mt-1 text-[10px] tracking-[0.02em] text-coral-200">
          Current value is not in the registered catalogue.
        </p>
      )}
    </ControlShell>
  );
};

registerControl('tool_name', ToolNameControl);
export { ToolNameControl };
