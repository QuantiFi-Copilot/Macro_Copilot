// ============================================================================
// WorkspaceHeader
// ----------------------------------------------------------------------------
// Top bar of the workspace surface: title + subtitle on the left, parameter
// pickers + a lookback selector + refresh on the right.  Each picker writes
// its change back through `onParamChange` which the WorkspacePage routes
// into URL search params (so the state remains shareable).
//
// The header deliberately does not assume a particular view — callers pass
// in the parameter list they want exposed.  Yield view shows curve/tenor;
// spread shows curve/short_tenor/long_tenor; etc.
// ============================================================================

import { ChevronDown, RefreshCw } from 'lucide-react';
import { cn } from '@/utils/cn';

export type ParamOption = {
  value: string;
  label?: string;
};

export type ParamSpec = {
  /** URL param key — e.g. "curve_family", "short_tenor". */
  key: string;
  /** Visible label above the dropdown. */
  label: string;
  /** Available choices.  If empty, a free-text input would be shown — but
   *  V1 keeps things constrained to known curve families / tenors. */
  options: ParamOption[];
  /** Current value (from URL).  Empty string treated as "not chosen". */
  value: string;
};

type WorkspaceHeaderProps = {
  title: string;
  subtitle?: string;
  asOfDate?: string | null;
  /** Parameter dropdowns to render. */
  params: ParamSpec[];
  /** Lookback days picker — optional (scanner / regime hide it). */
  lookbackDays?: number | undefined;
  onLookbackChange?: (days: number) => void;
  onParamChange: (key: string, value: string) => void;
  onRefresh?: () => void;
  isLoading?: boolean;
};

const LOOKBACK_OPTIONS: { value: number; label: string }[] = [
  { value: 22, label: '1M' },
  { value: 63, label: '3M' },
  { value: 126, label: '6M' },
  { value: 252, label: '1Y' },
  { value: 504, label: '2Y' },
];

function formatAsOf(d: string | null | undefined): string | null {
  if (!d) return null;
  const dt = new Date(d);
  if (Number.isNaN(dt.getTime())) return d;
  return dt.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  });
}

function ParamPicker({
  spec,
  onChange,
}: {
  spec: ParamSpec;
  onChange: (value: string) => void;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[9.5px] uppercase tracking-[0.07em] text-fg-faint">
        {spec.label}
      </span>
      <div className="relative">
        <select
          value={spec.value}
          onChange={(e) => onChange(e.target.value)}
          className={cn(
            'mono cursor-pointer appearance-none rounded-md border border-line-soft bg-white/[0.02] py-1.5 pl-2.5 pr-7 text-[11.5px] text-fg-primary',
            'transition-colors hover:border-ice-400/40 focus:border-ice-400/60 focus:outline-none',
          )}
        >
          {spec.options.map((opt) => (
            <option key={opt.value} value={opt.value} className="bg-surface text-fg-primary">
              {opt.label ?? opt.value}
            </option>
          ))}
        </select>
        <ChevronDown
          size={11}
          className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-fg-faint"
        />
      </div>
    </label>
  );
}

function LookbackPicker({
  value,
  onChange,
}: {
  value: number | undefined;
  onChange: (days: number) => void;
}) {
  // Match the picker UX to the param dropdowns for visual consistency.
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[9.5px] uppercase tracking-[0.07em] text-fg-faint">
        Lookback
      </span>
      <div className="flex items-center gap-1 rounded-md border border-line-soft bg-white/[0.02] p-0.5">
        {LOOKBACK_OPTIONS.map((opt) => {
          const active = value === opt.value;
          return (
            <button
              key={opt.value}
              type="button"
              onClick={() => onChange(opt.value)}
              className={cn(
                'mono rounded px-2 py-0.5 text-[10.5px] transition-colors',
                active
                  ? 'bg-ice-500/15 text-ice-200'
                  : 'text-fg-muted hover:text-fg-primary',
              )}
            >
              {opt.label}
            </button>
          );
        })}
      </div>
    </label>
  );
}

export function WorkspaceHeader({
  title,
  subtitle,
  asOfDate,
  params,
  lookbackDays,
  onLookbackChange,
  onParamChange,
  onRefresh,
  isLoading,
}: WorkspaceHeaderProps) {
  const asOfFormatted = formatAsOf(asOfDate);

  return (
    <header className="flex flex-col gap-4 border-b border-line-subtle px-6 py-4 lg:flex-row lg:items-end lg:justify-between">
      {/* Left: title + subtitle + as-of */}
      <div className="flex min-w-0 flex-col gap-1">
        <div className="flex items-baseline gap-2.5">
          <h1 className="text-[17px] font-semibold tracking-[-0.01em] text-fg-primary">
            {title}
          </h1>
          {asOfFormatted ? (
            <span className="mono text-[10.5px] text-fg-faint">
              as of {asOfFormatted}
            </span>
          ) : null}
        </div>
        {subtitle ? (
          <p className="text-[12px] text-fg-secondary">{subtitle}</p>
        ) : null}
      </div>

      {/* Right: param pickers + lookback + refresh */}
      <div className="flex flex-wrap items-end gap-3">
        {params.map((spec) => (
          <ParamPicker
            key={spec.key}
            spec={spec}
            onChange={(value) => onParamChange(spec.key, value)}
          />
        ))}
        {onLookbackChange ? (
          <LookbackPicker value={lookbackDays} onChange={onLookbackChange} />
        ) : null}
        {onRefresh ? (
          <button
            type="button"
            onClick={onRefresh}
            disabled={isLoading}
            className={cn(
              'flex h-[28px] items-center gap-1.5 self-end rounded-md border border-line-soft bg-white/[0.02] px-2.5 text-[11px] text-fg-muted transition-colors',
              'hover:border-ice-400/40 hover:text-fg-primary disabled:opacity-50',
            )}
            title="Refetch with current parameters"
          >
            <RefreshCw size={11} className={isLoading ? 'animate-spin' : undefined} />
            Refresh
          </button>
        ) : null}
      </div>
    </header>
  );
}
