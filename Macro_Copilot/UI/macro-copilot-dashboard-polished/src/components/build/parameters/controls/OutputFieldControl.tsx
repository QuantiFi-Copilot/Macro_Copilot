// ============================================================================
// OutputFieldControl — dual-mode renderer for ``output_field`` descriptors.
// ----------------------------------------------------------------------------
// Like ``ToolNameControl``, two callers reach this renderer:
//
//   1. Node-level ``output_field`` params from the persisted
//      ``NodeSummary.params``.  The substrate's lineage hash
//      includes this field, so editing it would invalidate the
//      stage's identity.  Renders as a styled name chip with the
//      "bridge-lift field · part of the artifact's content hash"
//      caption.  ``descriptor.readOnly === true`` selects this mode.
//
//   2. PR3 — slot-level ``*_output_field`` slots.  These are user-
//      settable through the fork pipeline.  The deriver pairs each
//      such slot with its sibling ``*_tool_name`` slot and populates
//      ``meta.allowedFields`` with the chosen tool's output_fields.
//      Renders as a ``<select>`` filtered by the paired tool.
//
// Cross-slot consistency
// ----------------------
// The control highlights the currently-selected value in coral when
// it falls OUTSIDE the paired tool's output_fields (e.g. the user
// changed the tool slot but hasn't picked a matching field yet).
// ``validateOverrides`` does the same check at submit time so the
// Apply button reflects the same invariant.
// ============================================================================

import { cn } from '@/utils/cn';
import {
  registerControl,
} from '../lib/controlRegistry';
import type { ParamControlProps } from '../lib/controlRegistry';
import { ControlShell } from './ControlShell';

const OutputFieldControl = ({
  descriptor,
  override,
  onChange,
}: ParamControlProps) => {
  const value =
    override?.value !== undefined
      ? String(override.value)
      : (descriptor.currentValue as string | undefined) ?? '';
  const meta =
    descriptor.meta.kind === 'output_field' ? descriptor.meta : null;
  const allowed = meta?.allowedFields;
  const selectedTool = meta?.selectedTool;

  // Node-level / topology-locked rendering.
  if (descriptor.readOnly) {
    return (
      <ControlShell descriptor={descriptor} override={undefined}>
        <div className="inline-flex items-center rounded-md border border-line-soft bg-white/[0.01] px-2 py-1 font-mono text-[11.5px] text-fg-secondary">
          {value || '—'}
        </div>
        <p className="mt-1 text-[10px] tracking-[0.02em] text-fg-faint">
          Bridge-lift field · part of the artifact&apos;s content hash.
        </p>
      </ControlShell>
    );
  }

  // PR3 — slot-level: catalogue / paired-tool not loaded → degraded
  // read-only.  No misleading free-text input.
  if (!allowed || allowed.length === 0) {
    const caption = !selectedTool
      ? 'Pick a tool above to load its output fields.'
      : `Loading output fields for ${selectedTool}…`;
    return (
      <ControlShell descriptor={descriptor} override={undefined}>
        <div className="inline-flex items-center rounded-md border border-line-soft bg-white/[0.01] px-2 py-1 font-mono text-[11.5px] text-fg-secondary">
          {value || '—'}
        </div>
        <p className="mt-1 text-[10px] tracking-[0.02em] text-fg-faint">
          {caption}
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
        {value === '' && <option value="">— select field —</option>}
        {isMissing && (
          <option value={value} disabled>
            {value} (not in {selectedTool ?? 'selected tool'})
          </option>
        )}
        {allowed.map((f) => (
          <option key={f} value={f}>
            {f}
          </option>
        ))}
      </select>
      {selectedTool && (
        <p className="mt-1 text-[10px] tracking-[0.02em] text-fg-faint">
          Output fields exposed by{' '}
          <code className="font-mono text-fg-muted">{selectedTool}</code>.
        </p>
      )}
      {isMissing && (
        <p className="mt-1 text-[10px] tracking-[0.02em] text-coral-200">
          Selection is inconsistent with the paired tool — pick one of
          its output fields above.
        </p>
      )}
    </ControlShell>
  );
};

registerControl('output_field', OutputFieldControl);
export { OutputFieldControl };
