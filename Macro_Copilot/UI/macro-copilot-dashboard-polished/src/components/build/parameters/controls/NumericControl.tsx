// ============================================================================
// NumericControl — generic numeric stepper (fallback for typed numeric params).
// ----------------------------------------------------------------------------
// Used by ``deriveControlsForStage`` when a leaf-name doesn't match
// any of the specialised numeric controls (lookback / window /
// threshold).  Honors integer vs float per descriptor metadata.
// ============================================================================

import { cn } from '@/utils/cn';
import {
  registerControl,
} from '../lib/controlRegistry';
import type { ParamControlProps } from '../lib/controlRegistry';
import { ControlShell } from './ControlShell';

const NumericControl = ({
  descriptor,
  override,
  onChange,
}: ParamControlProps) => {
  // The deriver always emits ``kind: 'numeric'`` for this control,
  // but the registry resolves by kind so a malformed descriptor
  // could land here.  Type-narrow defensively + supply a discriminated
  // default that preserves the union shape.
  const meta: {
    kind: 'numeric';
    min?: number;
    max?: number;
    step?: number;
    integer?: boolean;
  } =
    descriptor.meta.kind === 'numeric'
      ? descriptor.meta
      : { kind: 'numeric', integer: false };

  const effective =
    override?.value !== undefined
      ? Number(override.value)
      : Number(descriptor.currentValue ?? 0);

  return (
    <ControlShell
      descriptor={descriptor}
      override={override}
      onRevert={override ? () => onChange(undefined) : undefined}
    >
      <input
        type="number"
        min={meta.min}
        max={meta.max}
        step={meta.step ?? (meta.integer ? 1 : 0.01)}
        disabled={descriptor.readOnly}
        value={effective}
        onChange={(e) => {
          const raw = meta.integer
            ? parseInt(e.target.value, 10)
            : parseFloat(e.target.value);
          if (Number.isNaN(raw)) {
            onChange(undefined);
          } else {
            const clamped =
              typeof meta.min === 'number' && raw < meta.min
                ? meta.min
                : typeof meta.max === 'number' && raw > meta.max
                  ? meta.max
                  : raw;
            onChange(clamped);
          }
        }}
        className={cn(
          'w-32 rounded-md border border-line-soft bg-white/[0.012] px-2 py-1 font-mono text-[11.5px] text-fg-primary focus:border-ice-400/40 focus:outline-none focus:ring-1 focus:ring-ice-400/30',
          descriptor.readOnly && 'cursor-not-allowed opacity-60',
        )}
      />
    </ControlShell>
  );
};

registerControl('numeric', NumericControl);
export { NumericControl };
