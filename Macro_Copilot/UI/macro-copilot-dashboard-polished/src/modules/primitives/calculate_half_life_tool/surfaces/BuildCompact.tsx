// ============================================================================
// surfaces/BuildCompact.tsx — Compact Build view for
// ``calculate_half_life_tool``.  RICH-MODEL SNAPSHOT shape.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.2 every primitive claiming
// ``custom_build_surface`` ships a compact view; this is half-life's,
// mounted as a node body inside multi-tool query DAG visualizations.
//
// NO SPARKLINE — and honestly so: the tool is a PURE SNAPSHOT (the input
// series IS the time-series content; no time_series field exists on the
// wire, pinned by the backend's test_no_time_series_output).  The shared
// ``BuildCompactShell`` is LEVEL-shape oriented (3 KPI cells + sparkline +
// footer); rendering an empty chart would imply missing data rather than
// a snapshot-by-contract tool.  Following the scan_extremes_tool density-
// deviation precedent, this component honours the SEMANTIC contract of
// §2.2 (identity / headline data / methodology caveat / expand affordance
// / tone cues) with a snapshot-shaped layout: 3 KPIs + a half-life CI
// band where the sparkline would sit, reusing the shared FreshnessPill +
// tone treatment so it reads like the rest of the compact catalogue.
// THESIS Q3 documents the deviation.
// ============================================================================

import { ArrowUpRight, Info, Timer } from 'lucide-react';
import { cn } from '@/utils/cn';
import {
  FreshnessPill,
  type BuildCompactProps,
} from '@/components/shared/build';
import type { MetricItem } from '@/components/shared/build/model';
import {
  HALF_LIFE_UNDEFINED_NOTE,
  compactCaveat,
  compactKpis,
  constructedSeriesLabel,
  formatConfidencePct,
  unsignedFixed,
  useHalfLifeData,
} from './halfLifeShared';

const BuildCompact: React.FC<BuildCompactProps> = ({
  params,
  size = 'small',
  onExpand,
  callMeta,
}) => {
  // ----- Record<string,string> param parsing -----
  const curveFamily = params.curve_family ?? '';
  const tenor = params.tenor ?? '';
  const curveFamily2 = params.curve_family_2 || undefined;
  const lookbackDays =
    params.lookback_days != null && params.lookback_days !== ''
      ? Number(params.lookback_days)
      : undefined;
  const fieldName = params.field_name || undefined;

  const { data, isLoading, errorMessage } = useHalfLifeData({
    curveFamily,
    tenor,
    curveFamily2,
    lookbackDays,
    fieldName,
  });

  const cm = data?.current_metrics;
  const seriesLabel =
    cm?.series_label ?? constructedSeriesLabel(curveFamily, tenor, curveFamily2);
  const kpis: MetricItem[] = data
    ? compactKpis(data)
    : [
        { label: 'HALF-LIFE', value: '—', emphasis: true },
        { label: 'DEVIATION', value: '—' },
        { label: 'MEAN-REVERTING', value: '—' },
      ];

  const minHeight = size === 'medium' ? 340 : 250;

  return (
    <article
      className="card flex flex-col gap-3 overflow-hidden bg-bg-elevated px-5 py-4"
      style={{ minHeight }}
      data-testid="half-life-compact"
    >
      {/* ---------- Header ---------- */}
      <header className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 flex-1 items-start gap-2.5">
          <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md border border-line-subtle bg-surface-overlay">
            <Timer size={12} strokeWidth={1.75} className="text-ice-300" aria-hidden />
          </span>
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-[13px] font-semibold text-fg-primary">
              <span className="truncate">{seriesLabel} HALF-LIFE</span>
              <span className="text-fg-faint" aria-hidden>·</span>
              <span className="kicker text-fg-muted">SNAPSHOT</span>
            </div>
            <div className="mt-0.5 flex items-center gap-1.5 text-[12px] text-fg-secondary">
              <span>OU / AR(1) mean-reversion fit</span>
              {callMeta && (
                <span className="ml-1 kicker text-fg-faint">
                  · call {callMeta.n} of {callMeta.m}
                </span>
              )}
            </div>
          </div>
        </div>
        {onExpand && (
          <button
            type="button"
            onClick={onExpand}
            aria-label={`Open extended view of ${seriesLabel} half-life`}
            title="Open extended view"
            className="rounded-md border border-line-subtle bg-surface-overlay p-1.5 text-fg-muted transition-colors hover:border-line-strong hover:text-fg-primary"
          >
            <ArrowUpRight size={14} strokeWidth={1.75} />
          </button>
        )}
      </header>

      <hr className="border-t border-line-subtle" />

      {/* ---------- Body: 3 KPIs + CI band ---------- */}
      {errorMessage ? (
        <CompactError message={errorMessage} />
      ) : isLoading ? (
        <CompactSkeleton />
      ) : (
        <div className="flex min-h-0 flex-1 flex-col justify-between gap-3">
          <div className="grid grid-cols-3 gap-x-4">
            {kpis.map((k) => (
              <KpiCell key={k.label} item={k} />
            ))}
          </div>
          <HalfLifeCiBand
            point={cm?.half_life_days ?? null}
            lo={cm?.half_life_ci_lower_days ?? null}
            hi={cm?.half_life_ci_upper_days ?? null}
            confidencePct={formatConfidencePct(cm?.confidence_level_used)}
            isMeanReverting={cm?.is_mean_reverting ?? null}
          />
        </div>
      )}

      {/* ---------- Footer ---------- */}
      <hr className="border-t border-line-subtle" />
      <footer className="flex items-center justify-between gap-3 text-[11px]">
        <div
          className="flex min-w-0 flex-1 items-center gap-1.5 text-fg-secondary"
          title={compactCaveat(data)}
        >
          <Info size={12} strokeWidth={1.5} className="shrink-0 text-fg-muted" aria-hidden />
          <span className="truncate">{compactCaveat(data)}</span>
        </div>
        <div className="flex shrink-0 items-center gap-3 text-fg-muted">
          {cm?.as_of_date && <span>As of {cm.as_of_date}</span>}
          <FreshnessPill freshness="fresh" />
        </div>
      </footer>
    </article>
  );
};

export default BuildCompact;

// ---------------------------------------------------------------------------
// KPI cell — local rendering of the shared MetricItem descriptor shape at
// compact density (the shared PrimitiveMetrics grid is canvas-density;
// tone vocabulary + descriptor data come from halfLifeShared so the
// numbers and colours match the extended hero exactly).
// ---------------------------------------------------------------------------

const KPI_TONE_CLASS: Record<NonNullable<MetricItem['tone']>, string> = {
  neutral: 'text-fg-primary',
  positive: 'text-mint-400',
  negative: 'text-coral-400',
  warning: 'text-amber-400',
  extreme: 'text-coral-400',
};

function KpiCell({ item }: { item: MetricItem }) {
  const toneClass = KPI_TONE_CLASS[item.tone ?? 'neutral'];
  const valueSize = item.emphasis ? 'text-[22px]' : 'text-[16px]';
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <span className="text-[10px] uppercase tracking-[0.06em] text-fg-faint">
        {item.label}
      </span>
      <span className="flex items-baseline gap-1">
        <span className={cn('mono font-semibold tabular-nums', valueSize, toneClass)}>
          {item.value ?? '—'}
        </span>
        {item.unit && (
          <span className="text-[10.5px] text-fg-muted">{item.unit}</span>
        )}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// CI band — the honest replacement for the sparkline slot.  A simple
// horizontal range strip: [CI lower – CI upper] band with the point
// estimate marked, scaled 0 → ~1.1 × CI upper.  When the CI (or the
// half-life itself) is undefined, the row says so instead of rendering
// an empty band (P5 / P6 honesty).
// ---------------------------------------------------------------------------

function HalfLifeCiBand({
  point,
  lo,
  hi,
  confidencePct,
  isMeanReverting,
}: {
  point: number | null;
  lo: number | null;
  hi: number | null;
  confidencePct: string;
  isMeanReverting: boolean | null;
}) {
  if (point == null) {
    return (
      <p className="text-[10.5px] leading-snug text-fg-muted">
        {isMeanReverting === false
          ? 'Not mean-reverting (β ≥ 0) — no half-life to band.'
          : HALF_LIFE_UNDEFINED_NOTE}
      </p>
    );
  }
  if (lo == null || hi == null) {
    return (
      <p className="text-[10.5px] leading-snug text-fg-muted">
        Half-life {unsignedFixed(point, 1)}d — CI undefined for this fit (β std-error unavailable).
      </p>
    );
  }

  const axisMax = Math.max(hi, point) * 1.1 || 1;
  const pct = (v: number) => `${Math.max(0, Math.min(100, (v / axisMax) * 100))}%`;

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-baseline justify-between text-[10px] uppercase tracking-[0.06em] text-fg-faint">
        <span>Half-life CI ({confidencePct}, delta method)</span>
        <span className="mono normal-case tracking-normal text-fg-muted">
          [{unsignedFixed(lo, 1)} – {unsignedFixed(hi, 1)}]d
        </span>
      </div>
      <div className="relative h-2 overflow-hidden rounded-full border border-line-subtle bg-white/[0.012]">
        <div
          className="absolute inset-y-0 rounded-full bg-ice-300/25"
          style={{ left: pct(lo), width: `calc(${pct(hi)} - ${pct(lo)})` }}
          aria-hidden
        />
        <div
          className="absolute inset-y-0 w-[2px] bg-ice-300"
          style={{ left: pct(point) }}
          title={`Point estimate ${unsignedFixed(point, 1)}d`}
          aria-hidden
        />
      </div>
      <div className="flex justify-between text-[9.5px] text-fg-faint">
        <span>0d</span>
        <span>{unsignedFixed(axisMax, 0)}d</span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Skeleton + error states
// ---------------------------------------------------------------------------

function CompactSkeleton() {
  return (
    <div className="flex min-h-[110px] flex-1 flex-col justify-between gap-3" aria-busy>
      <div className="grid grid-cols-3 gap-x-4">
        {[0, 1, 2].map((i) => (
          <div key={i} className="flex flex-col gap-1.5">
            <div className="h-2.5 w-16 animate-pulse rounded bg-line-subtle" />
            <div className="h-5 w-14 animate-pulse rounded bg-line-subtle" />
          </div>
        ))}
      </div>
      <div className="h-2 w-full animate-pulse rounded-full bg-line-subtle" />
    </div>
  );
}

function CompactError({ message }: { message: string }) {
  return (
    <div className="flex min-h-[110px] flex-1 items-center justify-center px-2">
      <p className="text-center text-[11.5px] leading-[1.5] text-coral-300">
        {message}
      </p>
    </div>
  );
}
