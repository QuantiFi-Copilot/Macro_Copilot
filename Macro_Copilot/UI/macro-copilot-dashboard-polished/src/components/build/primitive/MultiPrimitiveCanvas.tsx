// ============================================================================
// MultiPrimitiveCanvas — N-card comparison grid for multi-tool Ask handoffs.
// ----------------------------------------------------------------------------
// R6.3.  When the user asks Ask a multi-instrument question like "compare
// UST / Bund / Gilt 10Y", the supervisor turn's ``workspaceContext``
// carries three tool calls.  BuildShell hands the raw ``?context=`` to
// ``decodePrimitiveList``; if it returns more than one entry (typed
// primitive OR unsupported-known), this canvas mounts the comparison
// grid.
//
// PR1 — every recognised tool gets a cell, including unsupported-
// known ones (rendered as an honest "this tool is paused" card next
// to the working primitives) so the user understands which tools in
// their Ask answer are renderable and which aren't.  Builder-class
// entries are filtered out upstream (they take the ``?builder=``
// redirect via VirtualPrimitiveCanvas).
//
// Why click-to-expand rather than dropdowns in each compact card:
// changing dropdowns in a multi-card layout would mutate the URL's
// ``?context=`` (which carries the FULL list), and would invalidate
// the other cards.  Read-only compact + click-to-expand keeps the
// URL as a stable source of truth for the comparison; the user
// drills into one card when they want to edit.
// ============================================================================

import { useMemo } from 'react';
import { Boxes } from 'lucide-react';
import {
  decodePrimitiveList,
  type DecodedPrimitive,
} from './contextDecoder';
import { isTypedPrimitive } from './fetchDispatcher';
import { MultiPrimitiveCard } from './MultiPrimitiveCard';
import { MultiUnsupportedKnownCard } from './MultiUnsupportedKnownCard';
import { MultiGenericBuilderCard } from './MultiGenericBuilderCard';

type Props = {
  /** Raw value of the ``?context=`` URL param.  Already URI-encoded JSON. */
  contextParam: string;
};

export function MultiPrimitiveCanvas({ contextParam }: Props) {
  const list = useMemo(() => decodePrimitiveList(contextParam), [contextParam]);

  // PR2 — three buckets in the multi-card grid:
  //   - typed primitives (Spread / CrossMarket / Butterfly / Yield /
  //     Regime / Scanner / Forward) → chart-shaped MultiPrimitiveCard
  //   - generic-builder (PR2) → MultiGenericBuilderCard; click opens
  //     the schema-driven configure + run surface for the single tool
  //   - unsupported_known → MultiUnsupportedKnownCard; tool is paused
  // Builder entries (rich analytical models) are filtered upstream by
  // ``decodePrimitiveList`` because they take the ``?builder=``
  // redirect path and don't belong in a primitive comparison grid.
  const typedCount = list.filter(isTypedPrimitive).length;
  const builderCount = list.filter((d) => d.kind === 'generic_builder').length;
  const unsupportedCount = list.filter((d) => d.kind === 'unsupported_known').length;

  return (
    <div className="ambient-grid flex h-full min-h-0 flex-col overflow-y-auto">
      <header className="flex shrink-0 flex-col gap-1.5 border-b border-line-subtle px-6 pt-5 pb-4">
        <div className="flex items-center gap-2">
          <Boxes
            size={12}
            strokeWidth={1.75}
            className="text-ice-300"
            aria-hidden
          />
          <span className="kicker text-fg-muted">
            {kickerFor(typedCount, builderCount, unsupportedCount)}
          </span>
        </div>
        <h1 className="text-[20px] font-medium tracking-[-0.012em] text-fg-primary">
          From your Ask answer
        </h1>
        <p className="text-[12px] leading-[1.5] text-fg-secondary">
          {gridSubtitle(typedCount, builderCount, unsupportedCount)}
        </p>
      </header>

      <div className="grid flex-1 gap-3 px-6 py-5 sm:grid-cols-2 lg:grid-cols-3">
        {list.map((decoded, i) => {
          const key = `${decoded.toolName}-${i}-${cardKey(decoded)}`;
          if (isTypedPrimitive(decoded)) {
            return <MultiPrimitiveCard key={key} decoded={decoded} />;
          }
          if (decoded.kind === 'generic_builder') {
            return (
              <MultiGenericBuilderCard
                key={key}
                toolName={decoded.toolName}
                params={decoded.params}
              />
            );
          }
          // ``decoded.kind === 'unsupported_known'`` — builder entries
          // were filtered out by ``decodePrimitiveList``.
          return (
            <MultiUnsupportedKnownCard
              key={key}
              toolName={decoded.toolName}
              params={decoded.params}
            />
          );
        })}
      </div>
    </div>
  );
}

function kickerFor(typed: number, builders: number, unsupported: number): string {
  const parts: string[] = [];
  if (typed > 0) parts.push(`${typed} typed`);
  if (builders > 0) parts.push(`${builders} builder${builders === 1 ? '' : 's'}`);
  if (unsupported > 0) parts.push(`${unsupported} unsupported`);
  if (parts.length === 0) return 'Nothing to compare';
  return `Comparing ${parts.join(' · ')}`;
}

function gridSubtitle(typed: number, builders: number, unsupported: number): string {
  if (typed > 0 && builders === 0 && unsupported === 0) {
    return 'Each card pulls live data from the typed-detail endpoint.  Click a card to open it with editable parameters and the full chart suite.';
  }
  if (typed === 0 && builders > 0 && unsupported === 0) {
    return 'These primitives have no bespoke Build view yet — each card opens a schema-driven builder with editable controls + the auto-renderer.';
  }
  if (typed === 0 && builders === 0 && unsupported > 0) {
    return "These tools are known to the backend but have no Build run path yet — use Ask while we wire them in.";
  }
  return 'Mixed result types — click a card to drill into the matching Build surface for that primitive.';
}

/** Stable key built from the params dict so React doesn't conflate two
 *  cards that share a tool name (e.g. two yield queries on different
 *  curves). */
function cardKey(decoded: DecodedPrimitive): string {
  return Object.entries(decoded.params)
    .map(([k, v]) => `${k}=${v}`)
    .sort()
    .join('&');
}
