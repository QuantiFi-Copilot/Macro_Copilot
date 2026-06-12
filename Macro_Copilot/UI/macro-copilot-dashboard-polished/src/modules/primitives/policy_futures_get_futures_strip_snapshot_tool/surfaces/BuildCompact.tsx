// ============================================================================
// surfaces/BuildCompact.tsx — Compact Build view for
// ``policy_futures_get_futures_strip_snapshot_tool``.  WHOLE-STRIP
// SNAPSHOT shape (table-shaped compact).
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.2 every primitive claiming
// ``custom_build_surface`` ships a compact view; this one is 3 KPIs +
// a compact ROW LIST of the first 4 strip positions (NOT a sparkline)
// per the scan_extremes_tool table-shaped guardrail: the tool's
// canonical headline data is the CROSS-SECTIONAL strip (one row per
// position on one aligned date) — there is no time series on the wire,
// and rendering an empty chart would read as missing data rather than
// snapshot-by-contract.  This component honours the SEMANTIC contract
// of §2.2 (identity / headline data / methodology caveat / expand
// affordance / tone cues) with a strip-shaped layout, reusing the
// shared FreshnessPill / InfoTooltip / tone helpers so the visual
// treatment matches the rest of the compact catalogue.  THESIS Q3
// documents the deviation.
// ============================================================================

import { ArrowUpRight, Rows3 } from 'lucide-react';
import {
  FreshnessPill,
  InfoTooltip,
  signedFixed,
  toneForChange,
  toneForZScore,
  toneTextClass,
  type BuildCompactProps,
  type KPIDescriptor,
} from '@/components/shared/build';
import {
  COMPACT_KPI_PLACEHOLDERS,
  STRIP_SNAPSHOT_COMPACT_CAVEAT,
  compactKPIs,
  curveMetaFor,
  dailyChangeBps,
  useStripSnapshot,
} from './policyFuturesStripSnapshotShared';

// Number of strip rows the compact card surfaces (the whites).  The
// full position set is one tap away via the expand affordance.
const COMPACT_ROWS = 4;

const BuildCompact: React.FC<BuildCompactProps> = ({
  params,
  size = 'small',
  onExpand,
  callMeta,
}) => {
  // Record<string,string> param parsing at the boundary.
  const curveFamily = params.curve_family ?? '';
  const asOfDate = params.as_of_date || undefined;
  const priceField = params.last_price_field_name || undefined;
  const oiField = params.open_interest_field_name || undefined;

  const { data, isLoading, errorMessage } = useStripSnapshot({
    curveFamily,
    asOfDate,
    lastPriceFieldName: priceField,
    openInterestFieldName: oiField,
  });

  const meta = curveMetaFor(data?.curve_family ?? curveFamily);
  const identity = meta
    ? `${meta.shortLabel.toUpperCase()} STRIP`
    : `${curveFamily || '—'} STRIP`;
  const kpis = data ? compactKPIs(data) : COMPACT_KPI_PLACEHOLDERS;
  const visibleRows = (data?.snapshot ?? []).slice(0, COMPACT_ROWS);
  const totalRows = data?.snapshot.length ?? 0;

  const minHeight = size === 'medium' ? 400 : 330;

  return (
    <article
      className="card flex flex-col gap-3 overflow-hidden bg-bg-elevated px-5 py-4"
      style={{ minHeight }}
      data-testid="strip-snapshot-compact"
    >
      {/* ---------- Header ---------- */}
      <header className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 flex-1 items-start gap-2.5">
          <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md border border-line-subtle bg-surface-overlay">
            <Rows3 size={12} strokeWidth={1.75} className="text-ice-300" aria-hidden />
          </span>
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-[13px] font-semibold text-fg-primary">
              {meta?.flag && <span className="text-[14px] leading-none">{meta.flag}</span>}
              <span className="truncate">{identity}</span>
              <span className="text-fg-faint" aria-hidden>·</span>
              <span className="kicker text-fg-muted">SNAPSHOT</span>
            </div>
            <div className="mt-0.5 flex items-center gap-1.5 text-[12px] text-fg-secondary">
              <span>{meta?.longLabel ?? 'Policy-futures whole strip'}</span>
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
            aria-label={`Open extended view of ${identity}`}
            title="Open extended view"
            className="rounded-md border border-line-subtle bg-surface-overlay p-1.5 text-fg-muted transition-colors hover:border-line-strong hover:text-fg-primary"
          >
            <ArrowUpRight size={14} strokeWidth={1.75} />
          </button>
        )}
      </header>

      <hr className="border-t border-line-subtle" />

      {/* ---------- Body: 3 KPIs + first-4-positions row list ---------- */}
      {errorMessage ? (
        <CompactError message={errorMessage} />
      ) : isLoading ? (
        <CompactSkeleton />
      ) : (
        <div className="flex min-h-0 flex-1 flex-col justify-between gap-3">
          <div className="grid grid-cols-3 gap-x-4">
            {kpis.map((k) => (
              <KpiCell key={k.label} kpi={k} />
            ))}
          </div>

          {/* Compact row list — first 4 positions (whites) */}
          <div className="flex flex-col">
            <div className="grid grid-cols-[40px_minmax(0,1fr)_auto_auto_auto] items-center gap-x-3 px-1 pb-1 text-[10px] uppercase tracking-wide text-fg-muted">
              <span>Pos</span>
              <span>Contract</span>
              <span className="text-right">Rate</span>
              <span className="text-right">1D Δ</span>
              <span className="text-right">Z</span>
            </div>
            {visibleRows.length === 0 ? (
              <div className="px-1 py-2 text-[12px] text-fg-secondary">
                No strip rows for this query.
              </div>
            ) : (
              visibleRows.map((row) => {
                const bps = dailyChangeBps(row);
                return (
                  <div
                    key={row.strip_position}
                    className="grid grid-cols-[40px_minmax(0,1fr)_auto_auto_auto] items-center gap-x-3 border-t border-line-subtle px-1 py-1 text-[12px]"
                  >
                    <span className="mono tabular-nums text-fg-muted">
                      {row.strip_position}
                    </span>
                    <span className="mono flex min-w-0 items-center gap-1 truncate tabular-nums text-fg-primary">
                      {row.contract_code}
                      <InfoTooltip
                        content={row.row_methodology_card}
                        label={`Methodology for ${row.contract_code}`}
                        size={10}
                      />
                    </span>
                    <span className="mono text-right tabular-nums text-fg-primary">
                      {row.implied_rate_pct.toFixed(2)}%
                    </span>
                    <span
                      className={`mono text-right tabular-nums ${toneTextClass(toneForChange(bps))}`}
                    >
                      {signedFixed(bps, 1)}
                    </span>
                    <span
                      className={`mono text-right tabular-nums ${toneTextClass(toneForZScore(row.z_score_implied_rate))}`}
                    >
                      {signedFixed(row.z_score_implied_rate, 2)}
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
          title={STRIP_SNAPSHOT_COMPACT_CAVEAT}
        >
          <InfoTooltip content={STRIP_SNAPSHOT_COMPACT_CAVEAT} />
          <span className="truncate">{STRIP_SNAPSHOT_COMPACT_CAVEAT}</span>
        </div>
        <div className="flex shrink-0 items-center gap-3 text-fg-muted">
          {onExpand && totalRows > visibleRows.length && (
            <button
              type="button"
              onClick={onExpand}
              className="text-ice-300 underline-offset-2 hover:underline"
            >
              All {totalRows} →
            </button>
          )}
          {data?.as_of_date && <span>As of {data.as_of_date}</span>}
          <FreshnessPill freshness="fresh" />
        </div>
      </footer>
    </article>
  );
};

export default BuildCompact;

// ---------------------------------------------------------------------------
// KPI cell — compact-density rendering of the shared KPIDescriptor shape
// (numbers + tones come from the shared builders so they match the
// extended strip exactly).
// ---------------------------------------------------------------------------

function KpiCell({ kpi }: { kpi: KPIDescriptor }) {
  const valueSize = kpi.emphasis === 'primary' ? 'text-[22px]' : 'text-[16px]';
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <span className="text-[10px] uppercase tracking-[0.06em] text-fg-faint">
        {kpi.label}
      </span>
      <span className="flex items-baseline gap-1">
        <span
          className={`mono font-semibold tabular-nums ${valueSize} ${toneTextClass(kpi.tone)}`}
        >
          {kpi.value}
        </span>
        {kpi.unit && (
          <span className="text-[10.5px] text-fg-muted">{kpi.unit}</span>
        )}
      </span>
      {kpi.caption && (
        <span className="text-[9.5px] leading-snug text-fg-faint">{kpi.caption}</span>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Skeleton + error states
// ---------------------------------------------------------------------------

function CompactSkeleton() {
  return (
    <div className="flex min-h-[160px] flex-1 flex-col justify-between gap-3" aria-busy>
      <div className="grid grid-cols-3 gap-x-4">
        {[0, 1, 2].map((i) => (
          <div key={i} className="flex flex-col gap-1.5">
            <div className="h-2.5 w-16 animate-pulse rounded bg-line-subtle" />
            <div className="h-5 w-14 animate-pulse rounded bg-line-subtle" />
          </div>
        ))}
      </div>
      <div className="flex flex-col gap-1.5">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="h-4 w-full animate-pulse rounded bg-line-subtle" />
        ))}
      </div>
    </div>
  );
}

function CompactError({ message }: { message: string }) {
  return (
    <div className="flex min-h-[160px] flex-1 items-center justify-center px-2">
      <p className="text-center text-[11.5px] leading-[1.5] text-coral-300">
        {message}
      </p>
    </div>
  );
}
