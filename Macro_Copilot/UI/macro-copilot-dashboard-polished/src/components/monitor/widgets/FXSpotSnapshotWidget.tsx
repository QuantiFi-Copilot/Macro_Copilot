// ============================================================================
// FXSpotSnapshotWidget — single-pair FX spot snapshot
// ----------------------------------------------------------------------------
// Pre-aggregated widget.  Reads from `useFxDataContext` (single page-
// level fetch shared across pre-aggregated FX widgets).  Mirrors the
// rates YieldLevelWidget tone for a single asset: large spot number,
// short-period moves (1D / 1W / 1M), and a 252-day context strip
// (z-score, percentile, range high/low).
//
// V1 pair is fixed to EURUSD via `useFxData` — same constraint as the
// rates pre-aggregated widgets, which hardcode the curve set in the
// page-card hook.  V2 will make the pair a configurable widget param.
// ============================================================================

import { useFxDataContext } from '@/components/monitor/FXDataProvider';
import {
  WidgetHeader,
  WidgetBody,
  WidgetProvenance,
} from '@/components/monitor/WidgetCard';
import { WidgetLoading, WidgetError } from './shared';
import { cn } from '@/utils/cn';

export function FXSpotSnapshotWidget() {
  const { data, isLoading, error } = useFxDataContext();

  if (error && !data) return <WidgetError message={error.message} />;
  if (!data || isLoading) return <WidgetLoading label="Loading spot…" />;

  const m = data.eurusd.current_metrics;
  const isJpy = m.pair.includes('JPY');
  const spotDigits = isJpy ? 2 : 4;

  return (
    <>
      <WidgetHeader
        kicker={`FX SPOT · ${m.pair}`}
        title="Spot Snapshot"
        meta={
          <span className="font-mono text-[10.5px] text-fg-muted">
            252d · n={m.observation_count}
          </span>
        }
      />
      <WidgetBody className="flex min-h-0 flex-1 flex-col px-5 pb-2">
        <div className="mt-1 font-mono text-[30px] font-medium tracking-[-0.025em] text-fg-primary">
          {fmtNum(m.current_spot, spotDigits)}
        </div>

        <div className="mt-4 grid grid-cols-3 gap-2">
          <Chip label="1D" value={fmtPct(m.daily_change_pct)} tone={pctTone(m.daily_change_pct)} />
          <Chip label="1W" value={fmtPct(m.weekly_change_pct)} tone={pctTone(m.weekly_change_pct)} />
          <Chip label="1M" value={fmtPct(m.monthly_change_pct)} tone={pctTone(m.monthly_change_pct)} />
        </div>

        <div className="mt-3 grid grid-cols-2 gap-2">
          <Chip label="Z-score" value={fmtZ(m.z_score)} tone={zScoreTone(m.z_score)} />
          <Chip label="252d pctile" value={fmtPctile(m.percentile_252d)} tone="neutral" />
          <Chip label="252d high" value={fmtNum(m.high_252d, spotDigits)} tone="neutral" />
          <Chip label="252d low" value={fmtNum(m.low_252d, spotDigits)} tone="neutral" />
        </div>
      </WidgetBody>
      <WidgetProvenance toolName="get_fx_spot_level" asOfDate={m.as_of_date} />
    </>
  );
}

// ----------------------------------------------------------------------------

type ChipTone = 'mint' | 'coral' | 'amber' | 'ice' | 'neutral';

function Chip({ label, value, tone }: { label: string; value: string; tone: ChipTone }) {
  return (
    <div className="rounded-lg border border-line-soft bg-white/[0.018] px-2.5 py-1.5">
      <div className="text-[9px] uppercase tracking-[0.14em] text-fg-faint">{label}</div>
      <div
        className={cn(
          'mt-0.5 font-mono text-[12px] font-medium tracking-[-0.005em]',
          toneClass(tone),
        )}
      >
        {value}
      </div>
    </div>
  );
}

function toneClass(t: ChipTone): string {
  switch (t) {
    case 'mint':
      return 'text-mint-300';
    case 'coral':
      return 'text-coral-300';
    case 'amber':
      return 'text-amber-300';
    case 'ice':
      return 'text-ice-200';
    default:
      return 'text-fg-primary';
  }
}

function pctTone(v: number | null): ChipTone {
  if (v === null || Number.isNaN(v)) return 'neutral';
  if (v > 0) return 'mint';
  if (v < 0) return 'coral';
  return 'neutral';
}

function zScoreTone(z: number | null): ChipTone {
  if (z === null) return 'neutral';
  const abs = Math.abs(z);
  if (abs >= 2.0) return z > 0 ? 'coral' : 'mint';
  if (abs >= 1.5) return z > 0 ? 'amber' : 'ice';
  return 'neutral';
}

function fmtNum(v: number | null, digits: number): string {
  if (v === null || Number.isNaN(v)) return '—';
  return v.toFixed(digits);
}

function fmtPct(v: number | null): string {
  if (v === null || Number.isNaN(v)) return '—';
  const sign = v > 0 ? '+' : '';
  return `${sign}${v.toFixed(2)}%`;
}

function fmtZ(v: number | null): string {
  if (v === null) return '—';
  const sign = v > 0 ? '+' : '';
  return `${sign}${v.toFixed(2)}`;
}

function fmtPctile(v: number | null): string {
  if (v === null) return '—';
  return `${v.toFixed(0)}%`;
}
