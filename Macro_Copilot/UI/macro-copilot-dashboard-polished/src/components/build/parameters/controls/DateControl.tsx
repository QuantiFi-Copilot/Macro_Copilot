// ============================================================================
// DateControl — native date input + relative-preset chips.
// ----------------------------------------------------------------------------
// Used for start_date / end_date / *_date params.  The native
// ``<input type="date">`` handles the calendar UI; preset chips
// jump to anchored dates (today, 30d ago, 1y ago) so the common
// case is a single click.
// ============================================================================

import { useMemo } from 'react';
import { cn } from '@/utils/cn';
import {
  registerControl,
} from '../lib/controlRegistry';
import type { ParamControlProps } from '../lib/controlRegistry';
import { ControlShell } from './ControlShell';

const DateControl = ({
  descriptor,
  override,
  onChange,
}: ParamControlProps) => {
  const isEnd = descriptor.path[descriptor.path.length - 1]
    .toLowerCase()
    .startsWith('end');

  const effective =
    override?.value !== undefined
      ? String(override.value)
      : (descriptor.currentValue as string | undefined) ?? '';

  const presets = useMemo(() => buildPresets(isEnd), [isEnd]);

  return (
    <ControlShell
      descriptor={descriptor}
      override={override}
      onRevert={override ? () => onChange(undefined) : undefined}
    >
      <div className="flex flex-wrap items-center gap-1">
        {presets.map((p) => {
          const isActive = effective === p.iso;
          return (
            <button
              key={p.label}
              type="button"
              disabled={descriptor.readOnly}
              onClick={() => onChange(p.iso)}
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
      <div className="mt-1.5">
        <input
          type="date"
          value={effective}
          disabled={descriptor.readOnly}
          onChange={(e) =>
            onChange(e.target.value === '' ? undefined : e.target.value)
          }
          className={cn(
            'w-40 rounded-md border border-line-soft bg-white/[0.012] px-2 py-1 font-mono text-[11.5px] text-fg-primary focus:border-ice-400/40 focus:outline-none focus:ring-1 focus:ring-ice-400/30',
            descriptor.readOnly && 'cursor-not-allowed opacity-60',
          )}
        />
      </div>
    </ControlShell>
  );
};

function buildPresets(isEnd: boolean): Array<{ label: string; iso: string }> {
  const today = new Date();
  const fmt = (d: Date) => d.toISOString().slice(0, 10);
  if (isEnd) {
    return [{ label: 'today', iso: fmt(today) }];
  }
  return [
    { label: '−30d', iso: fmt(daysAgo(today, 30)) },
    { label: '−1y', iso: fmt(daysAgo(today, 365)) },
    { label: '−2y', iso: fmt(daysAgo(today, 730)) },
    { label: '−5y', iso: fmt(daysAgo(today, 1825)) },
  ];
}

function daysAgo(anchor: Date, days: number): Date {
  const d = new Date(anchor);
  d.setDate(d.getDate() - days);
  return d;
}

registerControl('date', DateControl);
export { DateControl };
