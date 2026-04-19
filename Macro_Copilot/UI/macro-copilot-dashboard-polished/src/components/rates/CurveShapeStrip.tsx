import { TrendingUp, MoreHorizontal } from 'lucide-react';
import type { CurveShapesResponse } from '@/types/rates';
import { TerminalCard } from '@/components/ui/TerminalCard';
import { Sparkline } from '@/components/ui/Sparkline';
import { cn } from '@/utils/cn';

type CurveShapeStripProps = {
  data: CurveShapesResponse;
};

const CURVE_SHORT: Record<string, string> = {
  UST: 'UST', DE_BUND: 'Bund', UK_GILT: 'Gilt', JGB: 'JGB',
  FR_OAT: 'OAT', IT_BTP: 'BTP',
};

export function CurveShapeStrip({ data }: CurveShapeStripProps) {
  return (
    <TerminalCard
      kicker="Curve Shape · 2s10s"
      title="Slope Monitor"
      icon={<TrendingUp size={12} />}
      action={
        <button className="rounded-md p-1 text-fg-muted transition-colors hover:bg-white/[0.04] hover:text-fg-secondary">
          <MoreHorizontal size={14} />
        </button>
      }
      className="h-full"
    >
      <div className="flex min-w-0 flex-1 flex-col gap-2">
        {data.curves.map((curve) => {
          const isNeg = (curve.daily_change_bps ?? 0) < 0;
          const zAbs = Math.abs(curve.z_score ?? 0);
          const zColor =
            zAbs >= 2.0
              ? (curve.z_score ?? 0) > 0 ? 'text-coral-400' : 'text-mint-400'
              : zAbs >= 1.5
                ? 'text-amber-400'
                : 'text-fg-secondary';

          return (
            <div
              key={curve.curve_family}
              className="group grid grid-cols-[80px_1fr_100px] items-center gap-3 rounded-lg border border-line-subtle bg-white/[0.008] px-3.5 py-3 transition-colors duration-150 ease-sleek hover:border-line-strong hover:bg-white/[0.02]"
            >
              {/* Label + spread */}
              <div className="min-w-0">
                <div className="text-[12px] font-semibold text-fg-primary">
                  {CURVE_SHORT[curve.curve_family] ?? curve.curve_family}
                </div>
                <div className="mt-0.5 text-[10px] text-fg-muted">{curve.spread_label}</div>
              </div>

              {/* Sparkline */}
              <div className="h-9">
                <Sparkline
                  data={curve.sparkline}
                  tone="rates"
                  mode="area"
                  height={36}
                  strokeWidth={1.2}
                />
              </div>

              {/* Metrics */}
              <div className="flex flex-col items-end gap-0.5">
                <div className="flex items-center gap-1.5">
                  <span className="mono text-[13px] font-semibold text-fg-primary">
                    {curve.spread_bps?.toFixed(1) ?? '—'}
                  </span>
                  <span className={cn(
                    'mono text-[10px] font-semibold',
                    isNeg ? 'text-mint-400' : 'text-coral-400',
                  )}>
                    {curve.daily_change_bps !== null
                      ? `${curve.daily_change_bps > 0 ? '+' : ''}${curve.daily_change_bps.toFixed(1)}`
                      : '—'}
                  </span>
                </div>
                <span className={cn('mono text-[10px] font-medium', zColor)}>
                  z {curve.z_score?.toFixed(2) ?? '—'}
                </span>
              </div>
            </div>
          );
        })}
      </div>
    </TerminalCard>
  );
}
