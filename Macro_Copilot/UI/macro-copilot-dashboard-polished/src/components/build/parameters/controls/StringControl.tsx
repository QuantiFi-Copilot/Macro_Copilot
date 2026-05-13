// ============================================================================
// StringControl — fallback single-line input.
// ----------------------------------------------------------------------------
// Reached only for leaf names that don't match any specialised
// pattern AND whose value isn't numeric / boolean / dict.  Most
// common reasonable case: read-only identifier strings (tool_name,
// operator_name).  When the descriptor is readOnly, we render as a
// compact value chip rather than an input.
// ============================================================================

import { cn } from '@/utils/cn';
import {
  registerControl,
} from '../lib/controlRegistry';
import type { ParamControlProps } from '../lib/controlRegistry';
import { ControlShell } from './ControlShell';

const StringControl = ({
  descriptor,
  override,
  onChange,
}: ParamControlProps) => {
  const value =
    override?.value !== undefined
      ? String(override.value)
      : (descriptor.currentValue as string | undefined) ?? '';

  // Read-only renders as a styled value chip — no input, no spinner.
  if (descriptor.readOnly) {
    return (
      <ControlShell descriptor={descriptor} override={undefined}>
        <div className="inline-flex items-center rounded-md border border-line-soft bg-white/[0.01] px-2 py-1 font-mono text-[11.5px] text-fg-secondary">
          {value || '—'}
        </div>
      </ControlShell>
    );
  }

  return (
    <ControlShell
      descriptor={descriptor}
      override={override}
      onRevert={override ? () => onChange(undefined) : undefined}
    >
      <input
        type="text"
        value={value}
        maxLength={
          descriptor.meta.kind === 'string'
            ? descriptor.meta.maxLength
            : undefined
        }
        onChange={(e) =>
          onChange(e.target.value === '' ? undefined : e.target.value)
        }
        className={cn(
          'w-full rounded-md border border-line-soft bg-white/[0.012] px-2 py-1.5 text-[12px] text-fg-primary placeholder:text-fg-faint focus:border-ice-400/40 focus:outline-none focus:ring-1 focus:ring-ice-400/30',
        )}
      />
    </ControlShell>
  );
};

registerControl('string', StringControl);
export { StringControl };
