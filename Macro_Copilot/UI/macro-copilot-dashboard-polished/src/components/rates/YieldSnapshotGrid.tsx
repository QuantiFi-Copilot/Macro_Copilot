import { Layers, MoreHorizontal } from 'lucide-react';
import type { YieldSnapshotResponse } from '@/types/rates';
import { TerminalCard } from '@/components/ui/TerminalCard';
import { cn } from '@/utils/cn';

type YieldSnapshotGridProps = {
  data: YieldSnapshotResponse;
};

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

function formatBps(val: number | null): string {
  if (val === null) return '—';
  const sign = val > 0 ? '+' : '';
  return `${sign}${val.toFixed(1)}`;
}

function formatYield(val: number | null): string {
  if (val === null) return '—';
  return val.toFixed(3);
}

function formatZ(val: number | null): string {
  if (val === null) return '—';
  const sign = val > 0 ? '+' : '';
  return `${sign}${val.toFixed(2)}`;
}

function zScoreColor(z: number | null): string {
  if (z === null) return 'text-fg-muted';
  const abs = Math.abs(z);
  if (abs >= 2.0) return z > 0 ? 'text-coral-400' : 'text-mint-400';
  if (abs >= 1.5) return z > 0 ? 'text-amber-400' : 'text-ice-300';
  return 'text-fg-secondary';
}

function changeColor(val: number | null): string {
  if (val === null) return 'text-fg-muted';
  if (val > 0) return 'text-coral-400';
  if (val < 0) return 'text-mint-400';
  return 'text-fg-muted';
}

export function YieldSnapshotGrid({ data }: YieldSnapshotGridProps) {
  // Show a curated set of curves for the main grid
  const displayCurves = ['UST', 'DE_BUND', 'UK_GILT', 'JGB', 'FR_OAT', 'IT_BTP', 'ES_BONO', 'AU_GOVT', 'CANADA_GOVT'];
  const displayTenors = data.tenors;

  // Build a lookup map for fast cell rendering
  const lookup = new Map<string, (typeof data.rows)[0]>();
  for (const row of data.rows) {
    lookup.set(`${row.curve_family}:${row.tenor}`, row);
  }

  const curvesInData = displayCurves.filter((c) =>
    displayTenors.some((t) => lookup.has(`${c}:${t}`)),
  );

  const asOf = data.rows[0]?.as_of_date ?? '';

  return (
    <TerminalCard
      kicker="Sovereign Benchmarks"
      title="Yield Snapshot"
      icon={<Layers size={12} />}
      action={
        <>
          <span className="chip mono">{asOf}</span>
          <button className="rounded-md p-1 text-fg-muted transition-colors hover:bg-white/[0.04] hover:text-fg-secondary">
            <MoreHorizontal size={14} />
          </button>
        </>
      }
      bodyClassName="!p-0"
    >
      <div className="overflow-x-auto">
        <table className="w-full text-[11.5px]">
          <thead>
            <tr className="border-b border-line-subtle">
              <th className="sticky left-0 z-10 bg-[var(--card-bg,#0d0f17)] px-4 py-2.5 text-left">
                <span className="kicker">Curve</span>
              </th>
              {displayTenors.map((tenor) => (
                <th key={tenor} colSpan={3} className="px-1 py-2.5 text-center">
                  <span className="kicker">{tenor}</span>
                </th>
              ))}
            </tr>
            <tr className="border-b border-line-subtle/50">
              <th className="sticky left-0 z-10 bg-[var(--card-bg,#0d0f17)] px-4 py-1.5" />
              {displayTenors.map((tenor) => (
                <th key={`${tenor}-sub`} colSpan={3} className="px-1 py-1.5">
                  <div className="grid grid-cols-3 gap-0">
                    <span className="kicker text-center text-fg-faint">Yield</span>
                    <span className="kicker text-center text-fg-faint">Δ1D</span>
                    <span className="kicker text-center text-fg-faint">Z</span>
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
                  'transition-colors duration-100 hover:bg-white/[0.02]',
                  idx < curvesInData.length - 1 && 'border-b border-line-subtle/30',
                )}
              >
                <td className="sticky left-0 z-10 bg-[var(--card-bg,#0d0f17)] px-4 py-2.5">
                  <div className="flex items-center gap-2">
                    <span className="flex h-5 w-7 items-center justify-center rounded border border-line-soft bg-white/[0.02] text-[9px] font-bold text-fg-secondary">
                      {(CURVE_LABELS[curve] ?? curve).slice(0, 3).toUpperCase()}
                    </span>
                    <span className="text-[11.5px] font-medium text-fg-primary">
                      {CURVE_LABELS[curve] ?? curve}
                    </span>
                  </div>
                </td>
                {displayTenors.map((tenor) => {
                  const row = lookup.get(`${curve}:${tenor}`);
                  return (
                    <td key={`${curve}:${tenor}`} colSpan={3} className="px-1 py-2.5">
                      <div className="grid grid-cols-3 gap-0 text-center">
                        <span className="mono text-[11px] font-medium text-fg-primary">
                          {formatYield(row?.yield_pct ?? null)}
                        </span>
                        <span className={cn('mono text-[11px] font-semibold', changeColor(row?.daily_change_bps ?? null))}>
                          {formatBps(row?.daily_change_bps ?? null)}
                        </span>
                        <span className={cn('mono text-[11px] font-semibold', zScoreColor(row?.z_score ?? null))}>
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
      </div>
    </TerminalCard>
  );
}
