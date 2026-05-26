// ============================================================================
// CurveClassifierWidget — daily / weekly curve-move classification
// ----------------------------------------------------------------------------
// Pre-aggregated.  Surface for the substrate's `classify_curve_move_tool`.
// For each curve, renders the classification tag for "today" (1D) and
// "this week" (5D), with the front + back tenor bps changes
// underneath.
//
// Was named "RegimeMonitorWidget" in V0; renamed to mirror the
// backing tool's name.  "Regime" implied a broader macro framework
// the tool doesn't actually compute — it classifies a single curve
// move shape (steepener / flattener / twist), not a macro regime.
//
// Class chips are color-keyed: bull-steepener → mint, bear-steepener
// → coral, bull-flattener → ice, bear-flattener → amber, twist →
// violet, parallel → neutral.
// ============================================================================

import type { RegimeRow } from '@/types/rates';
import { useRatesDataContext } from '@/components/monitor/RatesDataProvider';
import {
  WidgetHeader,
  WidgetBody,
  WidgetProvenance,
} from '@/components/monitor/WidgetCard';
import { WidgetLoading, WidgetError } from '@/components/monitor/widgets/shared';
import { cn } from '@/utils/cn';

const CURVE_LABEL: Record<string, string> = {
  UST: 'UST',
  DE_BUND: 'Bund',
  UK_GILT: 'Gilt',
  JGB: 'JGB',
};

const REGIME_TONES: Record<
  string,
  { ring: string; bg: string; text: string }
> = {
  BULL_STEEPENER: {
    ring: 'ring-mint-400/30',
    bg: 'bg-mint-400/[0.10]',
    text: 'text-mint-300',
  },
  BEAR_STEEPENER: {
    ring: 'ring-coral-400/30',
    bg: 'bg-coral-400/[0.10]',
    text: 'text-coral-300',
  },
  BULL_FLATTENER: {
    ring: 'ring-ice-400/30',
    bg: 'bg-ice-400/[0.10]',
    text: 'text-ice-200',
  },
  BEAR_FLATTENER: {
    ring: 'ring-amber-400/30',
    bg: 'bg-amber-400/[0.10]',
    text: 'text-amber-300',
  },
  TWIST: {
    ring: 'ring-lineage-400/30',
    bg: 'bg-lineage-400/[0.10]',
    text: 'text-lineage-200',
  },
  PARALLEL_SHIFT: {
    ring: 'ring-line-soft',
    bg: 'bg-white/[0.025]',
    text: 'text-fg-secondary',
  },
};

const REGIME_LABEL: Record<string, string> = {
  BULL_STEEPENER: 'Bull steep',
  BEAR_STEEPENER: 'Bear steep',
  BULL_FLATTENER: 'Bull flat',
  BEAR_FLATTENER: 'Bear flat',
  TWIST: 'Twist',
  PARALLEL_SHIFT: 'Parallel',
};

export function CurveClassifierWidget() {
  const { data, isLoading, error } = useRatesDataContext();

  if (error && !data) return <WidgetError message={error.message} />;
  if (!data || isLoading) return <WidgetLoading label="Classifying curves…" />;

  const regimes = data.regimes.regimes;
  const byKey = new Map<string, RegimeRow>();
  for (const r of regimes) {
    byKey.set(`${r.curve_family}:${r.lookback_period}`, r);
  }
  const curves = ['UST', 'DE_BUND', 'UK_GILT', 'JGB'].filter(
    (c) => byKey.has(`${c}:1d`) || byKey.has(`${c}:5d`),
  );
  const asOf = regimes[0]?.as_of_date ?? null;

  return (
    <>
      <WidgetHeader
        kicker="CLASSIFY_CURVE_MOVE · 1D / 5D"
        title="Curve Classifier"
      />
      <WidgetBody className="space-y-1.5 px-3.5 pb-3">
        {/* Column headers */}
        <div className="grid grid-cols-[68px_1fr_1fr] gap-3 px-2">
          <span className="text-[9.5px] font-semibold uppercase tracking-[0.16em] text-fg-faint">
            Curve
          </span>
          <span className="text-center text-[9.5px] font-semibold uppercase tracking-[0.16em] text-fg-faint">
            Today · 1d
          </span>
          <span className="text-center text-[9.5px] font-semibold uppercase tracking-[0.16em] text-fg-faint">
            Week · 5d
          </span>
        </div>
        {curves.map((curve) => {
          const r1d = byKey.get(`${curve}:1d`);
          const r5d = byKey.get(`${curve}:5d`);
          return (
            <div
              key={curve}
              className="grid grid-cols-[68px_1fr_1fr] items-center gap-3 rounded-[10px] bg-white/[0.012] px-3 py-2.5 ring-1 ring-line-subtle"
            >
              <div>
                <div className="text-[12.5px] font-medium tracking-[-0.005em] text-fg-primary">
                  {CURVE_LABEL[curve] ?? curve}
                </div>
                <div className="mt-0.5 font-mono text-[9.5px] text-fg-faint">
                  {r1d?.spread_label ?? '2s10s'}
                </div>
              </div>
              <RegimeCell row={r1d} />
              <RegimeCell row={r5d} />
            </div>
          );
        })}
      </WidgetBody>
      <WidgetProvenance toolName="classify_curve_move_tool" asOfDate={asOf} />
    </>
  );
}

function RegimeCell({ row }: { row: RegimeRow | undefined }) {
  if (!row) {
    return <span className="text-center font-mono text-[10px] text-fg-faint">—</span>;
  }
  const tone = REGIME_TONES[row.regime_tag] ?? REGIME_TONES.PARALLEL_SHIFT;
  const label = REGIME_LABEL[row.regime_tag] ?? row.regime_tag;
  return (
    <div className="flex flex-col items-center gap-1.5">
      <span
        className={cn(
          'inline-flex items-center rounded-full px-2 py-[1px] text-[10.5px] font-medium tracking-[-0.005em] ring-1',
          tone.ring,
          tone.bg,
          tone.text,
        )}
      >
        {label}
      </span>
      <div className="flex items-baseline gap-2 font-mono text-[9.5px] text-fg-faint">
        <span>
          2Y{' '}
          <span
            className={cn(
              'font-medium',
              (row.front_change_bps ?? 0) < 0
                ? 'text-mint-300'
                : 'text-coral-300',
            )}
          >
            {row.front_change_bps !== null
              ? `${row.front_change_bps > 0 ? '+' : ''}${row.front_change_bps.toFixed(1)}`
              : '—'}
          </span>
        </span>
        <span>
          10Y{' '}
          <span
            className={cn(
              'font-medium',
              (row.back_change_bps ?? 0) < 0
                ? 'text-mint-300'
                : 'text-coral-300',
            )}
          >
            {row.back_change_bps !== null
              ? `${row.back_change_bps > 0 ? '+' : ''}${row.back_change_bps.toFixed(1)}`
              : '—'}
          </span>
        </span>
      </div>
    </div>
  );
}
