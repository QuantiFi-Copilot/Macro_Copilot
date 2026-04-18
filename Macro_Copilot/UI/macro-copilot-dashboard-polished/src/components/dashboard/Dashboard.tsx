import { ArrowUpRight, Clock4 } from 'lucide-react';
import type { DashboardData } from '@/types/dashboard';
import { RatesCard } from '@/components/dashboard/RatesCard';
import { FXCard } from '@/components/dashboard/FXCard';
import { EventMonitorCard } from '@/components/dashboard/EventMonitorCard';
import { MarketMoversTable } from '@/components/dashboard/MarketMoversTable';
import { MacroHighlights } from '@/components/dashboard/MacroHighlights';
import { FXMatrixCard } from '@/components/dashboard/FXMatrixCard';
import { DashboardSkeleton } from '@/components/dashboard/DashboardSkeleton';

type DashboardProps = {
  data: DashboardData | null;
  isLoading: boolean;
};

export function Dashboard({ data, isLoading }: DashboardProps) {
  if (isLoading || !data) {
    return <DashboardSkeleton />;
  }

  const today = new Date().toLocaleDateString('en-US', {
    weekday: 'long',
    month: 'short',
    day: 'numeric',
  });

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-[1680px] 3xl:max-w-[1880px] 4xl:max-w-[2160px] px-6 py-6 lg:px-8 lg:py-7 3xl:px-10">
        {/* Page header */}
        <div className="mb-6 flex items-end justify-between gap-6">
          <div>
            <div className="flex items-center gap-2 text-fg-muted">
              <Clock4 size={11} />
              <span className="text-[11px] font-medium uppercase tracking-[0.14em]">
                {today} · Daily Monitor
              </span>
            </div>
            <h1 className="mt-2 text-[22px] font-semibold tracking-[-0.018em] text-fg-primary 3xl:text-[24px]">
              Good morning, Sreeram.
            </h1>
            <p className="mt-1 text-[12.5px] text-fg-secondary">
              Three dislocations are above your 5D Z-score threshold. The US CPI analog is live.
            </p>
          </div>
          <div className="hidden items-center gap-2 md:flex">
            <button className="btn-ghost h-9">
              <span>Customize</span>
            </button>
            <button className="btn-primary h-9">
              <span>New analysis</span>
              <ArrowUpRight size={13} />
            </button>
          </div>
        </div>

        {/* Bento grid — fluid 12-col that scales from laptop to ultrawide */}
        <div className="grid grid-cols-12 gap-4 lg:gap-5">
          {/* Row 1 */}
          <div className="col-span-12 xl:col-span-5">
            <RatesCard data={data.ratesCard} />
          </div>
          <div className="col-span-12 md:col-span-6 xl:col-span-4">
            <FXCard data={data.fxCard} />
          </div>
          <div className="col-span-12 md:col-span-6 xl:col-span-3">
            <EventMonitorCard rows={data.events} />
          </div>

          {/* Row 2 */}
          <div className="col-span-12 xl:col-span-7">
            <MarketMoversTable rows={data.marketMovers} />
          </div>
          <div className="col-span-12 md:col-span-6 xl:col-span-5">
            <MacroHighlights items={data.highlights} />
          </div>

          {/* Row 3 — full-width RV strip */}
          <div className="col-span-12">
            <FXMatrixCard rows={data.fxMatrix} />
          </div>
        </div>
      </div>
    </div>
  );
}
