// ============================================================================
// TenorControl — chip group for tenor selection.
// ============================================================================
// Mockup B's parameter row shows tenors as horizontal pill chips
// ("2Y · 5Y · 10Y · 30Y").  Far more scannable than a freeform input.
// We render the common tenors as chips + provide a small text input
// for the rare custom case (e.g. "9M", "12M2Y" forward windows).
//
// Selection states:
//   - chip matches current value     → solid violet background
//   - chip matches override          → solid violet background +
//                                      ring-1 ring-violet-400
//   - chip neither                   → bordered, hover-only background
// ============================================================================

import { useState } from 'react';
import { cn } from '@/utils/cn';
import {
  registerControl,
} from '../lib/controlRegistry';
import type { ParamControlProps } from '../lib/controlRegistry';
import { ControlShell } from './ControlShell';

const TenorControl = ({
  descriptor,
  override,
  onChange,
}: ParamControlProps) => {
  const commonTenors =
    descriptor.meta.kind === 'tenor'
      ? descriptor.meta.commonTenors
      : ['2Y', '5Y', '10Y', '30Y'];

  const effectiveValue =
    override?.value !== undefined
      ? String(override.value)
      : (descriptor.currentValue as string | undefined) ?? '';

  const isCustom = effectiveValue && !commonTenors.includes(effectiveValue);
  const [showCustomInput, setShowCustomInput] = useState<boolean>(
    Boolean(isCustom),
  );

  return (
    <ControlShell
      descriptor={descriptor}
      override={override}
      onRevert={override ? () => onChange(undefined) : undefined}
    >
      <div className="flex flex-wrap items-center gap-1">
        {commonTenors.map((t) => {
          const isActive = effectiveValue === t;
          return (
            <button
              key={t}
              type="button"
              disabled={descriptor.readOnly}
              onClick={() => {
                setShowCustomInput(false);
                onChange(t);
              }}
              className={cn(
                'rounded-md border px-2 py-0.5 text-[11px] font-medium transition-colors',
                isActive
                  ? 'border-violet-400/45 bg-violet-500/15 text-violet-100'
                  : 'border-line-soft bg-white/[0.008] text-fg-secondary hover:border-ice-400/30 hover:bg-ice-500/[0.05]',
                descriptor.readOnly && 'cursor-not-allowed opacity-60',
              )}
            >
              {t}
            </button>
          );
        })}
        <button
          type="button"
          disabled={descriptor.readOnly}
          onClick={() => setShowCustomInput((v) => !v)}
          className={cn(
            'rounded-md border border-dashed px-2 py-0.5 text-[11px] font-medium transition-colors',
            isCustom || showCustomInput
              ? 'border-ice-400/45 text-ice-200'
              : 'border-line-soft text-fg-faint hover:border-ice-400/30 hover:text-fg-secondary',
            descriptor.readOnly && 'cursor-not-allowed opacity-60',
          )}
        >
          {isCustom ? `Custom · ${effectiveValue}` : 'Custom…'}
        </button>
      </div>

      {showCustomInput && !descriptor.readOnly && (
        <div className="mt-1.5">
          <input
            type="text"
            value={isCustom ? effectiveValue : ''}
            placeholder="e.g. 9M, 1Y1Y"
            onChange={(e) =>
              onChange(e.target.value.trim() === '' ? undefined : e.target.value.trim())
            }
            className="w-32 rounded-md border border-line-soft bg-white/[0.012] px-2 py-1 text-[11.5px] text-fg-primary placeholder:text-fg-faint focus:border-ice-400/40 focus:outline-none focus:ring-1 focus:ring-ice-400/30"
          />
        </div>
      )}
    </ControlShell>
  );
};

registerControl('tenor', TenorControl);
export { TenorControl };
