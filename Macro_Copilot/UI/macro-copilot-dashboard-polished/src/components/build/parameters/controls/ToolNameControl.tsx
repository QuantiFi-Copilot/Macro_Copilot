// ============================================================================
// ToolNameControl — read-only display of the tool / operator identifier.
// ----------------------------------------------------------------------------
// In PR B the workflow's topology stays locked — overriding a stage's
// ``tool_name`` would mean a different DAG shape, which is a different
// workflow.  We render this control as a styled name chip with a
// "topology-locked" caption so the user understands why it isn't
// editable.  Future PR C might add a "swap primitive" affordance.
// ============================================================================

import {
  registerControl,
} from '../lib/controlRegistry';
import type { ParamControlProps } from '../lib/controlRegistry';
import { ControlShell } from './ControlShell';

const ToolNameControl = ({ descriptor }: ParamControlProps) => {
  const value =
    (descriptor.currentValue as string | undefined) ?? '—';

  return (
    <ControlShell
      descriptor={{ ...descriptor, readOnly: true }}
      override={undefined}
    >
      <div className="inline-flex items-center rounded-md border border-line-soft bg-white/[0.01] px-2 py-1 font-mono text-[11.5px] text-fg-secondary">
        {value}
      </div>
      <p className="mt-1 text-[10px] tracking-[0.02em] text-fg-faint">
        Topology-locked · changing this would alter the DAG shape.
      </p>
    </ControlShell>
  );
};

registerControl('tool_name', ToolNameControl);
export { ToolNameControl };
