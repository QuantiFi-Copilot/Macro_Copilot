function PulseCard({ className = '' }: { className?: string }) {
  return (
    <div className={`card animate-pulse ${className}`}>
      <div className="h-full w-full rounded-[13px] bg-white/[0.012]" />
    </div>
  );
}

export function RatesPageSkeleton() {
  return (
    <div className="min-h-0 flex-1 overflow-hidden">
      <div className="mx-auto w-full max-w-[1680px] 3xl:max-w-[1880px] 4xl:max-w-[2160px] px-6 py-6 lg:px-8 lg:py-7 3xl:px-10">
        {/* Header skeleton */}
        <div className="mb-6 space-y-3">
          <div className="h-3 w-48 animate-pulse rounded bg-white/[0.03]" />
          <div className="h-6 w-[320px] animate-pulse rounded bg-white/[0.035]" />
          <div className="h-3 w-[280px] animate-pulse rounded bg-white/[0.02]" />
        </div>

        {/* Grid skeleton matching the rates page layout */}
        <div className="grid grid-cols-12 gap-4 lg:gap-5">
          {/* Yield snapshot — full width */}
          <div className="col-span-12">
            <PulseCard className="h-[340px]" />
          </div>

          {/* Curve shapes + Scanner */}
          <div className="col-span-12 xl:col-span-5">
            <PulseCard className="h-[300px]" />
          </div>
          <div className="col-span-12 xl:col-span-7">
            <PulseCard className="h-[300px]" />
          </div>

          {/* Cross-market + Regime */}
          <div className="col-span-12 xl:col-span-6">
            <PulseCard className="h-[340px]" />
          </div>
          <div className="col-span-12 xl:col-span-6">
            <PulseCard className="h-[340px]" />
          </div>
        </div>
      </div>
    </div>
  );
}
