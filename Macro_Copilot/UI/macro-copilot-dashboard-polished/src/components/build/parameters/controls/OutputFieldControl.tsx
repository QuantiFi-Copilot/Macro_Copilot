// ============================================================================
// OutputFieldControl — read-only display of the bridge lift field.
// ----------------------------------------------------------------------------
// Same rationale as ToolNameControl — the substrate's lineage hash
// includes ``output_field``, so editing it would invalidate the
// stage's identity.  Surface as a styled name chip with a clear
// caption.
// ============================================================================

import {
  registerControl,
} from '../lib/controlRegistry';
import type { ParamControlProps } from '../lib/controlRegistry';
import { ControlShell } from './ControlShell';

const OutputFieldControl = ({ descriptor }: ParamControlProps) => {
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
        Bridge-lift field · part of the artifact's content hash.
      </p>
    </ControlShell>
  );
};

registerControl('output_field', OutputFieldControl);
export { OutputFieldControl };
