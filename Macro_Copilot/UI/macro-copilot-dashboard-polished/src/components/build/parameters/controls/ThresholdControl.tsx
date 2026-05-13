// ============================================================================
// ThresholdControl — numeric stepper with σ preset chips.
// ----------------------------------------------------------------------------
// Z-thresholds are the most-tweaked numeric param across desk
// workflows.  Preset chips at 1.0σ / 1.5σ / 2.0σ / 2.5σ cover the
// canonical event-study levels.  Precise input is always available.
// ============================================================================

import { cn } from '@/utils/cn';
import {
  registerControl,
} from '../lib/controlRegistry';
import type { ParamControlProps } from '../lib/controlRegistry';
import { ControlShell } from './ControlShell';

const PRESETS = [1.0, 1.5, 2.0, 2.5, 3.0];

const ThresholdControl = ({
  descriptor,
  override,
  onChange,
}: ParamControlProps) => {
  const meta =
    descriptor.meta.kind === 'threshold'
      ? descriptor.meta
      : { min: 0, max: 5, step: 0.1 };

  const effective =
    override?.value !== undefined
      ? Number(override.value)
      : (descriptor.currentValue as number | undefined) ?? meta.min;

  return (
    <ControlShell
      descriptor={descriptor}
      override={override}
      onRevert={override ? () => onChange(undefined) : undefined}
    >
      <div className="flex flex-wrap items-center gap-1">
        {PRESETS.map((p) => {
          const isActive = effective === p;
          return (
            <button
              key={p}
              type="button"
              disabled={descriptor.readOnly}
              onClick={() => onChange(p)}
              className={cn(
                'rounded-md border px-2 py-0.5 text-[11px] font-medium transition-colors',
                isActive
                  ? 'border-violet-400/45 bg-violet-500/15 text-violet-100'
                  : 'border-line-soft bg-white/[0.008] text-fg-secondary hover:border-ice-400/30 hover:bg-ice-500/[0.05]',
                descriptor.readOnly && 'cursor-not-allowed opacity-60',
              )}
            >
              {p.toFixed(1)}σ
            </button>
          );
        })}
      </div>
      <div className="mt-1.5 flex items-center gap-2">
        <input
          type="number"
          min={meta.min}
          max={meta.max}
          step={meta.step}
          disabled={descriptor.readOnly}
          value={effective}
          onChange={(e) => {
            const n = parseFloat(e.target.value);
            if (Number.isNaN(n)) {
              onChange(undefined);
            } else {
              onChange(Math.min(Math.max(n, meta.min), meta.max));
            }
          }}
          className={cn(
            'w-24 rounded-md border border-line-soft bg-white/[0.012] px-2 py-1 font-mono text-[11.5px] text-fg-primary focus:border-ice-400/40 focus:outline-none focus:ring-1 focus:ring-ice-400/30',
            descriptor.readOnly && 'cursor-not-allowed opacity-60',
          )}
        />
        <span className="font-mono text-[10.5px] text-fg-faint">σ</span>
      </div>
    </ControlShell>
  );
};

registerControl('threshold', ThresholdControl);
export { ThresholdControl };
