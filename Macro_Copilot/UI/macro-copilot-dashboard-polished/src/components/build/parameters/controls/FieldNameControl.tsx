// ============================================================================
// FieldNameControl — dropdown for Bloomberg field name.
// ----------------------------------------------------------------------------
// Substrate primitives accept a Bloomberg field override (e.g. switch
// from YLD_YTM_MID to PX_MID) without a config edit.  The dropdown
// here lists the known fields + always offers a "default
// (config.yaml)" option that clears the override and falls through to
// the primitive's bundled YAML default.
// ============================================================================

import { useId } from 'react';
import { cn } from '@/utils/cn';
import {
  registerControl,
} from '../lib/controlRegistry';
import type { ParamControlProps } from '../lib/controlRegistry';
import { ControlShell } from './ControlShell';

const FieldNameControl = ({
  descriptor,
  override,
  onChange,
}: ParamControlProps) => {
  const id = useId();
  const allowed =
    descriptor.meta.kind === 'field_name'
      ? descriptor.meta.allowedFields
      : ['YLD_YTM_MID'];
  const value =
    override?.value !== undefined
      ? String(override.value)
      : (descriptor.currentValue as string | undefined) ?? '';

  return (
    <ControlShell
      descriptor={descriptor}
      override={override}
      onRevert={override ? () => onChange(undefined) : undefined}
    >
      <select
        id={id}
        value={value}
        disabled={descriptor.readOnly}
        onChange={(e) =>
          onChange(e.target.value === '' ? undefined : e.target.value)
        }
        className={cn(
          'w-full rounded-md border border-line-soft bg-white/[0.012] px-2 py-1.5 font-mono text-[12px] text-fg-primary focus:border-ice-400/40 focus:outline-none focus:ring-1 focus:ring-ice-400/30',
          descriptor.readOnly && 'cursor-not-allowed opacity-70',
        )}
      >
        <option value="">default (config.yaml)</option>
        {allowed.map((f) => (
          <option key={f} value={f}>
            {f}
          </option>
        ))}
      </select>
    </ControlShell>
  );
};

registerControl('field_name', FieldNameControl);
export { FieldNameControl };
