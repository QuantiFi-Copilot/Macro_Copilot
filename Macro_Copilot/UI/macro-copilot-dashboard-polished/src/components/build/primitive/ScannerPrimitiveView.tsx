// ============================================================================
// ScannerPrimitiveView — view for ``scan_extremes_tool``.
// ----------------------------------------------------------------------------
// Ranked table of |z| extremes across sovereigns + tenors.  Rows are
// click-targets that deep-link to a yield primitive view.  Reshell of the
// legacy ScannerView onto PrimitiveCanvasShell (phase R2).
// ============================================================================

import { useNavigate } from 'react-router-dom';
import type { ScannerResponse } from '@/types/rates';
import { cn } from '@/utils/cn';
import { formatSigned } from './PrimitiveMetrics';
import { PrimitiveCanvasShell, ToolNameChip } from './PrimitiveCanvasShell';

type Props = {
  payload: ScannerResponse;
};

function changeToneClass(value: number | null): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return 'text-fg-muted';
  }
  if (value > 0) return 'text-mint-300';
  if (value < 0) return 'text-coral-300';
  return 'text-fg-muted';
}

function zScoreToneClass(value: number | null): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return 'text-fg-muted';
  }
  const abs = Math.abs(value);
  if (abs > 2) return 'text-coral-300';
  if (abs > 1.5) return 'text-amber-300';
  return 'text-fg-primary';
}

function signalBadgeClass(signal: string): string {
  const s = signal.toLowerCase();
  if (s.includes('rich') || s.includes('over') || s.includes('high')) {
    return 'border-coral-400/30 bg-coral-500/10 text-coral-300';
  }
  if (s.includes('cheap') || s.includes('under') || s.includes('low')) {
    return 'border-mint-400/30 bg-mint-500/10 text-mint-300';
  }
  return 'border-line-soft bg-white/[0.03] text-fg-secondary';
}

export function ScannerPrimitiveView({ payload }: Props) {
  const navigate = useNavigate();
  const { summary, results } = payload;
  const asOfDate = results[0]?.as_of_date ?? null;

  return (
    <PrimitiveCanvasShell
      kicker="Primitive · Scanner"
      title="Z-score extremes"
      subtitle="Largest |z| moves across sovereign curves and tenors today."
      asOfDate={asOfDate}
      meta={<ToolNameChip tool="scan_extremes_tool" />}
      methodology={
        <p>
          For each (curve, tenor), z = (current − rolling-mean) / rolling-σ
          over the trailing window.  Results are ranked by |z| descending,
          capped at the top N.  "Rich / Cheap" tags are derived from the
          sign of z relative to recent history.
        </p>
      }
    >
      {summary ? (
        <section className="research-card px-5 py-4">
          <div className="kicker mb-2 text-fg-muted">Summary</div>
          <p className="text-[12.5px] leading-relaxed text-fg-secondary">{summary}</p>
        </section>
      ) : null}

      <section className="research-card overflow-hidden">
        <div className="flex items-center justify-between border-b border-line-subtle px-5 py-3">
          <div className="kicker text-fg-muted">Scanner Results</div>
          <div className="font-mono text-[10px] text-fg-faint">
            {results.length} {results.length === 1 ? 'row' : 'rows'}
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="mono w-full min-w-[720px] text-[11.5px] tabular-nums">
            <thead>
              <tr className="border-b border-line-subtle text-[10px] uppercase tracking-[0.06em] text-fg-faint">
                <th className="px-4 py-2 text-left font-normal">#</th>
                <th className="px-4 py-2 text-left font-normal">Curve</th>
                <th className="px-4 py-2 text-left font-normal">Tenor</th>
                <th className="px-4 py-2 text-right font-normal">Yield</th>
                <th className="px-4 py-2 text-right font-normal">Daily Δ</th>
                <th className="px-4 py-2 text-right font-normal">Z-score</th>
                <th className="px-4 py-2 text-right font-normal">252d %ile</th>
                <th className="px-4 py-2 text-left font-normal">Signal</th>
                <th className="px-4 py-2 text-left font-normal">As of</th>
              </tr>
            </thead>
            <tbody>
              {results.map((row) => {
                // Synthesise a ``?context=`` URL pointing at the matching
                // yield primitive so the row click materialises into a
                // virtual primitive canvas in Build.
                const ctx = encodeURIComponent(
                  JSON.stringify({
                    tools: [
                      {
                        tool: 'get_yield_levels_tool',
                        params: {
                          curve_family: row.curve_family,
                          tenor: row.tenor,
                        },
                      },
                    ],
                    tool_count: 1,
                  }),
                );
                const target = `/workspace?context=${ctx}`;
                return (
                  <tr
                    key={`${row.rank}-${row.curve_family}-${row.tenor}`}
                    onClick={() => navigate(target)}
                    className="cursor-pointer border-b border-line-subtle/50 transition-colors hover:bg-white/[0.02]"
                  >
                    <td className="px-4 py-2.5 text-fg-faint">{row.rank}</td>
                    <td className="px-4 py-2.5 text-fg-primary">{row.curve_family}</td>
                    <td className="px-4 py-2.5 text-fg-secondary">{row.tenor}</td>
                    <td className="px-4 py-2.5 text-right text-fg-primary">
                      {row.yield_pct?.toFixed(3) ?? '—'}
                    </td>
                    <td
                      className={cn(
                        'px-4 py-2.5 text-right',
                        changeToneClass(row.daily_change_bps),
                      )}
                    >
                      {formatSigned(row.daily_change_bps, 1)}
                    </td>
                    <td className={cn('px-4 py-2.5 text-right font-semibold', zScoreToneClass(row.z_score))}>
                      {row.z_score?.toFixed(2) ?? '—'}
                    </td>
                    <td className="px-4 py-2.5 text-right text-fg-secondary">
                      {row.percentile_252d != null
                        ? `${row.percentile_252d.toFixed(0)}%`
                        : '—'}
                    </td>
                    <td className="px-4 py-2.5">
                      <span
                        className={cn(
                          'inline-flex items-center rounded-md border px-1.5 py-0.5 text-[10px] uppercase tracking-[0.04em]',
                          signalBadgeClass(row.signal),
                        )}
                      >
                        {row.signal}
                      </span>
                    </td>
                    <td className="px-4 py-2.5 text-fg-faint">{row.as_of_date ?? '—'}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>
    </PrimitiveCanvasShell>
  );
}
