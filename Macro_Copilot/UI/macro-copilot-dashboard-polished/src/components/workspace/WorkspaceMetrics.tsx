// ============================================================================
// WorkspaceMetrics
// ----------------------------------------------------------------------------
// Renders a uniform key/value grid for a current_metrics payload.  Each view
// (spread, butterfly, etc.) hands in its own list of fields with display
// formatting; this component only knows how to lay them out and apply tone.
//
// The grid responds to viewport width: 2 cols on mobile, 4 cols on tablet,
// 6 cols on desktop — keeps every cell the same width so the eye can scan
// down column-aligned numbers.
// ============================================================================

import { cn } from '@/utils/cn';

export type MetricItem = {
  label: string;
  /** Pre-formatted value string. `null`/`undefined` rendered as em-dash. */
  value: string | number | null | undefined;
  /** Optional unit suffix (e.g. "bps", "%"). */
  unit?: string;
  /** Tone for the value text — used for change/z-score colouring. */
  tone?: 'neutral' | 'positive' | 'negative' | 'warning' | 'extreme';
  /** Mark this metric as the headline (larger value). */
  emphasis?: boolean;
  /** Optional secondary line under the value (e.g. "vs prior session"). */
  subtext?: string;
};

type WorkspaceMetricsProps = {
  items: MetricItem[];
  /** Optional heading shown above the grid. */
  title?: string;
  /** Override grid columns at the desktop breakpoint. */
  desktopCols?: 4 | 5 | 6;
};

const TONE_CLASS: Record<NonNullable<MetricItem['tone']>, string> = {
  neutral: 'text-fg-primary',
  positive: 'text-mint-400',
  negative: 'text-coral-400',
  warning: 'text-amber-400',
  extreme: 'text-coral-400',
};

function formatValue(value: MetricItem['value']): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) return '—';
    return value.toFixed(2);
  }
  return value;
}

export function WorkspaceMetrics({
  items,
  title,
  desktopCols = 6,
}: WorkspaceMetricsProps) {
  const desktopColClass =
    desktopCols === 4
      ? 'lg:grid-cols-4'
      : desktopCols === 5
        ? 'lg:grid-cols-5'
        : 'lg:grid-cols-6';

  return (
    <div className="flex flex-col gap-3">
      {title ? (
        <div className="kicker">{title}</div>
      ) : null}
      <div className={cn('grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-4', desktopColClass)}>
        {items.map((item) => {
          const toneClass = TONE_CLASS[item.tone ?? 'neutral'];
          const valueSize = item.emphasis ? 'text-[20px]' : 'text-[14px]';
          return (
            <div
              key={item.label}
              className="flex min-w-0 flex-col gap-1 border-l border-line-subtle pl-3 first:border-l-0 first:pl-0 sm:border-l sm:pl-3 sm:first:border-l sm:first:pl-3"
            >
              <span className="text-[10px] uppercase tracking-[0.06em] text-fg-faint">
                {item.label}
              </span>
              <span className="flex items-baseline gap-1">
                <span className={cn('mono font-semibold tabular-nums', valueSize, toneClass)}>
                  {formatValue(item.value)}
                </span>
                {item.unit ? (
                  <span className="text-[10.5px] text-fg-muted">{item.unit}</span>
                ) : null}
              </span>
              {item.subtext ? (
                <span className="text-[10px] text-fg-faint">{item.subtext}</span>
              ) : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------------
// Helpers exposed for the view components — keeps tone / formatting decisions
// in one place so every view colours numbers identically.
// ----------------------------------------------------------------------------

/** Tone for a daily/weekly/monthly change (positive = mint, negative = coral). */
export function toneForChange(
  value: number | null | undefined,
): MetricItem['tone'] {
  if (value === null || value === undefined || !Number.isFinite(value)) return 'neutral';
  if (value > 0) return 'positive';
  if (value < 0) return 'negative';
  return 'neutral';
}

/** Tone for a z-score using the workspace's |z| thresholds. */
export function toneForZScore(
  value: number | null | undefined,
): MetricItem['tone'] {
  if (value === null || value === undefined || !Number.isFinite(value)) return 'neutral';
  const abs = Math.abs(value);
  if (abs > 2) return 'extreme';
  if (abs > 1.5) return 'warning';
  return 'neutral';
}

/** Format a signed number with explicit + for positives. */
export function formatSigned(
  value: number | null | undefined,
  decimals = 1,
): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—';
  const rounded = value.toFixed(decimals);
  // toFixed("-0.0") collapses to "0.0" only at exact zero — handle the
  // visible-zero case too so we don't show "+-0.0".
  if (Number(rounded) === 0) return rounded;
  return value > 0 ? `+${rounded}` : rounded;
}
