// ============================================================================
// RegimePrimitiveView — view for ``classify_curve_move_tool``.
// ----------------------------------------------------------------------------
// Classification of a curve move (bull steepener / bear flattener / …) over
// a lookback window.  Reshell of the legacy RegimeView onto
// PrimitiveCanvasShell (phase R2).
// ============================================================================

import type { RegimeOutput } from '@/types/rates';
import { cn } from '@/utils/cn';
import {
  PrimitiveMetrics,
  formatSigned,
  toneForChange,
  type MetricItem,
} from './PrimitiveMetrics';
import { PrimitiveCanvasShell, ToolNameChip } from './PrimitiveCanvasShell';

type Props = {
  payload: RegimeOutput;
};

function regimeAccent(tag: string): { badgeClass: string } {
  const t = tag.toLowerCase();
  if (t.includes('bull')) {
    return { badgeClass: 'border-mint-400/30 bg-mint-500/10 text-mint-300' };
  }
  if (t.includes('bear')) {
    return { badgeClass: 'border-coral-400/30 bg-coral-500/10 text-coral-300' };
  }
  return { badgeClass: 'border-line-soft bg-white/[0.03] text-fg-secondary' };
}

export function RegimePrimitiveView({ payload }: Props) {
  const { current_metrics: cm } = payload;
  const accent = regimeAccent(cm.regime_tag);

  const items: MetricItem[] = [
    {
      label: 'Spread',
      value: cm.spread_current_bps?.toFixed(1) ?? '—',
      unit: 'bps',
      emphasis: true,
    },
    {
      label: 'Spread Δ',
      value: formatSigned(cm.spread_change_bps, 1),
      unit: 'bps',
      tone: toneForChange(cm.spread_change_bps),
    },
    {
      label: `${cm.front_tenor} Δ`,
      value: formatSigned(cm.front_change_bps, 1),
      unit: 'bps',
      tone: toneForChange(cm.front_change_bps),
    },
    {
      label: `${cm.back_tenor} Δ`,
      value: formatSigned(cm.back_change_bps, 1),
      unit: 'bps',
      tone: toneForChange(cm.back_change_bps),
    },
    {
      label: `${cm.front_tenor} now`,
      value: cm.front_level_current?.toFixed(3) ?? '—',
      unit: '%',
    },
    {
      label: `${cm.back_tenor} now`,
      value: cm.back_level_current?.toFixed(3) ?? '—',
      unit: '%',
    },
    {
      label: `${cm.front_tenor} prior`,
      value: cm.front_level_prior?.toFixed(3) ?? '—',
      unit: '%',
    },
    {
      label: `${cm.back_tenor} prior`,
      value: cm.back_level_prior?.toFixed(3) ?? '—',
      unit: '%',
    },
    {
      label: 'Spread prior',
      value: cm.spread_prior_bps?.toFixed(1) ?? '—',
      unit: 'bps',
    },
  ];

  return (
    <PrimitiveCanvasShell
      kicker="Primitive · Curve Regime"
      title={`${cm.curve_family} ${cm.spread_label}`}
      subtitle={`Classification over ${cm.lookback_period} window.`}
      asOfDate={cm.as_of_date ?? null}
      meta={<ToolNameChip tool="classify_curve_move_tool" />}
      methodology={
        <>
          <p>
            Front / back yield change over the lookback period drives the
            classification.  A "bull" tag means yields rallied (lower
            yields); "bear" means yields sold off.  "Steepener" vs.
            "flattener" depends on the spread Δ.
          </p>
          <p className="mt-2 text-fg-muted">
            Source: Bloomberg mid-yield.  Lookback expressed in trading
            days; the prior reference date is{' '}
            <span className="font-mono">{cm.prior_date}</span>.
          </p>
        </>
      }
    >
      <section className="research-card px-6 py-5">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <div className="flex flex-col gap-1.5">
            <div className="kicker text-fg-muted">
              {cm.curve_family} · {cm.spread_label} · {cm.lookback_period}
            </div>
            <div className="flex items-center gap-3">
              <span
                className={cn(
                  'mono inline-flex items-center rounded-md border px-2.5 py-1 text-[12px] font-semibold uppercase tracking-[0.04em]',
                  accent.badgeClass,
                )}
              >
                {cm.regime_tag}
              </span>
              <span className="text-[11.5px] text-fg-secondary">
                {cm.regime_description}
              </span>
            </div>
          </div>
          <div className="text-[10.5px] text-fg-faint">
            <div>prior: {cm.prior_date}</div>
            <div>now: {cm.as_of_date}</div>
          </div>
        </div>
      </section>

      <section className="research-card px-5 py-4">
        <PrimitiveMetrics items={items} title="Move detail" desktopCols={5} />
      </section>
    </PrimitiveCanvasShell>
  );
}
