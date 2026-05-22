// ============================================================================
// WindowDaysControl — numeric stepper with statistical-window presets.
// ----------------------------------------------------------------------------
// Distinct from LookbackDays: this is the ROLLING-WINDOW length on
// statistical fits (z-score, regression, PCA).  Desk presets are
// in trading-day units rather than calendar-day units, and the
// semantic anchors are tactical/quarterly/annual/two-year.
// ============================================================================

import { cn } from '@/utils/cn';
import {
  registerControl,
} from '../lib/controlRegistry';
import type { ParamControlProps } from '../lib/controlRegistry';
import { ControlShell } from './ControlShell';

const PRESETS: Array<{ label: string; days: number; note: string }> = [
  { label: '60d', days: 60, note: 'Tactical' },
  { label: '126d', days: 126, note: 'Quarterly' },
  { label: '252d', days: 252, note: 'Annual' },
  { label: '504d', days: 504, note: 'Two-year' },
];

const WindowDaysControl = ({
  descriptor,
  override,
  onChange,
}: ParamControlProps) => {
  const meta =
    descriptor.meta.kind === 'window_days'
      ? descriptor.meta
      : { min: 20, max: 1260 };
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
              title={p.note}
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
          trading days
        </span>
      </div>
    </ControlShell>
  );
};

registerControl('window_days', WindowDaysControl);
export { WindowDaysControl };
