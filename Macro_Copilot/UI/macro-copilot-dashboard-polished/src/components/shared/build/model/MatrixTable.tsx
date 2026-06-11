// ============================================================================
// src/components/shared/build/model/MatrixTable.tsx — model matrix card.
// ----------------------------------------------------------------------------
// Consolidation target #4: ONE rendering for every model's
// rows × columns matrix (PCA loadings: tenors × components; regression
// summaries: regressors × statistics) with the SHARED diverging
// shading — mint positive / coral negative, bar length ∝ |value| /
// max|value| — exactly the visual the PCA pilot established, now
// finance-blind and reusable (FP13, P3).
//
// The module maps its Output onto ``MatrixRow[]`` + column keys; this
// component never knows what a "loading" is.
// ============================================================================

import { cn } from '@/utils/cn';
import { QualityBadge, type QualityBadgeProps } from './QualityBadge';
import type { MatrixRow } from './types';

export interface MatrixTableProps {
  /** Card kicker (e.g. "Loadings", "Per-regressor fit"). */
  title: string;
  /** One-line explanation under the kicker (P5: say what the shading
   *  means; the default covers the diverging read). */
  description?: string;
  /** Header label of the leftmost identity column (e.g. "Tenor"). */
  rowKeyLabel: string;
  /** Column keys, in render order. */
  columns: string[];
  /** Optional per-column display label; defaults to the key. */
  columnLabels?: Record<string, string>;
  /** Optional per-column quality chip rendered in the header. */
  columnBadges?: Record<string, QualityBadgeProps>;
  rows: MatrixRow[];
  /** Fixed decimals for cell values (PCA loadings read at 3). */
  decimals?: number;
}

export function MatrixTable({
  title,
  description = 'Bar shading shows magnitude; mint = positive, coral = negative.',
  rowKeyLabel,
  columns,
  columnLabels,
  columnBadges,
  rows,
  decimals = 3,
}: MatrixTableProps) {
  // Absolute max across the matrix scales every cell's bar.
  let absMax = 0;
  for (const row of rows) {
    for (const c of columns) {
      const v = row.cells[c];
      if (typeof v === 'number' && Number.isFinite(v)) {
        absMax = Math.max(absMax, Math.abs(v));
      }
    }
  }
  const denom = absMax === 0 ? 1 : absMax;

  return (
    <section className="card overflow-hidden">
      <div className="flex items-baseline justify-between border-b border-line-subtle px-5 py-3">
        <div>
          <div className="kicker">{title}</div>
          {description && (
            <p className="mt-0.5 text-[10.5px] text-fg-muted">{description}</p>
          )}
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="mono w-full min-w-[420px] text-[11px] tabular-nums">
          <thead>
            <tr className="border-b border-line-subtle text-[10px] uppercase tracking-[0.06em] text-fg-faint">
              <th className="px-4 py-2 text-left font-normal">{rowKeyLabel}</th>
              {columns.map((c) => (
                <th key={c} className="px-4 py-2 text-right font-normal">
                  <span className="inline-flex items-center gap-1.5">
                    {columnLabels?.[c] ?? c}
                    {columnBadges?.[c] && <QualityBadge {...columnBadges[c]} />}
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr
                key={row.key}
                className="border-b border-line-subtle/40 transition-colors hover:bg-white/[0.012]"
              >
                <td className="px-4 py-2 text-fg-primary">{row.key}</td>
                {columns.map((c) => {
                  const v = row.cells[c];
                  const finite = typeof v === 'number' && Number.isFinite(v);
                  const num = finite ? (v as number) : 0;
                  const ratio = (num / denom) * 100;
                  return (
                    <td key={c} className="relative px-4 py-2 text-right">
                      <div
                        aria-hidden
                        className={cn(
                          'absolute top-1/2 h-[60%] -translate-y-1/2 rounded-[2px]',
                          num >= 0
                            ? 'left-1/2 bg-mint-400/15'
                            : 'right-1/2 bg-coral-400/15',
                        )}
                        style={{ width: `${Math.abs(ratio) / 2}%` }}
                      />
                      <span
                        className={cn(
                          'relative',
                          finite ? 'text-fg-primary' : 'text-fg-faint',
                        )}
                      >
                        {finite ? (v as number).toFixed(decimals) : '—'}
                      </span>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
