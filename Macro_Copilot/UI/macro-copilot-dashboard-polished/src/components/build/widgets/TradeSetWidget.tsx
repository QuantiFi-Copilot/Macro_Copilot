// ============================================================================
// TradeSetWidget — payload-backed renderer for TradeSet artifacts.
// ----------------------------------------------------------------------------
// PR4 — loads the persisted TradeSet body via the PR3 hook.
//
// State machine
// -------------
// TradeSets come from the backtest archetype, which is still paused on
// the LLM-facing surface (``rates_agent/workflows/mcp_server.py``).
// In practice we encounter TWO kinds of TradeSet artifacts:
//
//   1. Genuine trade-table output from a directly-invoked backtest
//      workflow.  ``payload.trades`` carries the flattened
//      ``TradeSet.to_records()`` list — render a real preview table.
//
//   2. Workspaces persisted before backtest was paused, OR
//      placeholder rows from an interim wiring.  Either way the
//      payload is empty or has zero trade records; render the
//      "backtest paused" honest state.
//
// What this widget DOES NOT do
// ----------------------------
//   - Never invents trades or P&L when the payload is missing.
//   - Never shows the artifact-summary row-count as the trade count
//     without verifying the actual list length.
//   - Never claims a strategy ran successfully when its payload is
//     empty — the paused-state caption is the load-bearing affordance.
//
// Per-row layout
// --------------
// We render a compact subset of common TradeRecord fields when
// present: ``entry_date`` / ``exit_date`` / ``entry_value`` /
// ``exit_value`` / ``pnl`` / ``return_pct``.  Each backtest variant
// may add legs / weights / signal_value — those land in the detail
// view (PR4 stays on the card).
// ============================================================================

import { useMemo } from 'react';
import { Construction } from 'lucide-react';
import type { NodeRenderer } from '@/components/build/lib/nodeRendererRegistry';
import { registerArtifactRenderer } from '@/components/build/lib/nodeRendererRegistry';
import { PayloadShell } from './shared/PayloadShell';
import {
  formatDate,
  formatNumber,
  MISSING_VALUE_DASH,
  tradeRowCount,
  type TradeSetPayloadEnvelope,
} from './shared/artifactFormat';
import type { TradeRecord } from '@/types/artifacts';

const TRADE_PREVIEW_LIMIT_HEAD = 3;
const TRADE_PREVIEW_LIMIT_TAIL = 1;

const TradeSetWidget: NodeRenderer = ({ node, artifact }) => {
  return (
    <PayloadShell<TradeSetPayloadEnvelope>
      artifactHash={node.artifact_hash ?? artifact.hash}
      expectedType="TradeSet"
      displayName="TradeSet"
      // NOTE: an empty TradeSet is NOT a payload-empty state — it's
      // the "backtest paused / no trades to render" honest signal.
      // Handle it in the body so the empty caption is specific.
    >
      {(payload) => <TradeSetBody payload={payload} />}
    </PayloadShell>
  );
};

function TradeSetBody({ payload }: { payload: TradeSetPayloadEnvelope }) {
  const tradeCount = tradeRowCount(payload);

  if (tradeCount === 0) {
    return <PausedState />;
  }

  return <TradePreview payload={payload} tradeCount={tradeCount} />;
}

// ---------------------------------------------------------------------------
// Paused / empty state — honest "no trades to render" caption.
// ---------------------------------------------------------------------------

function PausedState() {
  return (
    <div className="flex min-h-0 flex-1 flex-col px-5 py-4">
      <div className="flex items-center gap-2">
        <Construction
          size={11}
          strokeWidth={1.75}
          aria-hidden
          className="text-amber-300"
        />
        <span className="kicker text-amber-300">
          backtest · no trades persisted
        </span>
      </div>
      <p className="mt-3 text-[11px] leading-[1.5] text-fg-secondary">
        The backtest archetype is currently paused while data prerequisites
        (DV01, OTR, true O/N OIS, TIPS carry) are wired in.  This card
        reflects an empty TradeSet — no trades to render.  When the
        archetype resumes, real trades will populate from this same
        widget without a UI change.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Trade preview — compact table with head/tail rows.
// ---------------------------------------------------------------------------

function TradePreview({
  payload,
  tradeCount,
}: {
  payload: TradeSetPayloadEnvelope;
  tradeCount: number;
}) {
  const trades = payload.payload.trades ?? [];
  const totalPnl = useMemo(
    () => sumFiniteField(trades, 'pnl'),
    [trades],
  );
  const winners = useMemo(
    () => countWhere(trades, (t) => isFiniteNumber(t.pnl) && (t.pnl as number) > 0),
    [trades],
  );

  const slots = useMemo(
    () => buildTradeSlots(tradeCount),
    [tradeCount],
  );

  return (
    <div className="flex min-h-0 flex-1 flex-col px-5 pt-3 pb-3">
      <div className="kicker text-fg-muted">
        {tradeCount.toLocaleString()} trade{tradeCount === 1 ? '' : 's'} ·{' '}
        {winners.toLocaleString()} winner{winners === 1 ? '' : 's'}
        {totalPnl !== null && (
          <span className="ml-2 normal-case tracking-normal text-fg-faint">
            net pnl {formatNumber(totalPnl, { unit: 'decimal' })}
          </span>
        )}
      </div>
      <div className="mt-2 overflow-x-auto">
        <table className="min-w-full border-collapse text-[10.5px] tabular-nums">
          <thead>
            <tr className="border-b border-line-subtle">
              <th className="py-1 pr-3 text-left font-medium uppercase tracking-[0.1em] text-fg-faint">
                entry
              </th>
              <th className="py-1 pr-3 text-left font-medium uppercase tracking-[0.1em] text-fg-faint">
                exit
              </th>
              <th className="py-1 pr-3 text-right font-medium uppercase tracking-[0.1em] text-fg-faint">
                pnl
              </th>
              <th className="py-1 pr-3 text-right font-medium uppercase tracking-[0.1em] text-fg-faint">
                return
              </th>
            </tr>
          </thead>
          <tbody>
            {slots.map((slot) =>
              slot === 'ellipsis' ? (
                <tr key="ellipsis" className="border-t border-line-subtle/60">
                  <td className="py-1 text-fg-faint" colSpan={4}>
                    …
                  </td>
                </tr>
              ) : (
                <TradeRow key={`t${slot}`} trade={trades[slot]} />
              ),
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function TradeRow({ trade }: { trade: TradeRecord }) {
  return (
    <tr className="border-t border-line-subtle/40">
      <td className="py-1 pr-3 font-mono text-fg-secondary">
        {formatDate(trade.entry_date)}
      </td>
      <td className="py-1 pr-3 font-mono text-fg-secondary">
        {formatDate(trade.exit_date)}
      </td>
      <td className="py-1 pr-3 text-right font-mono text-fg-primary">
        {isFiniteNumber(trade.pnl)
          ? formatNumber(trade.pnl, { unit: 'decimal' })
          : MISSING_VALUE_DASH}
      </td>
      <td className="py-1 pr-3 text-right font-mono text-fg-primary">
        {isFiniteNumber(trade.return_pct)
          ? formatNumber(trade.return_pct, { unit: 'percent' })
          : MISSING_VALUE_DASH}
      </td>
    </tr>
  );
}

function buildTradeSlots(total: number): Array<number | 'ellipsis'> {
  if (total <= TRADE_PREVIEW_LIMIT_HEAD + TRADE_PREVIEW_LIMIT_TAIL) {
    return Array.from({ length: total }, (_, i) => i);
  }
  const slots: Array<number | 'ellipsis'> = [];
  for (let i = 0; i < TRADE_PREVIEW_LIMIT_HEAD; i++) slots.push(i);
  slots.push('ellipsis');
  for (
    let i = total - TRADE_PREVIEW_LIMIT_TAIL;
    i < total;
    i++
  )
    slots.push(i);
  return slots;
}

function isFiniteNumber(v: unknown): v is number {
  return typeof v === 'number' && Number.isFinite(v);
}

function sumFiniteField(
  trades: TradeRecord[],
  field: 'pnl' | 'return_pct' | 'entry_value' | 'exit_value',
): number | null {
  let sum = 0;
  let any = false;
  for (const t of trades) {
    const v = t[field];
    if (isFiniteNumber(v)) {
      sum += v;
      any = true;
    }
  }
  return any ? sum : null;
}

function countWhere(
  trades: TradeRecord[],
  pred: (t: TradeRecord) => boolean,
): number {
  let n = 0;
  for (const t of trades) {
    if (pred(t)) n += 1;
  }
  return n;
}

registerArtifactRenderer('TradeSet', TradeSetWidget);
export { TradeSetWidget };
