// ============================================================================
// MultiPrimitiveCanvas — N-card comparison grid for multi-tool Ask handoffs.
// ----------------------------------------------------------------------------
// R6.3.  When the user asks Ask a multi-instrument question like "compare
// UST / Bund / Gilt 10Y", the supervisor turn's ``workspaceContext``
// carries three tool calls.  BuildShell hands the raw ``?context=`` to
// ``decodePrimitiveList``; if it returns more than one typed primitive
// (i.e. not a builder), this canvas mounts the comparison grid:
//
//   ┌─────────────────────────────────────────────────────────────────┐
//   │ COMPARING N PRIMITIVES · from your Ask answer                   │
//   ├──────────────────┬──────────────────┬──────────────────┬───────┤
//   │ ↗ Yield · UST 10Y │ ↗ Yield · Bund... │ ↗ Yield · Gilt 10Y │ ... │
//   │ 4.278 %          │ 2.942 %          │ 4.707 %          │      │
//   │ -1.7 bps · z 0.39 │ -14.1 bps · z 1.6 │ -19.4 bps · z 0.98│      │
//   └──────────────────┴──────────────────┴──────────────────┴───────┘
//
// Each card is independently fetching its typed-detail payload.  Click
// any card → opens it in the full single-card canvas (with editable
// dropdowns and the methodology card).
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
import { isTypedPrimitive, type DecodedTypedPrimitive } from './fetchDispatcher';
import { MultiPrimitiveCard } from './MultiPrimitiveCard';

type Props = {
  /** Raw value of the ``?context=`` URL param.  Already URI-encoded JSON. */
  contextParam: string;
};

export function MultiPrimitiveCanvas({ contextParam }: Props) {
  const list = useMemo(() => decodePrimitiveList(contextParam), [contextParam]);

  // Narrow to typed primitives only — ``decodePrimitiveList`` already
  // filters out rich-model entries, so this is a type-only guard so
  // the card prop matches.
  const typed: DecodedTypedPrimitive[] = list.filter(isTypedPrimitive);

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
            Comparing {typed.length} primitives
          </span>
        </div>
        <h1 className="text-[20px] font-medium tracking-[-0.012em] text-fg-primary">
          From your Ask answer
        </h1>
        <p className="text-[12px] leading-[1.5] text-fg-secondary">
          Each card pulls live data from the typed-detail endpoint.  Click a
          card to open it with editable parameters and the full chart suite.
        </p>
      </header>

      <div className="grid flex-1 gap-3 px-6 py-5 sm:grid-cols-2 lg:grid-cols-3">
        {typed.map((decoded, i) => (
          <MultiPrimitiveCard
            key={`${decoded.toolName}-${i}-${cardKey(decoded)}`}
            decoded={decoded}
          />
        ))}
      </div>
    </div>
  );
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
