// ============================================================================
// shared/build/extended/KPIStrip.tsx
// ----------------------------------------------------------------------------
// N-cell horizontal KPI strip for the extended Build canvas.  Each cell
// shows label + large value + optional unit + optional subtext + optional
// sparkline UNDER the value.
//
// Finance-blind — the per-tool wrapper provides the KPIDescriptor list.
// Cell count adapts (no hard cap); the layout uses CSS grid with equal-
// width columns.
// ============================================================================

import { useMemo } from 'react';
import type { KPIDescriptor } from '../lib/types';
import { toneTextClass } from '../lib/tone';

type Props = {
  kpis: ReadonlyArray<KPIDescriptor>;
};

export function KPIStrip({ kpis }: Props) {
  if (kpis.length === 0) return null;

  // Use auto-fit columns so 6, 7, 8, 9 cells all lay out reasonably.
  // Min cell width 110px so labels don't truncate.
  return (
    <section
      className="card flex flex-wrap items-stretch gap-4 px-5 py-4"
      data-testid="kpi-strip-extended"
    >
      {kpis.map((kpi, i) => (
        <KPICell key={`${kpi.label}-${i}`} kpi={kpi} />
      ))}
    </section>
  );
}

function KPICell({ kpi }: { kpi: KPIDescriptor }) {
  return (
    <div className="flex min-w-[110px] flex-1 flex-col gap-1.5">
      <span className="kicker text-fg-muted">{kpi.label}</span>
      <div className="flex items-baseline gap-1">
        <span
          className={`text-[22px] font-medium leading-none ${toneTextClass(kpi.tone)}`}
        >
          {kpi.value}
        </span>
        {kpi.unit && (
          <span
            className={`text-[12px] leading-none ${toneTextClass(kpi.tone)} opacity-80`}
          >
            {kpi.unit}
          </span>
        )}
      </div>
      {kpi.subtext && (
        <span className={`text-[10.5px] ${toneTextClass(kpi.tone)} opacity-75`}>
          {kpi.subtext}
        </span>
      )}
      {kpi.caption && (
        <span className={`text-[10.5px] ${toneTextClass(kpi.tone)}`}>
          {kpi.caption}
        </span>
      )}
      {kpi.sparkline && kpi.sparkline.length > 1 && (
        <Sparkline points={kpi.sparkline} tone={kpi.tone ?? 'neutral'} />
      )}
    </div>
  );
}

function Sparkline({
  points,
  tone,
}: {
  points: ReadonlyArray<number>;
  tone: KPIDescriptor['tone'];
}) {
  const path = useMemo(() => buildSparkPath(points), [points]);
  const stroke =
    tone === 'positive' || tone === 'extreme-down'
      ? '#7ee5c1'
      : tone === 'negative' || tone === 'extreme-up'
        ? '#ff8c8c'
        : tone === 'elevated'
          ? '#f4c47a'
          : '#7b6cff';
  return (
    <svg
      viewBox="0 0 100 14"
      preserveAspectRatio="none"
      className="mt-0.5 h-2.5 w-full"
      aria-hidden
    >
      <path d={path} fill="none" stroke={stroke} strokeWidth={1} opacity={0.6} />
    </svg>
  );
}

function buildSparkPath(points: ReadonlyArray<number>): string {
  if (points.length < 2) return '';
  const min = Math.min(...points);
  const max = Math.max(...points);
  const range = max - min || 1;
  const step = 100 / (points.length - 1);
  return points
    .map((v, i) => {
      const x = i * step;
      const y = 14 - ((v - min) / range) * 14;
      return `${i === 0 ? 'M' : 'L'} ${x.toFixed(2)} ${y.toFixed(2)}`;
    })
    .join(' ');
}
