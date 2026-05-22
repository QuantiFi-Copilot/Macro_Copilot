// ============================================================================
// MultiTenorPicker — multi-select tenor list (PCA's `tenors[]` field)
// ----------------------------------------------------------------------------
// Renders the canonical tenor universe as a row of selectable chips.
// Empty selection means "use the curve_family's full tenor universe" —
// matches the primitive's `tenors=None` semantic.
// ============================================================================

import { CANONICAL_TENORS } from './CurveAndTenor';
import { ControlField } from './ControlPrimitives';
import { cn } from '@/utils/cn';

export function MultiTenorPicker({
  label,
  help,
  value,
  onChange,
}: {
  label: string;
  help?: string;
  value: string[];
  onChange: (next: string[]) => void;
}) {
  const toggle = (t: string) => {
    if (value.includes(t)) onChange(value.filter((x) => x !== t));
    else onChange([...value, t]);
  };

  return (
    <ControlField
      label={label}
      help={
        help ??
        'Click chips to select. Empty selection = use the full tenor universe.'
      }
      hint="multi-select"
    >
      <div className="flex flex-wrap gap-1">
        {CANONICAL_TENORS.map((t) => {
          const active = value.includes(t);
          return (
            <button
              key={t}
              type="button"
              onClick={() => toggle(t)}
              className={cn(
                'mono rounded border px-2 py-0.5 text-[10.5px] transition-colors',
                active
                  ? 'border-ice-400/40 bg-ice-500/15 text-ice-100'
                  : 'border-line-soft bg-white/[0.02] text-fg-muted hover:border-line-strong hover:text-fg-secondary',
              )}
            >
              {t}
            </button>
          );
        })}
      </div>
    </ControlField>
  );
}
