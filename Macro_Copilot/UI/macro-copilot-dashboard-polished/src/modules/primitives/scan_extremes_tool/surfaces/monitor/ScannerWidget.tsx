// ============================================================================
// ScannerWidget — top-N z-score extremes across the global universe
// ----------------------------------------------------------------------------
// Pre-aggregated; reads from useRatesDataContext.  Renders a ranked
// table of instruments above the configured z-score threshold, with a
// subtle row-fill bar keyed to |z-score| magnitude.
//
// The scanner is the canonical "anomaly" widget — its top-rule renders
// in amber (vs. ice for data widgets) so the user can spot it at
// peripheral glance.
// ============================================================================

import { useRatesDataContext } from '@/components/monitor/RatesDataProvider';
import {
  WidgetHeader,
  WidgetBody,
  WidgetProvenance,
} from '@/components/monitor/WidgetCard';
import { WidgetLoading, WidgetError, WidgetEmpty } from '@/components/monitor/widgets/shared';
import { cn } from '@/utils/cn';

const CURVE_SHORT: Record<string, string> = {
  UST: 'UST',
  DE_BUND: 'Bund',
  UK_GILT: 'Gilt',
  JGB: 'JGB',
  FR_OAT: 'OAT',
  IT_BTP: 'BTP',
  ES_BONO: 'Bono',
  AU_GOVT: 'AUS',
  CANADA_GOVT: 'CAN',
};

// Display constants surfaced as visible meta on the widget header so
// the user can read the filter without inspecting code.  These mirror
// the request params hardcoded in useRatesData.ts; in V2 they become
// configurable widget params and this constant goes away.
const SCANNER_THRESHOLD_SIGMA = 1.5;
const SCANNER_TOP_N = 8;

export function ScannerWidget() {
  const { data, isLoading, error } = useRatesDataContext();

  if (error && !data) return <WidgetError message={error.message} />;
  if (!data || isLoading) return <WidgetLoading label="Scanning…" />;

  const scanner = data.scanner;
  const flaggedCount = scanner.results.length;

  return (
    <>
      <WidgetHeader
        kicker="ANOMALY DETECTION · Z-SCORE EXTREMES"
        title="Scanner"
        meta={
          <div className="flex items-center gap-1.5">
            {/* Filter chip — names the actual threshold + cap so the
                user can read the truth at a glance.  Refusing to
                claim "your threshold" until V2 makes it configurable. */}
            <span className="inline-flex items-center rounded-full bg-white/[0.025] px-2 py-0.5 font-mono text-[10px] tracking-[0.02em] text-fg-muted ring-1 ring-line-soft">
              ≥ {SCANNER_THRESHOLD_SIGMA}σ · top {SCANNER_TOP_N}
            </span>
            <span
              className={cn(
                'inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium tracking-[0.02em]',
                flaggedCount > 0
                  ? 'bg-amber-400/[0.12] text-amber-300 ring-1 ring-amber-400/30'
                  : 'bg-mint-400/[0.10] text-mint-300 ring-1 ring-mint-400/25',
              )}
            >
              {flaggedCount} flagged
            </span>
          </div>
        }
      />
      <WidgetBody className="px-2.5 pb-2">
        {flaggedCount === 0 ? (
          <WidgetEmpty message="No instruments above the z-score threshold." />
        ) : (
          <div className="flex min-h-0 flex-1 flex-col">
            {/* Column headers */}
            <div className="grid grid-cols-[28px_1fr_56px_56px_50px] items-center gap-2 px-2.5 pb-1.5 pt-0.5">
              <span className="text-[9px] font-semibold uppercase tracking-[0.16em] text-fg-faint">
                #
              </span>
              <span className="text-[9px] font-semibold uppercase tracking-[0.16em] text-fg-faint">
                Instrument
              </span>
              <span className="text-[9px] font-semibold uppercase tracking-[0.16em] text-fg-faint text-right">
                Yield
              </span>
              <span className="text-[9px] font-semibold uppercase tracking-[0.16em] text-fg-faint text-right">
                Δ1d
              </span>
              <span className="text-[9px] font-semibold uppercase tracking-[0.16em] text-fg-faint text-right">
                z
              </span>
            </div>

            {scanner.results.slice(0, 8).map((r, idx) => {
              const zAbs = Math.abs(r.z_score ?? 0);
              const isHigh = r.signal === 'EXTREME_HIGH';
              const barWidth = Math.min(100, (zAbs / 3.5) * 100);
              const zColor =
                zAbs >= 2.5
                  ? 'text-coral-300'
                  : zAbs >= 2.0
                    ? 'text-amber-300'
                    : 'text-ice-200';

              return (
                <div
                  key={`${r.curve_family}-${r.tenor}`}
                  className={cn(
                    'group relative grid grid-cols-[28px_1fr_56px_56px_50px] items-center gap-2 px-2.5 py-2 transition-colors duration-150 hover:bg-white/[0.018]',
                    idx > 0 && 'border-t border-line-subtle/40',
                  )}
                >
                  {/* z-score magnitude bar */}
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
                    {String(r.rank).padStart(2, '0')}
                  </span>
                  <span className="relative truncate text-[12px] font-medium tracking-[-0.005em] text-fg-primary">
                    {CURVE_SHORT[r.curve_family] ?? r.curve_family} {r.tenor}
                  </span>
                  <span className="relative font-mono text-[11px] text-right tracking-[-0.005em] text-fg-primary">
                    {r.yield_pct?.toFixed(3) ?? '—'}
                  </span>
                  <span
                    className={cn(
                      'relative font-mono text-[11px] font-medium text-right tracking-[-0.005em]',
                      (r.daily_change_bps ?? 0) < 0
                        ? 'text-mint-300'
                        : 'text-coral-300',
                    )}
                  >
                    {r.daily_change_bps !== null
                      ? `${r.daily_change_bps > 0 ? '+' : ''}${r.daily_change_bps.toFixed(1)}`
                      : '—'}
                  </span>
                  <span
                    className={cn(
                      'relative font-mono text-[11px] font-semibold text-right tracking-[-0.005em]',
                      zColor,
                    )}
                  >
                    {r.z_score?.toFixed(2) ?? '—'}
                  </span>
                </div>
              );
            })}
          </div>
        )}
      </WidgetBody>
      <WidgetProvenance
        toolName="scan_extremes_tool"
        asOfDate={scanner.results[0]?.as_of_date ?? null}
      />
    </>
  );
}
