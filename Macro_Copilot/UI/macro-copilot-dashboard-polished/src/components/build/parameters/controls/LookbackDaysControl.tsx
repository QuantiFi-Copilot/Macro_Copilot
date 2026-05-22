// ============================================================================
// LookbackDaysControl — numeric stepper with semantic preset chips.
// ----------------------------------------------------------------------------
// "Lookback days" is the displayed-history length on most primitives.
// Users think in years, not days — so we ship 1y/2y/3y/5y/10y preset
// chips alongside a precise number input.  Clicking a preset writes
// the matching integer; the number input is always synchronized.
// ============================================================================

import { cn } from '@/utils/cn';
import {
  registerControl,
} from '../lib/controlRegistry';
import type { ParamControlProps } from '../lib/controlRegistry';
import { ControlShell } from './ControlShell';

const PRESETS: Array<{ label: string; days: number }> = [
  { label: '1y', days: 365 },
  { label: '2y', days: 730 },
  { label: '3y', days: 1095 },
  { label: '5y', days: 1825 },
  { label: '10y', days: 3650 },
];

const LookbackDaysControl = ({
  descriptor,
  override,
  onChange,
}: ParamControlProps) => {
  const meta =
    descriptor.meta.kind === 'lookback_days'
      ? descriptor.meta
      : { min: 30, max: 7300 };
  const effective =
    override?.value !== undefined
      ? Number(override.value)
      : Number(descriptor.currentValue ?? meta.min);

  return (
    <ControlShell
      descriptor={descriptor}
      override={override}
      onRevert={override ? () => onChange(undefined) : undefined}
    >
      <div className="flex flex-wrap items-center gap-1">
        {PRESETS.map((p) => {
          const isActive = effective === p.days;
          return (
            <button
              key={p.label}
              type="button"
              disabled={descriptor.readOnly}
              onClick={() => onChange(p.days)}
              className={cn(
                'rounded-md border px-2 py-0.5 text-[11px] font-medium transition-colors',
                isActive
                  ? 'border-violet-400/45 bg-violet-500/15 text-violet-100'
                  : 'border-line-soft bg-white/[0.008] text-fg-secondary hover:border-ice-400/30 hover:bg-ice-500/[0.05]',
                descriptor.readOnly && 'cursor-not-allowed opacity-60',
              )}
            >
              {p.label}
            </button>
          );
        })}
      </div>
      <div className="mt-1.5 flex items-center gap-2">
        <input
          type="number"
          min={meta.min}
          max={meta.max}
          step={1}
          disabled={descriptor.readOnly}
          value={effective}
          onChange={(e) => {
            const n = parseInt(e.target.value, 10);
            if (Number.isNaN(n)) {
              onChange(undefined);
            } else {
              onChange(Math.min(Math.max(n, meta.min), meta.max));
            }
          }}
          className={cn(
            'w-24 rounded-md border border-line-soft bg-white/[0.012] px-2 py-1 text-[11.5px] font-mono text-fg-primary focus:border-ice-400/40 focus:outline-none focus:ring-1 focus:ring-ice-400/30',
            descriptor.readOnly && 'cursor-not-allowed opacity-60',
          )}
        />
        <span className="font-mono text-[10.5px] text-fg-faint">
          calendar days
        </span>
      </div>
    </ControlShell>
  );
};

registerControl('lookback_days', LookbackDaysControl);
export { LookbackDaysControl };
