// ============================================================================
// EnumControl — generic select for kind='enum' descriptors.
// ============================================================================

import { cn } from '@/utils/cn';
import {
  registerControl,
} from '../lib/controlRegistry';
import type { ParamControlProps } from '../lib/controlRegistry';
import { ControlShell } from './ControlShell';

const EnumControl = ({
  descriptor,
  override,
  onChange,
}: ParamControlProps) => {
  const options =
    descriptor.meta.kind === 'enum' ? descriptor.meta.options : [];

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
        value={value}
        disabled={descriptor.readOnly}
        onChange={(e) =>
          onChange(e.target.value === '' ? undefined : e.target.value)
        }
        className={cn(
          'w-full rounded-md border border-line-soft bg-white/[0.012] px-2 py-1.5 text-[12px] text-fg-primary focus:border-ice-400/40 focus:outline-none focus:ring-1 focus:ring-ice-400/30',
          descriptor.readOnly && 'cursor-not-allowed opacity-70',
        )}
      >
        {value === '' && <option value="">— select —</option>}
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </ControlShell>
  );
};

registerControl('enum', EnumControl);
export { EnumControl };
