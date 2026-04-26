// ============================================================================
// RegimeView
// ----------------------------------------------------------------------------
// /detail/regime: classification of the curve move over a lookback period
// (bull steepener / bear flattener / etc.).  No time series — we render the
// classification prominently with the supporting front/back yield deltas.
// ============================================================================

import type { RegimeOutput } from '@/types/rates';
import {
  WorkspaceMetrics,
  formatSigned,
  toneForChange,
  type MetricItem,
} from '../WorkspaceMetrics';

type RegimeViewProps = {
  payload: RegimeOutput;
};

// Map regime tags to a tonal accent so the eye can pick out direction at
// a glance.  Bull ≈ rallying yields (mint), bear ≈ selling off (coral).
function regimeAccent(tag: string): {
  badgeClass: string;
  textClass: string;
} {
  const t = tag.toLowerCase();
  if (t.includes('bull')) {
    return {
      badgeClass: 'border-mint-400/30 bg-mint-500/10 text-mint-300',
      textClass: 'text-mint-300',
    };
  }
  if (t.includes('bear')) {
    return {
      badgeClass: 'border-coral-400/30 bg-coral-500/10 text-coral-300',
      textClass: 'text-coral-300',
    };
  }
  return {
    badgeClass: 'border-line-soft bg-white/[0.03] text-fg-secondary',
    textClass: 'text-fg-secondary',
  };
}

export function RegimeView({ payload }: RegimeViewProps) {
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
      value: cm.front_yield_current?.toFixed(3) ?? '—',
      unit: '%',
    },
    {
      label: `${cm.back_tenor} now`,
      value: cm.back_yield_current?.toFixed(3) ?? '—',
      unit: '%',
    },
    {
      label: `${cm.front_tenor} prior`,
      value: cm.front_yield_prior?.toFixed(3) ?? '—',
      unit: '%',
    },
    {
      label: `${cm.back_tenor} prior`,
      value: cm.back_yield_prior?.toFixed(3) ?? '—',
      unit: '%',
    },
    {
      label: 'Spread prior',
      value: cm.spread_prior_bps?.toFixed(1) ?? '—',
      unit: 'bps',
    },
  ];

  return (
    <div className="flex flex-col gap-6 px-6 py-5">
      <section className="card px-6 py-5">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <div className="flex flex-col gap-1.5">
            <div className="kicker">
              {cm.curve_family} · {cm.spread_label} · {cm.lookback_period} window
            </div>
            <div className="flex items-center gap-3">
              <span
                className={`mono inline-flex items-center rounded-md border px-2.5 py-1 text-[12px] font-semibold uppercase tracking-[0.04em] ${accent.badgeClass}`}
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

      <section className="card px-5 py-4">
        <WorkspaceMetrics items={items} title="Move detail" desktopCols={5} />
      </section>
    </div>
  );
}
