// ============================================================================
// src/components/shared/build/model/DecompositionBars.tsx — decomposition card.
// ----------------------------------------------------------------------------
// Consolidation target #4: ONE rendering for every model's "how does the
// whole split into parts" read.  Two modes cover the catalogue:
//
//   mode="share"  — unsigned fractions of a whole (PCA variance
//                   explained): tone-cycled bars + per-row Σ cumulative.
//   mode="signed" — signed quantities around a center axis (attribution
//                   contributions in bps): mint positive / coral
//                   negative, residual row de-emphasised.
//
// Finance-blind: the module maps its Output onto DecompositionEntry[];
// this component never knows what a "variance share" is (FP13, P3).
// ============================================================================

import { chartStrokeForTone } from '@/lib/chart';
import { cn } from '@/utils/cn';
import { QualityBadge, type QualityBadgeProps } from './QualityBadge';
import { modelToneAt, type DecompositionEntry } from './types';

export interface DecompositionBarsProps {
  /** Card kicker (e.g. "Variance explained", "Contribution by factor"). */
  title: string;
  /** One-line explanation under the kicker (P5). */
  description?: string;
  entries: DecompositionEntry[];
  mode: 'share' | 'signed';
  /** Display unit for "signed" mode values (e.g. "bps"). */
  unit?: string;
  /** Decimals for the value column (share mode renders percents at 1). */
  decimals?: number;
}

export function DecompositionBars({
  title,
  description,
  entries,
  mode,
  unit,
  decimals,
}: DecompositionBarsProps) {
  if (entries.length === 0) return null;
  const valueDecimals = decimals ?? (mode === 'share' ? 1 : 1);

  // Signed mode scales bars by the absolute max entry.
  const absMax = Math.max(
    ...entries.map((e) =>
      typeof e.value === 'number' && Number.isFinite(e.value)
        ? Math.abs(e.value)
        : 0,
    ),
    0,
  );
  const denom = absMax === 0 ? 1 : absMax;

  return (
    <section className="card px-5 py-4">
      <div className="kicker mb-1">{title}</div>
      {description && (
        <p className="mb-2.5 text-[10.5px] text-fg-muted">{description}</p>
      )}
      <div className="space-y-2">
        {entries.map((e, i) => {
          const finite =
            typeof e.value === 'number' && Number.isFinite(e.value);
          const v = finite ? (e.value as number) : 0;

          if (mode === 'share') {
            const tone = e.tone ?? modelToneAt(i);
            const stroke = chartStrokeForTone(tone);
            const pctShare = v * 100;
            const pctCum =
              typeof e.cumulative === 'number' && Number.isFinite(e.cumulative)
                ? e.cumulative * 100
                : null;
            return (
              <div key={e.label} className="flex items-center gap-3">
                <LabelCell entry={e} />
                <div className="relative flex-1 overflow-hidden rounded-md border border-line-subtle bg-white/[0.012]">
                  <div
                    className="h-3"
                    style={{
                      width: `${Math.max(0, Math.min(100, pctShare))}%`,
                      backgroundColor: stroke + '88',
                    }}
                  />
                </div>
                <span className="mono w-14 shrink-0 text-right text-[10.5px] text-fg-primary">
                  {finite ? `${pctShare.toFixed(valueDecimals)}%` : '—'}
                </span>
                <span className="mono w-14 shrink-0 text-right text-[10.5px] text-fg-muted">
                  {pctCum != null ? `Σ ${pctCum.toFixed(valueDecimals)}%` : ''}
                </span>
              </div>
            );
          }

          // mode === 'signed' — center-axis bars, mint/coral by sign.
          const pct = (Math.abs(v) / denom) * 100;
          const positive = v >= 0;
          return (
            <div key={e.label} className="flex items-center gap-3">
              <LabelCell entry={e} />
              <div className="relative flex h-4 flex-1 items-center">
                <div className="absolute inset-y-0 left-1/2 w-px bg-line-subtle" />
                <div
                  className={cn(
                    'absolute h-2.5 rounded-[3px]',
                    e.isResidual
                      ? positive
                        ? 'left-1/2 bg-fg-faint/25 ring-1 ring-fg-faint/20'
                        : 'right-1/2 bg-fg-faint/25 ring-1 ring-fg-faint/20'
                      : positive
                        ? 'left-1/2 bg-mint-400/55 ring-1 ring-mint-400/30'
                        : 'right-1/2 bg-coral-400/55 ring-1 ring-coral-400/30',
                  )}
                  style={{ width: `${pct / 2}%` }}
                />
              </div>
              <span
                className={cn(
                  'mono w-20 shrink-0 text-right text-[10.5px] tabular-nums',
                  !finite
                    ? 'text-fg-faint'
                    : e.isResidual
                      ? 'text-fg-muted'
                      : v > 0
                        ? 'text-mint-300'
                        : v < 0
                          ? 'text-coral-300'
                          : 'text-fg-muted',
                )}
              >
                {finite
                  ? `${v >= 0 ? '+' : ''}${v.toFixed(valueDecimals)}${unit ? ` ${unit}` : ''}`
                  : '—'}
              </span>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function LabelCell({ entry }: { entry: DecompositionEntry }) {
  return (
    <span
      className={cn(
        'mono flex w-28 shrink-0 items-center gap-1.5 text-[10.5px]',
        entry.isResidual ? 'text-fg-muted' : 'text-fg-secondary',
      )}
    >
      <span className="truncate">{entry.label}</span>
      {entry.quality && (
        <QualityBadge
          level={entry.quality}
          label={entry.quality}
          note={entry.qualityNote}
        />
      )}
    </span>
  );
}
