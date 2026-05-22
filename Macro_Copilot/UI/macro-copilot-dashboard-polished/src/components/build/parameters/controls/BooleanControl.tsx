// ============================================================================
// BooleanControl — pill-style toggle switch.
// ============================================================================

import { cn } from '@/utils/cn';
import {
  registerControl,
} from '../lib/controlRegistry';
import type { ParamControlProps } from '../lib/controlRegistry';
import { ControlShell } from './ControlShell';

const BooleanControl = ({
  descriptor,
  override,
  onChange,
}: ParamControlProps) => {
  const effective =
    override?.value !== undefined
      ? Boolean(override.value)
      : Boolean(descriptor.currentValue);

  return (
    <ControlShell
      descriptor={descriptor}
      override={override}
      onRevert={override ? () => onChange(undefined) : undefined}
    >
      <button
        type="button"
        role="switch"
        aria-checked={effective}
        disabled={descriptor.readOnly}
        onClick={() => onChange(!effective)}
        className={cn(
          'relative inline-flex h-5 w-9 items-center rounded-full border transition-colors',
          effective
            ? 'border-violet-400/45 bg-violet-500/30'
            : 'border-line-soft bg-white/[0.02]',
          descriptor.readOnly && 'cursor-not-allowed opacity-60',
        )}
      >
        <span
          aria-hidden
          className={cn(
            'inline-block h-3.5 w-3.5 rounded-full bg-white shadow-sm transition-transform',
            effective ? 'translate-x-[18px]' : 'translate-x-[2px]',
          )}
        />
      </button>
    </ControlShell>
  );
};

registerControl('boolean', BooleanControl);
export { BooleanControl };
