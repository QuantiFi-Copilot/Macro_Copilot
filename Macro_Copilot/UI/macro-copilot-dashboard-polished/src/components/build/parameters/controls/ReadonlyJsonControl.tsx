// ============================================================================
// ReadonlyJsonControl — fallback for deeply-nested dicts / unknown shapes.
// ----------------------------------------------------------------------------
// Renders the raw JSON in a compact ``<pre>`` block so the user can
// inspect what's stored without us inventing a per-shape editor.
// Used by ``deriveControlsForStage`` for anything more than one
// level deep, and registered as the fallback for the registry.
// ============================================================================

import { useMemo } from 'react';
import {
  registerControl,
  registerFallbackControl,
} from '../lib/controlRegistry';
import type { ParamControlProps } from '../lib/controlRegistry';
import { ControlShell } from './ControlShell';

const ReadonlyJsonControl = ({ descriptor }: ParamControlProps) => {
  const serialized = useMemo(() => {
    try {
      return JSON.stringify(descriptor.currentValue, null, 2);
    } catch {
      return String(descriptor.currentValue);
    }
  }, [descriptor.currentValue]);

  return (
    <ControlShell
      descriptor={{ ...descriptor, readOnly: true }}
      override={undefined}
    >
      <pre className="max-h-32 overflow-auto rounded-md border border-line-soft bg-white/[0.008] px-2 py-1.5 font-mono text-[10.5px] leading-[1.5] text-fg-secondary">
        {serialized}
      </pre>
      <p className="mt-1 text-[10px] tracking-[0.02em] text-fg-faint">
        Read-only · nested shape not editable on this surface.
      </p>
    </ControlShell>
  );
};

registerControl('readonly_json', ReadonlyJsonControl);
registerFallbackControl(ReadonlyJsonControl);
export { ReadonlyJsonControl };
