import { Clock4, RefreshCw } from 'lucide-react';
import { useRatesData } from '@/hooks/useRatesData';
import { YieldSnapshotGrid } from '@/components/rates/YieldSnapshotGrid';
import { CurveShapeStrip } from '@/components/rates/CurveShapeStrip';
import { ScannerCard } from '@/components/rates/ScannerCard';
import { CrossMarketCard } from '@/components/rates/CrossMarketCard';
import { RegimeMonitorCard } from '@/components/rates/RegimeMonitorCard';
import { RatesPageSkeleton } from '@/components/rates/RatesPageSkeleton';

export function RatesPage() {
  const { data, isLoading, error, refetch } = useRatesData();

  if (isLoading && !data) {
    return <RatesPageSkeleton />;
  }

  if (error && !data) {
    return (
      <div className="flex min-h-0 flex-1 items-center justify-center px-6">
        <div className="card max-w-md px-6 py-6">
          <p className="kicker mb-2">System</p>
          <p className="text-[15px] font-semibold text-fg-primary">
            Rates data failed to load
          </p>
          <p className="mt-2 text-[12px] text-fg-secondary">{error.message}</p>
          <button
            onClick={refetch}
            className="btn-ghost mt-4 h-9"
          >
            <RefreshCw size={13} />
            <span>Retry</span>
          </button>
        </div>
      </div>
    );
  }

  if (!data) return null;

  const asOf = data.yieldSnapshot.rows[0]?.as_of_date ?? '';
  const asOfFormatted = asOf
    ? new Date(asOf + 'T00:00:00').toLocaleDateString('en-US', {
        weekday: 'long',
        month: 'short',
        day: 'numeric',
        year: 'numeric',
      })
    : '';

  const extremeCount = data.scanner.results.length;

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-[1680px] 3xl:max-w-[1880px] 4xl:max-w-[2160px] px-6 py-6 lg:px-8 lg:py-7 3xl:px-10">
        {/* Page header */}
        <div className="mb-6 flex items-end justify-between gap-6">
          <div>
            <div className="flex items-center gap-2 text-fg-muted">
              <Clock4 size={11} />
              <span className="text-[11px] font-medium uppercase tracking-[0.14em]">
                {asOfFormatted} · Rates Morning Snapshot
              </span>
            </div>
            <h1 className="mt-2 text-[22px] font-semibold tracking-[-0.018em] text-fg-primary 3xl:text-[24px]">
              Sovereign Rates Monitor
            </h1>
            <p className="mt-1 text-[12.5px] text-fg-secondary">
              {extremeCount > 0
                ? `${extremeCount} instrument${extremeCount > 1 ? 's' : ''} flagged above z-score threshold.`
                : 'No statistical extremes detected today.'}
              {' '}All metrics are deterministic — computed from TimescaleDB.
            </p>
          </div>
          <div className="hidden items-center gap-2 md:flex">
            <button onClick={refetch} className="btn-ghost h-9">
              <RefreshCw size={13} className={isLoading ? 'animate-spin' : ''} />
              <span>Refresh</span>
            </button>
          </div>
        </div>

        {/* Bento grid */}
        <div className="grid grid-cols-12 gap-4 lg:gap-5">
          {/* Row 1: Yield Snapshot — full width */}
          <div className="col-span-12">
            <YieldSnapshotGrid data={data.yieldSnapshot} />
          </div>

          {/* Row 2: Curve Shapes + Scanner */}
          <div className="col-span-12 xl:col-span-5">
            <CurveShapeStrip data={data.curveShapes} />
          </div>
          <div className="col-span-12 xl:col-span-7">
            <ScannerCard data={data.scanner} />
          </div>

          {/* Row 3: Cross-Market + Regime */}
          <div className="col-span-12 xl:col-span-6">
            <CrossMarketCard data={data.crossMarket} />
          </div>
          <div className="col-span-12 xl:col-span-6">
            <RegimeMonitorCard data={data.regimes} />
          </div>
        </div>
      </div>
    </div>
  );
}
