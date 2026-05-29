// ============================================================================
// shared/build/elements/ZScoreRegimeSlider.tsx
// ----------------------------------------------------------------------------
// Static z-score regime bar showing Normal / Elevated / Extreme zones
// with a marker at the current z-score's position.  Finance-blind —
// thresholds are passed in (defaults to the shared constants in ../lib/tone).
// ============================================================================

import { Z_ELEVATED_THRESHOLD, Z_EXTREME_THRESHOLD } from '../lib/tone';

type Props = {
  /** Current z-score (signed). */
  value: number | null | undefined;
  /** Override thresholds — defaults to the shared (1.5, 2.0). */
  amberAt?: number;
  coralAt?: number;
  /** Optional clamp on the slider's domain (defaults to ±3σ). */
  domainMax?: number;
};

export function ZScoreRegimeSlider({
  value,
  amberAt = Z_ELEVATED_THRESHOLD,
  coralAt = Z_EXTREME_THRESHOLD,
  domainMax = 3,
}: Props) {
  const clamped =
    value == null || Number.isNaN(value)
      ? null
      : Math.max(-domainMax, Math.min(domainMax, value));

  const positionPct =
    clamped == null
      ? null
      : ((clamped + domainMax) / (2 * domainMax)) * 100;

  // Compute zone widths in percent: extreme-neg | elevated-neg | normal | elevated-pos | extreme-pos
  const extremePct = ((domainMax - coralAt) / (2 * domainMax)) * 100;
  const elevatedPct = ((coralAt - amberAt) / (2 * domainMax)) * 100;
  const normalPct = ((2 * amberAt) / (2 * domainMax)) * 100;

  return (
    <div className="flex flex-col gap-1.5">
      <div className="relative h-2 w-full overflow-hidden rounded-full bg-line-subtle">
        <div className="flex h-full">
          <div
            className="h-full bg-coral-300/30"
            style={{ width: `${extremePct}%` }}
            aria-hidden
          />
          <div
            className="h-full bg-amber-300/30"
            style={{ width: `${elevatedPct}%` }}
            aria-hidden
          />
          <div
            className="h-full bg-fg-faint/15"
            style={{ width: `${normalPct}%` }}
            aria-hidden
          />
          <div
            className="h-full bg-amber-300/30"
            style={{ width: `${elevatedPct}%` }}
            aria-hidden
          />
          <div
            className="h-full bg-coral-300/30"
            style={{ width: `${extremePct}%` }}
            aria-hidden
          />
        </div>
        {positionPct != null && (
          <div
            className="absolute top-1/2 h-3 w-1 -translate-x-1/2 -translate-y-1/2 rounded-full bg-fg-primary shadow-overlay"
            style={{ left: `${positionPct}%` }}
            aria-label={`z=${(clamped ?? 0).toFixed(2)}`}
          />
        )}
      </div>
      <div className="flex justify-between text-[10px] uppercase tracking-[0.05em] text-fg-faint">
        <span>Normal</span>
        <span>Elevated</span>
        <span>Extreme</span>
      </div>
    </div>
  );
}
