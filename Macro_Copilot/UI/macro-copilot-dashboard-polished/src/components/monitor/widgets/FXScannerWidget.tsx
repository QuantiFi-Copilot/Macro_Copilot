// ============================================================================
// FXScannerWidget — top-N FX pairs ranked by |z-score|
// ----------------------------------------------------------------------------
// Pre-aggregated; reads from useFxDataContext.  Mirrors the rates
// ScannerWidget: ranked list with a row-fill bar keyed to |z-score|
// magnitude, signal coloring matching the rates "EXTREME_HIGH /
// EXTREME_LOW" convention.
// ============================================================================

import { useFxDataContext } from '@/components/monitor/FXDataProvider';
import {
  WidgetHeader,
  WidgetBody,
  WidgetProvenance,
} from '@/components/monitor/WidgetCard';
import { WidgetLoading, WidgetError, WidgetEmpty } from './shared';
import { cn } from '@/utils/cn';

// Display constants surfaced as visible meta on the widget header so
// the user can read the filter without inspecting code.  These mirror
// the request params hardcoded in useFxData.ts; V2 will make them
// configurable.
const SCANNER_TOP_N = 8;

export function FXScannerWidget() {
  const { data, isLoading, error } = useFxDataContext();

  if (error && !data) return <WidgetError message={error.message} />;
  if (!data || isLoading) return <WidgetLoading label="Scanning FX…" />;

  const rows = data.scanner.rows.slice(0, SCANNER_TOP_N);
  const flaggedCount = rows.length;

  return (
    <>
      <WidgetHeader
        kicker="FX ANOMALY · |Z-SCORE| EXTREMES"
        title="FX Scanner"
        meta={
          <div className="flex items-center gap-1.5">
            <span className="inline-flex items-center rounded-full bg-white/[0.025] px-2 py-0.5 font-mono text-[10px] tracking-[0.02em] text-fg-muted ring-1 ring-line-soft">
              top {SCANNER_TOP_N}
            </span>
            <span
              className={cn(
                'inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium tracking-[0.02em]',
                flaggedCount > 0
                  ? 'bg-amber-400/[0.12] text-amber-300 ring-1 ring-amber-400/30'
                  : 'bg-mint-400/[0.10] text-mint-300 ring-1 ring-mint-400/25',
              )}
            >
              {flaggedCount} ranked
            </span>
          </div>
        }
      />
      <WidgetBody className="px-2.5 pb-2">
        {flaggedCount === 0 ? (
          <WidgetEmpty message="No FX pairs returned by the scanner." />
        ) : (
          <div className="flex min-h-0 flex-1 flex-col">
            <div className="grid grid-cols-[28px_1fr_64px_56px_50px] items-center gap-2 px-2.5 pb-1.5 pt-0.5">
              <span className="text-[9px] font-semibold uppercase tracking-[0.16em] text-fg-faint">
                #
              </span>
              <span className="text-[9px] font-semibold uppercase tracking-[0.16em] text-fg-faint">
                Pair
              </span>
              <span className="text-right text-[9px] font-semibold uppercase tracking-[0.16em] text-fg-faint">
                Spot
              </span>
              <span className="text-right text-[9px] font-semibold uppercase tracking-[0.16em] text-fg-faint">
                1M
              </span>
              <span className="text-right text-[9px] font-semibold uppercase tracking-[0.16em] text-fg-faint">
                z
              </span>
            </div>

            {rows.map((r, idx) => {
              const z = r.z_score ?? 0;
              const zAbs = Math.abs(z);
              const isHigh = r.signal === 'EXTREME_HIGH' || z > 0;
              const barWidth = Math.min(100, (zAbs / 3.5) * 100);
              const zColor =
                zAbs >= 2.5
                  ? 'text-coral-300'
                  : zAbs >= 2.0
                    ? 'text-amber-300'
                    : 'text-ice-200';
              const spotDigits = r.pair.includes('JPY') ? 2 : 4;

              return (
                <div
                  key={r.pair}
                  className={cn(
                    'group relative grid grid-cols-[28px_1fr_64px_56px_50px] items-center gap-2 px-2.5 py-2 transition-colors duration-150 hover:bg-white/[0.018]',
                    idx > 0 && 'border-t border-line-subtle/40',
                  )}
                >
                  <span
                    aria-hidden
                    className="absolute inset-y-1 left-0 rounded-[2px] opacity-[0.05]"
                    style={{
                      width: `${barWidth}%`,
                      background: isHigh
                        ? 'linear-gradient(90deg, rgba(248,113,113,0.95), transparent)'
                        : 'linear-gradient(90deg, rgba(63,214,154,0.95), transparent)',
                    }}
                  />
                  <span className="relative font-mono text-[10px] font-medium text-fg-faint">
                    {String(idx + 1).padStart(2, '0')}
                  </span>
                  <span className="relative truncate text-[12px] font-medium tracking-[-0.005em] text-fg-primary">
                    {r.pair}
                  </span>
                  <span className="relative text-right font-mono text-[11px] tracking-[-0.005em] text-fg-primary">
                    {r.current_spot.toFixed(spotDigits)}
                  </span>
                  <span
                    className={cn(
                      'relative text-right font-mono text-[11px] font-medium tracking-[-0.005em]',
                      (r.momentum_1m_pct ?? 0) > 0
                        ? 'text-mint-300'
                        : (r.momentum_1m_pct ?? 0) < 0
                          ? 'text-coral-300'
                          : 'text-fg-muted',
                    )}
                  >
                    {r.momentum_1m_pct !== null
                      ? `${r.momentum_1m_pct > 0 ? '+' : ''}${r.momentum_1m_pct.toFixed(2)}%`
                      : '—'}
                  </span>
                  <span
                    className={cn(
                      'relative text-right font-mono text-[11px] font-semibold tracking-[-0.005em]',
                      zColor,
                    )}
                  >
                    {r.z_score !== null
                      ? `${r.z_score > 0 ? '+' : ''}${r.z_score.toFixed(2)}`
                      : '—'}
                  </span>
                </div>
              );
            })}
          </div>
        )}
      </WidgetBody>
      <WidgetProvenance
        toolName="scan_fx_spot"
        asOfDate={rows[0]?.as_of_date ?? null}
      />
    </>
  );
}
