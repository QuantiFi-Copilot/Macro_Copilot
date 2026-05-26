// ============================================================================
// CurveSpreadsWidget — 2s10s spread monitor across G4 curves
// ----------------------------------------------------------------------------
// Pre-aggregated.  Surface for `calculate_curve_spread_tool` evaluated
// across G4 (UST, Bund, Gilt, JGB) at 2s10s.  Renders one row per
// curve: label · sparkline · current spread + Δ1d + z-score.
//
// Was named "CurveShapesWidget" with title "Slope Monitor" in V0.
// Renamed to mirror the backing tool's name (`calculate_curve_spread_tool`).
// ============================================================================

import { useRatesDataContext } from '@/components/monitor/RatesDataProvider';
import {
  WidgetHeader,
  WidgetBody,
  WidgetProvenance,
} from '@/components/monitor/WidgetCard';
import { Sparkline } from '@/components/ui/Sparkline';
import { WidgetLoading, WidgetError } from '@/components/monitor/widgets/shared';
import { cn } from '@/utils/cn';

const CURVE_LABEL: Record<string, string> = {
  UST: 'UST',
  DE_BUND: 'Bund',
  UK_GILT: 'Gilt',
  JGB: 'JGB',
  FR_OAT: 'OAT',
  IT_BTP: 'BTP',
};

export function CurveSpreadsWidget() {
  const { data, isLoading, error } = useRatesDataContext();

  if (error && !data) return <WidgetError message={error.message} />;
  if (!data || isLoading)
    return <WidgetLoading label="Loading curve spreads…" />;

  const curves = data.curveShapes.curves;
  const asOf = curves[0]?.as_of_date ?? null;

  return (
    <>
      <WidgetHeader
        kicker="CALCULATE_CURVE_SPREAD · 2s10s"
        title="Curve Spreads"
        meta={
          <span className="font-mono text-[10.5px] text-fg-muted">
            {curves.length} curves
          </span>
        }
      />
      <WidgetBody className="space-y-1.5 px-3.5 pb-3">
        {curves.map((curve) => {
          const dailyNeg = (curve.daily_change_bps ?? 0) < 0;
          const zAbs = Math.abs(curve.z_score ?? 0);
          const zToneClass =
            zAbs >= 2.0
              ? (curve.z_score ?? 0) > 0
                ? 'text-coral-300'
                : 'text-mint-300'
              : zAbs >= 1.5
                ? 'text-amber-300'
                : 'text-fg-secondary';

          return (
            <div
              key={curve.curve_family}
              className="grid grid-cols-[60px_1fr_92px] items-center gap-3 rounded-[10px] bg-white/[0.012] px-3 py-2.5 ring-1 ring-line-subtle"
            >
              <div className="min-w-0">
                <div className="text-[12.5px] font-medium tracking-[-0.005em] text-fg-primary">
                  {CURVE_LABEL[curve.curve_family] ?? curve.curve_family}
                </div>
                <div className="mt-0.5 font-mono text-[9.5px] text-fg-faint">
                  {curve.spread_label}
                </div>
              </div>
              <div className="h-9 min-w-0">
                <Sparkline
                  data={curve.sparkline}
                  tone="rates"
                  mode="area"
                  height={36}
                  strokeWidth={1.2}
                />
              </div>
              <div className="flex flex-col items-end gap-0.5">
                <div className="flex items-baseline gap-1.5">
                  <span className="font-mono text-[13px] font-medium tracking-[-0.005em] text-fg-primary">
                    {curve.spread_bps?.toFixed(1) ?? '—'}
                  </span>
                  <span
                    className={cn(
                      'font-mono text-[10px] font-medium',
                      dailyNeg ? 'text-mint-300' : 'text-coral-300',
                    )}
                  >
                    {curve.daily_change_bps !== null
                      ? `${curve.daily_change_bps > 0 ? '+' : ''}${curve.daily_change_bps.toFixed(1)}`
                      : '—'}
                  </span>
                </div>
                <span className={cn('font-mono text-[9.5px] font-medium', zToneClass)}>
                  z {curve.z_score?.toFixed(2) ?? '—'}
                </span>
              </div>
            </div>
          );
        })}
      </WidgetBody>
      <WidgetProvenance
        toolName="calculate_curve_spread_tool"
        asOfDate={asOf}
      />
    </>
  );
}
