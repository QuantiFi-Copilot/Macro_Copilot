// ============================================================================
// ArtifactStatsBlock — first / last / row-count summary strip.
// ----------------------------------------------------------------------------
// Reusable beneath any sparkline-based renderer.  Pulls first / last
// preview values + the row count off the artifact summary and lays
// them out as a compact stats strip.
//
// Visual register matches the existing ``MonitorPage`` widget bodies
// (kicker labels at 10px uppercase 0.16em tracked; values at 13.5px
// mono).  We render numbers via a thin formatter that picks units
// from the artifact metadata when present.
// ============================================================================

import { useMemo } from 'react';
import type { ArtifactSummary } from '@/services/workspaceApi';
import { cn } from '@/utils/cn';

type Props = {
  artifact: ArtifactSummary;
  /** Compact mode trims to first / last only (no row count).  Used
   *  inside small-size widgets where the third row would force a
   *  scroll. */
  compact?: boolean;
};

export function ArtifactStatsBlock({ artifact, compact = false }: Props) {
  const cells = useMemo(() => {
    const out: { kicker: string; value: string; isMono?: boolean }[] = [];
    const values = artifact.preview_values ?? [];
    const idx = artifact.preview_index ?? [];

    const firstFiniteIdx = values.findIndex(
      (v) => v != null && !Number.isNaN(v),
    );
    const lastFiniteIdx = lastIndexOfFinite(values);

    if (firstFiniteIdx >= 0) {
      out.push({
        kicker: 'First',
        value: formatValue(values[firstFiniteIdx], artifact.units),
        isMono: true,
      });
      out.push({
        kicker: 'First date',
        value: idx[firstFiniteIdx] ?? '—',
        isMono: true,
      });
    }
    if (lastFiniteIdx >= 0 && lastFiniteIdx !== firstFiniteIdx) {
      out.push({
        kicker: 'Last',
        value: formatValue(values[lastFiniteIdx], artifact.units),
        isMono: true,
      });
      out.push({
        kicker: 'Last date',
        value: idx[lastFiniteIdx] ?? '—',
        isMono: true,
      });
    }
    if (!compact && artifact.row_count != null) {
      out.push({
        kicker: 'Rows',
        value: String(artifact.row_count),
        isMono: true,
      });
    }
    return out;
  }, [artifact, compact]);

  if (cells.length === 0) return null;

  return (
    <div
      className={cn(
        'grid gap-x-4 gap-y-2 px-5 pt-3 pb-3',
        cells.length <= 2 ? 'grid-cols-2' : 'grid-cols-2 sm:grid-cols-4',
      )}
    >
      {cells.map((cell) => (
        <div key={cell.kicker} className="flex min-w-0 flex-col gap-0.5">
          <span className="text-[9.5px] font-medium uppercase tracking-[0.16em] text-fg-faint">
            {cell.kicker}
          </span>
          <span
            className={cn(
              'truncate text-[12.5px] font-medium tracking-[-0.005em] text-fg-primary',
              cell.isMono && 'font-mono text-[12px] text-fg-secondary',
            )}
          >
            {cell.value}
          </span>
        </div>
      ))}
    </div>
  );
}

// ----------------------------------------------------------------------------

function lastIndexOfFinite(values: Array<number | null>): number {
  for (let i = values.length - 1; i >= 0; i--) {
    const v = values[i];
    if (v != null && !Number.isNaN(v)) return i;
  }
  return -1;
}

function formatValue(value: number | null, units: string | null): string {
  if (value == null) return '—';
  const u = (units ?? '').toLowerCase();
  // BPS and z-score values stay in their natural unit; percent
  // values render with 3 decimal places to match Monitor.
  if (u === 'bps') {
    return `${value.toFixed(2)} bps`;
  }
  if (u === 'percent' || u === 'pct') {
    return `${value.toFixed(3)}%`;
  }
  if (u === 'z_score' || u === 'zscore') {
    return value.toFixed(2);
  }
  if (u === 'ratio') {
    return value.toFixed(3);
  }
  if (u && u !== 'count') {
    return `${formatNumber(value)} ${units}`;
  }
  return formatNumber(value);
}

function formatNumber(value: number): string {
  if (Math.abs(value) >= 1e6) return value.toExponential(3);
  if (Number.isInteger(value)) return String(value);
  return value.toPrecision(5);
}
