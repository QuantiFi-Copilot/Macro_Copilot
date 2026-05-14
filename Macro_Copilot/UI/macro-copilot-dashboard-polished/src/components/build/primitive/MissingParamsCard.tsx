// ============================================================================
// MissingParamsCard — PR-B-β honest "this card has missing params" tile.
// ----------------------------------------------------------------------------
// Pre-PR-B-β an Ask-handoff card whose required params were dropped
// upstream (e.g. the LLM invoked ``calculate_cross_market_spread_tool``
// with no ``curve_family_1`` / ``curve_family_2``) silently folded
// the spec defaults (``IT_BTP-DE_BUND``) and rendered a fully-formed
// chart card for the WRONG instrument pair.  Users got duplicated
// "default" cards in the multi-card grid with no signal that anything
// was wrong.
//
// PR-B-β surfaces this honestly: when the Ask-handoff signal is set
// AND the card's required-params taxonomy
// (``requiredParamsFor(kind)``) flags missing values, the typed view
// renders this card instead of fetching.  The user sees:
//
//   - Which params are missing (named, not just "incomplete").
//   - A "Configure & open" affordance that drills into the editable
//     single-card canvas so the user can fill in the values and
//     re-fetch.
//   - The originating tool name + kicker label so the row reads
//     consistently with the working cards next to it.
//
// What this card explicitly does NOT do
// -------------------------------------
//   - It does NOT silently default missing values — that was the
//     pre-PR-B-β bug.
//   - It does NOT call the typed-detail endpoint with empty params —
//     that path returns 400s and a confusing fetch-error tile.
//   - It does NOT cover Library-blank opens — blank opens still
//     fold spec defaults via ``resolveParamValue`` so the user lands
//     on a working chart with the default instrument.  The missing-
//     param flow is gated on ``handoff === 'ask'`` upstream.
// ============================================================================

import { AlertCircle, ArrowUpRight } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import {
  HANDOFF_QUERY_PARAM,
  HANDOFF_ASK_VALUE,
} from './handoffSignal';
import type { PrimitiveViewKind } from './contextDecoder';

type Props = {
  toolName: string;
  /** ``DecodedPrimitive`` kind — drives the kicker label. */
  kind: PrimitiveViewKind;
  /** Names of required params the Ask handoff dropped.  At LEAST
   *  one entry; the caller decides whether to render this card. */
  missingParams: ReadonlyArray<string>;
  /** Params the Ask trace DID carry — preserved so "Configure &
   *  open" drills into the single-card canvas with what's available
   *  already populated.  The user only has to fill the missing
   *  fields. */
  presentParams: Record<string, string>;
  /** Compact mode (used inside ``MultiPrimitiveCanvas`` grid cells).
   *  When true the card sheds vertical chrome to fit a ``min-h-[180px]``
   *  cell; when false (single-canvas case) the card spans the full
   *  canvas surface. */
  compact?: boolean;
};

export function MissingParamsCard({
  toolName,
  kind,
  missingParams,
  presentParams,
  compact = true,
}: Props) {
  const navigate = useNavigate();

  const handleConfigure = () => {
    // Round-trip through ``?context=`` so the destination canvas
    // mounts the EDITABLE single-card view with the present-params
    // already in the controls strip.  PRESERVE the ``handoff=ask``
    // marker so a subsequent re-encode (when the user changes a
    // value via the controls strip) keeps the Ask-handoff semantics
    // — though after the user fills in the missing fields the
    // missing-param tile won't re-fire.
    const ctx = encodeURIComponent(
      JSON.stringify({
        tools: [{ tool: toolName, params: presentParams }],
        tool_count: 1,
      }),
    );
    navigate(
      `/workspace?context=${ctx}&${HANDOFF_QUERY_PARAM}=${HANDOFF_ASK_VALUE}`,
    );
  };

  const kicker = kickerFor(kind);
  const title = `${kicker} · ${missingParams.length} missing param${
    missingParams.length === 1 ? '' : 's'
  }`;

  return (
    <button
      type="button"
      onClick={handleConfigure}
      data-testid={`missing-params-card:${toolName}`}
      data-missing-params={missingParams.join(',')}
      className={
        compact
          ? 'research-card group relative flex min-h-[180px] flex-col gap-3 overflow-hidden border-coral-400/30 px-4 py-3.5 text-left transition-transform duration-200 ease-sleek hover:-translate-y-px focus-visible:-translate-y-px focus-visible:outline-none'
          : 'research-card group relative flex flex-col gap-4 overflow-hidden border-coral-400/30 px-6 py-5 text-left transition-transform duration-200 ease-sleek hover:-translate-y-px focus-visible:-translate-y-px focus-visible:outline-none'
      }
      style={{
        // Coral rail telegraphs "something's off" without making the
        // tile look broken — keeps the visual register consistent
        // with the surrounding cards.
        ['--rail-color' as string]: 'rgba(243, 113, 113, 0.55)',
      }}
    >
      <span aria-hidden className="research-card-rail" />

      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <span className="kicker text-coral-300">{kicker}</span>
          <h4 className="mt-0.5 text-[13px] font-semibold tracking-[-0.008em] text-fg-primary">
            {title}
          </h4>
        </div>
        <ArrowUpRight
          size={13}
          strokeWidth={1.75}
          aria-hidden
          className="mt-0.5 shrink-0 text-fg-faint transition-colors group-hover:text-coral-200"
        />
      </div>

      <div className="flex items-start gap-2">
        <AlertCircle
          size={12}
          strokeWidth={1.75}
          className="mt-0.5 shrink-0 text-coral-300"
          aria-hidden
        />
        <p className="text-[11px] leading-[1.5] text-fg-secondary">
          The Ask handoff didn&apos;t include{' '}
          {missingParams.map((p, i) => (
            <span key={p}>
              <code className="font-mono text-[10.5px] text-coral-200">
                {p}
              </code>
              {i < missingParams.length - 1 ? ', ' : ''}
            </span>
          ))}
          . Click to configure &amp; run.
        </p>
      </div>

      <div className="mt-auto flex items-center gap-1.5 text-[10.5px] text-fg-faint">
        <span className="font-mono">{toolName}</span>
      </div>
    </button>
  );
}

function kickerFor(kind: PrimitiveViewKind): string {
  switch (kind) {
    case 'spread':
      return 'Curve spread';
    case 'cross_market':
      return 'Cross-market';
    case 'butterfly':
      return 'Butterfly';
    case 'yield':
      return 'Yield';
    case 'regime':
      return 'Regime';
    case 'scanner':
      return 'Scanner';
    case 'forward':
      return 'OIS forward';
  }
}
