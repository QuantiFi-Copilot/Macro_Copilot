// ============================================================================
// shared/build/extended/StretchContextCard.tsx
// ----------------------------------------------------------------------------
// Right-side panel for the extended view showing the current observation's
// position vs its trailing history:
//
//   - Percentile in trailing range (with Low/Normal/High bucket)
//   - Z-score regime slider (Normal / Elevated / Extreme bands)
//   - Optional interpretation paragraph
//
// Finance-blind — the per-tool wrapper passes the StretchContext
// descriptor; this component renders.  Per rendering_density.md §2.1
// this is an OPTIONAL slot — tools without a "stretch" concept omit it.
// ============================================================================

import { InfoTooltip } from '../elements/InfoTooltip';
import { ZScoreRegimeSlider } from '../elements/ZScoreRegimeSlider';
import { percentileLabel } from '../lib/format';
import { bucketForPercentile, toneTextClass } from '../lib/tone';
import type { StretchContext } from '../lib/types';

type Props = {
  context: StretchContext;
};

export function StretchContextCard({ context }: Props) {
  return (
    <section className="card flex flex-col gap-4 px-5 py-4">
      <div className="flex items-center gap-2">
        <h3 className="kicker text-fg-muted">STRETCH CONTEXT</h3>
        <InfoTooltip content="Where the current observation sits relative to its trailing-history distribution.  Percentile = positional rank inside the trailing range; z-score = standardised distance from the trailing mean." />
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        {context.percentile && (
          <div className="flex flex-col gap-2">
            <span className="kicker text-fg-faint">252D PERCENTILE</span>
            <div className="flex items-baseline gap-2">
              <span className="text-[28px] font-medium leading-none text-fg-primary">
                {percentileLabel(context.percentile.value)}
              </span>
              <span
                className={`text-[12px] ${toneTextClass(
                  context.percentile.bucket === 'High'
                    ? 'negative'
                    : context.percentile.bucket === 'Low'
                      ? 'positive'
                      : 'neutral',
                )}`}
              >
                {context.percentile.bucket}
              </span>
            </div>
            <PercentileBar value={context.percentile.value} />
          </div>
        )}

        {context.zScoreRegime && (
          <div className="flex flex-col gap-3">
            <span className="kicker text-fg-faint">Z-SCORE REGIME</span>
            <ZScoreRegimeSlider
              value={context.zScoreRegime.value}
              amberAt={context.zScoreRegime.bands?.amber}
              coralAt={context.zScoreRegime.bands?.coral}
            />
          </div>
        )}
      </div>

      {context.interpretation && (
        <div className="rounded-md border border-line-subtle bg-bg-elevated px-4 py-3">
          <span className="kicker text-fg-muted">INTERPRETATION</span>
          <p className="mt-1.5 text-[12.5px] leading-[1.55] text-fg-secondary">
            {context.interpretation}
          </p>
        </div>
      )}
    </section>
  );
}

/** Tiny horizontal bar showing the percentile position (0-100). */
function PercentileBar({ value }: { value: number | null | undefined }) {
  const safe =
    value == null || Number.isNaN(value)
      ? null
      : Math.max(0, Math.min(100, value));
  const tone = bucketForPercentile(value);
  const fillClass =
    tone === 'High'
      ? 'bg-coral-300/60'
      : tone === 'Low'
        ? 'bg-mint-300/60'
        : 'bg-ice-300/60';

  return (
    <div className="flex items-center gap-1.5">
      {Array.from({ length: 20 }).map((_, i) => {
        const filled = safe != null && (i + 1) * 5 <= safe;
        return (
          <div
            key={i}
            className={`h-2 w-2.5 rounded-sm ${
              filled ? fillClass : 'bg-line-subtle/60'
            }`}
            aria-hidden
          />
        );
      })}
    </div>
  );
}
