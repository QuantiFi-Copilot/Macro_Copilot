// ============================================================================
// YieldSnapshotWidget — sovereign benchmark grid (curves × tenors)
// ----------------------------------------------------------------------------
// Pre-aggregated widget.  Reads from `useRatesDataContext` (single
// page-level fetch shared across pre-aggregated widgets).  Renders a
// dense table: curves down the left, tenors across the top, three
// metrics per cell (yield, daily change, z-score).
//
// Visual register matches the Ask page: clean monospace numerics,
// tabular-nums, conditional formatting via the data-token tones
// (mint for negative bps move = positive PnL on bonds; coral for
// positive bps = negative; amber/coral for extreme z-scores).
// ============================================================================

import type { YieldSnapshotResponse } from '@/types/rates';
import { useRatesDataContext } from '@/components/monitor/RatesDataProvider';
import {
  WidgetHeader,
  WidgetBody,
  WidgetProvenance,
} from '@/components/monitor/WidgetCard';
import { WidgetLoading, WidgetError } from './shared';
import { cn } from '@/utils/cn';

const CURVE_LABELS: Record<string, string> = {
  UST: 'UST',
  DE_BUND: 'Bund',
  UK_GILT: 'Gilt',
  JGB: 'JGB',
  FR_OAT: 'OAT',
  IT_BTP: 'BTP',
  ES_BONO: 'Bono',
  CANADA_GOVT: 'CAN',
  AU_GOVT: 'AUS',
};

export function YieldSnapshotWidget() {
  const { data, isLoading, error } = useRatesDataContext();

  if (error && !data) return <WidgetError message={error.message} />;
  if (!data || isLoading) return <WidgetLoading label="Loading yield snapshot…" />;

  const snapshot = data.yieldSnapshot;
  const asOf = snapshot.rows[0]?.as_of_date ?? null;

  return (
    <>
      <WidgetHeader
        kicker="SOVEREIGN BENCHMARKS"
        title="Yield Snapshot"
        meta={
          <span className="font-mono text-[10.5px] text-fg-muted">
            {snapshot.rows.length} cells
          </span>
        }
      />
      <WidgetBody className="overflow-x-auto px-5 pb-2">
        <SnapshotTable data={snapshot} />
      </WidgetBody>
      <WidgetProvenance
        toolName="get_yield_levels_tool · scan_extremes"
        asOfDate={asOf}
      />
    </>
  );
}

// ----------------------------------------------------------------------------

function SnapshotTable({ data }: { data: YieldSnapshotResponse }) {
  const displayCurves = [
    'UST',
    'DE_BUND',
    'UK_GILT',
    'JGB',
    'FR_OAT',
    'IT_BTP',
    'ES_BONO',
    'AU_GOVT',
    'CANADA_GOVT',
  ];
  const displayTenors = data.tenors;

  const lookup = new Map<string, (typeof data.rows)[number]>();
  for (const row of data.rows) {
    lookup.set(`${row.curve_family}:${row.tenor}`, row);
  }
  const curvesInData = displayCurves.filter((c) =>
    displayTenors.some((t) => lookup.has(`${c}:${t}`)),
  );

  return (
    <table className="w-full">
      <thead>
        <tr>
          <th className="sticky left-0 px-2 py-2 text-left">
            <span className="text-[9.5px] font-semibold uppercase tracking-[0.16em] text-fg-faint">
              Curve
            </span>
          </th>
          {displayTenors.map((t) => (
            <th key={t} colSpan={3} className="px-1 py-2 text-center">
              <span className="font-mono text-[10.5px] font-medium text-fg-secondary">
                {t}
              </span>
            </th>
          ))}
        </tr>
        <tr>
          <th className="sticky left-0 px-2" />
          {displayTenors.map((t) => (
            <th key={`${t}-sub`} colSpan={3} className="pb-1.5 pt-0.5">
              <div className="grid grid-cols-3 gap-0">
                <span className="text-[9px] uppercase tracking-[0.14em] text-fg-faint">
                  yld
                </span>
                <span className="text-[9px] uppercase tracking-[0.14em] text-fg-faint">
                  Δ1d
                </span>
                <span className="text-[9px] uppercase tracking-[0.14em] text-fg-faint">
                  z
                </span>
              </div>
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {curvesInData.map((curve, idx) => (
          <tr
            key={curve}
            className={cn(
              'transition-colors duration-150 hover:bg-white/[0.018]',
              idx > 0 && 'border-t border-line-subtle/50',
            )}
          >
            <td className="sticky left-0 px-2 py-2.5">
              <span className="text-[12px] font-medium tracking-[-0.005em] text-fg-primary">
                {CURVE_LABELS[curve] ?? curve}
              </span>
            </td>
            {displayTenors.map((t) => {
              const row = lookup.get(`${curve}:${t}`);
              return (
                <td key={`${curve}:${t}`} colSpan={3} className="px-1 py-2.5">
                  <div className="grid grid-cols-3 gap-0 text-center">
                    <span className="font-mono text-[11.5px] font-medium tracking-[-0.005em] text-fg-primary">
                      {formatYield(row?.yield_pct ?? null)}
                    </span>
                    <span
                      className={cn(
                        'font-mono text-[11px] font-medium tracking-[-0.005em]',
                        deltaToneClass(row?.daily_change_bps ?? null),
                      )}
                    >
                      {formatBps(row?.daily_change_bps ?? null)}
                    </span>
                    <span
                      className={cn(
                        'font-mono text-[11px] font-medium tracking-[-0.005em]',
                        zScoreToneClass(row?.z_score ?? null),
                      )}
                    >
                      {formatZ(row?.z_score ?? null)}
                    </span>
                  </div>
                </td>
              );
            })}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// ----------------------------------------------------------------------------
// Format / tone helpers — kept inline to keep the widget self-contained.

function formatYield(v: number | null): string {
  return v === null ? '—' : v.toFixed(3);
}

function formatBps(v: number | null): string {
  if (v === null) return '—';
  const sign = v > 0 ? '+' : '';
  return `${sign}${v.toFixed(1)}`;
}

function formatZ(v: number | null): string {
  if (v === null) return '—';
  const sign = v > 0 ? '+' : '';
  return `${sign}${v.toFixed(2)}`;
}

function deltaToneClass(v: number | null): string {
  if (v === null) return 'text-fg-muted';
  // For yields, lower bps = bond rally → mint (positive PnL connotation).
  if (v < 0) return 'text-mint-300';
  if (v > 0) return 'text-coral-300';
  return 'text-fg-muted';
}

function zScoreToneClass(z: number | null): string {
  if (z === null) return 'text-fg-muted';
  const abs = Math.abs(z);
  if (abs >= 2.0) return z > 0 ? 'text-coral-300' : 'text-mint-300';
  if (abs >= 1.5) return z > 0 ? 'text-amber-300' : 'text-ice-200';
  return 'text-fg-secondary';
}
