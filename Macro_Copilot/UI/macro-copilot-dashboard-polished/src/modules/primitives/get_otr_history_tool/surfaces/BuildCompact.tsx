// ============================================================================
// surfaces/BuildCompact.tsx — Compact Build view for ``get_otr_history_tool``.
// CATEGORICAL SCD2 TIMELINE shape.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.2 every primitive claiming
// ``custom_build_surface`` ships a compact view; this one is a KPI row +
// RECENT-WINDOWS TABLE (NOT a sparkline) per the same semantic-contract
// guardrail the scanner compacts document: the wire carries dated
// identifier rows, no numeric series — a chart would fabricate one.
// Mounted as a node body inside multi-tool DAG visualisations.
//
// Layout precedent: ../../scan_ois_extremes_tool/surfaces/BuildCompact.tsx
// (the table-shaped compact family) — shared ``FreshnessPill`` + the same
// header / hr / footer skeleton so visual treatment matches the rest of
// the compact catalogue.  The headline KPIs come from the shared
// ``compactKPIs`` builder (current OTR / transitions / current-since).
// ============================================================================

import { ArrowUpRight, History, Info } from 'lucide-react';
import {
  FreshnessPill,
  type BuildCompactProps,
} from '@/components/shared/build';
import {
  OTR_HISTORY_COMPACT_CAVEAT,
  compactKPIs,
  formatShortDate,
  formatWindowEnd,
  otrCountryMetaFor,
  sortTransitionsForDisplay,
  useOtrHistoryData,
} from './otrHistoryShared';

// Number of SCD2 windows the compact card surfaces.  Mirrors the scanner
// compacts' top-row convention; the full log lives on the extended view
// behind the expand affordance.
const COMPACT_TOP_ROWS = 4;

const OTR_COMPACT_DEFAULTS = { country: 'US', tenor: '10Y', lookback: '365' };

const BuildCompact: React.FC<BuildCompactProps> = ({
  params,
  size = 'small',
  onExpand,
  callMeta,
}) => {
  const country = params.country || OTR_COMPACT_DEFAULTS.country;
  const tenor = params.tenor || OTR_COMPACT_DEFAULTS.tenor;
  const lookbackStr = params.lookback_days || OTR_COMPACT_DEFAULTS.lookback;
  const lookbackDays = Number(lookbackStr);

  const { data, isLoading, errorMessage } = useOtrHistoryData({
    country,
    tenor,
    lookbackDays: Number.isFinite(lookbackDays) ? lookbackDays : undefined,
  });

  const cm = data?.current_metrics ?? null;
  const meta = otrCountryMetaFor(country);
  const rows = data ? sortTransitionsForDisplay(data.transitions) : [];
  const visibleRows = rows.slice(0, COMPACT_TOP_ROWS);
  const totalAvailable = rows.length;

  const minHeight = size === 'medium' ? 420 : 340;

  return (
    <article
      className="card flex flex-col gap-3 overflow-hidden bg-bg-elevated px-5 py-4"
      style={{ minHeight }}
      data-testid="otr-history-compact"
    >
      {/* ---------- Header ---------- */}
      <header className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 flex-1 items-start gap-2.5">
          <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md border border-line-subtle bg-surface-overlay">
            <History size={12} strokeWidth={1.75} className="text-ice-300" aria-hidden />
          </span>
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-[13px] font-semibold text-fg-primary">
              <span className="truncate">
                {meta && (
                  <span className="mr-1" aria-hidden>
                    {meta.flag}
                  </span>
                )}
                OTR {country} {tenor}
              </span>
              <span className="text-fg-faint" aria-hidden>·</span>
              <span className="kicker text-fg-muted">SCD2 LOG</span>
            </div>
            <div className="mt-0.5 flex items-center gap-1.5 text-[12px] text-fg-secondary">
              <span>{meta ? meta.issuerLabel : country}</span>
              <span className="text-fg-faint" aria-hidden>·</span>
              <span className="font-mono">{lookbackStr}d</span>
              {callMeta && (
                <span className="ml-1 kicker text-fg-faint">
                  · call {callMeta.n} of {callMeta.m}
                </span>
              )}
            </div>
          </div>
        </div>
        {onExpand && (
          <button
            type="button"
            onClick={onExpand}
            aria-label="Open extended view of the OTR transition log"
            title="Open extended view"
            className="rounded-md border border-line-subtle bg-surface-overlay p-1.5 text-fg-muted transition-colors hover:border-line-strong hover:text-fg-primary"
          >
            <ArrowUpRight size={14} strokeWidth={1.75} />
          </button>
        )}
      </header>

      <hr className="border-t border-line-subtle" />

      {/* ---------- Body ---------- */}
      {errorMessage ? (
        <CompactError message={errorMessage} />
      ) : isLoading || !data ? (
        <CompactSkeleton />
      ) : (
        <div className="flex min-h-0 flex-1 flex-col gap-3">
          {/* Headline KPIs — current OTR identity / transition count /
              current-since (shared compactKPIs builder). */}
          <div className="grid grid-cols-3 gap-3">
            {compactKPIs(data).map((kpi) => (
              <div key={kpi.label} className="flex min-w-0 flex-col gap-0.5">
                <span className="kicker text-fg-muted">{kpi.label}</span>
                <span
                  className={`truncate font-mono tabular-nums text-fg-primary ${kpi.emphasis === 'primary' ? 'text-[15px] font-semibold' : 'text-[13.5px]'}`}
                  title={kpi.value}
                >
                  {kpi.value}
                </span>
                {kpi.caption && (
                  <span className="text-[10.5px] text-fg-faint">
                    {kpi.caption}
                  </span>
                )}
              </div>
            ))}
          </div>

          {/* Recent windows table — dated identifier rows, no chart (the
              wire has no numeric series to draw). */}
          <div className="flex flex-col">
            <div className="grid grid-cols-[1.3fr_auto_1fr] items-center gap-2 px-1 text-[10.5px] uppercase tracking-wide text-fg-muted">
              <span>Window</span>
              <span />
              <span className="text-right">Identifier</span>
            </div>
            {visibleRows.length === 0 ? (
              <div className="px-1 py-3 text-[12px] text-fg-secondary">
                No resolver-observed windows in this lookback.
              </div>
            ) : (
              visibleRows.map((row) => {
                const isOpen = row.effective_to == null;
                return (
                  <div
                    key={`${row.otr_instrument_id}-${row.effective_from}`}
                    className="grid grid-cols-[1.3fr_auto_1fr] items-center gap-2 border-t border-line-subtle px-1 py-1.5 text-[12px]"
                  >
                    <span className="font-mono tabular-nums text-fg-primary">
                      {formatShortDate(row.effective_from)}
                      <span className="mx-1 text-fg-faint">→</span>
                      {row.effective_to
                        ? formatShortDate(row.effective_to)
                        : formatWindowEnd(row.effective_to)}
                    </span>
                    <span>
                      {isOpen && (
                        <span className="rounded-full border border-mint-300/40 bg-mint-300/10 px-1.5 py-0.5 text-[9.5px] font-semibold uppercase tracking-wide text-mint-300">
                          Current
                        </span>
                      )}
                    </span>
                    <span
                      className="truncate text-right font-mono text-fg-secondary"
                      title={row.cusip ?? row.isin ?? row.vendor_ticker ?? undefined}
                    >
                      {row.cusip ?? row.isin ?? row.vendor_ticker ?? '—'}
                    </span>
                  </div>
                );
              })
            )}
          </div>
        </div>
      )}

      {/* ---------- Footer ---------- */}
      <hr className="border-t border-line-subtle" />
      <footer className="flex items-center justify-between gap-3 text-[11px]">
        <div
          className="flex min-w-0 flex-1 items-center gap-1.5 text-fg-secondary"
          title={OTR_HISTORY_COMPACT_CAVEAT}
        >
          <Info size={12} strokeWidth={1.5} className="shrink-0 text-fg-muted" aria-hidden />
          <span className="truncate">{OTR_HISTORY_COMPACT_CAVEAT}</span>
        </div>
        <div className="flex shrink-0 items-center gap-3 text-fg-muted">
          {onExpand && totalAvailable > visibleRows.length && (
            <button
              type="button"
              onClick={onExpand}
              className="text-ice-300 underline-offset-2 hover:underline"
            >
              View all {totalAvailable} →
            </button>
          )}
          {cm && <span>As of {cm.as_of_date}</span>}
          <FreshnessPill freshness="fresh" />
        </div>
      </footer>
    </article>
  );
};

export default BuildCompact;

// ---------------------------------------------------------------------------
// Skeleton + error states
// ---------------------------------------------------------------------------

function CompactSkeleton() {
  return (
    <div className="flex min-h-[180px] flex-1 flex-col gap-3">
      <div className="grid grid-cols-3 gap-3">
        {[0, 1, 2].map((i) => (
          <div key={i} className="flex flex-col gap-1.5">
            <div className="h-2 w-16 animate-pulse rounded bg-line-subtle" />
            <div className="h-4 w-20 animate-pulse rounded bg-line-subtle" />
          </div>
        ))}
      </div>
      {[0, 1, 2, 3].map((i) => (
        <div
          key={i}
          className="grid grid-cols-[1.3fr_auto_1fr] items-center gap-2 border-t border-line-subtle px-1 py-1.5"
        >
          <div className="h-3 w-28 animate-pulse rounded bg-line-subtle" />
          <div className="h-3 w-10 animate-pulse rounded bg-line-subtle" />
          <div className="h-3 w-20 animate-pulse rounded bg-line-subtle justify-self-end" />
        </div>
      ))}
    </div>
  );
}

function CompactError({ message }: { message: string }) {
  return (
    <div className="flex min-h-[180px] flex-1 items-center justify-center px-2">
      <p className="text-center text-[11.5px] leading-[1.5] text-coral-300">
        {message}
      </p>
    </div>
  );
}
