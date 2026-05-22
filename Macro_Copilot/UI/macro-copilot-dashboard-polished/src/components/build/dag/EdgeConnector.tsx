// ============================================================================
// EdgeConnector — slim hairline wire rendered between consecutive stage cards.
// ----------------------------------------------------------------------------
// PR A ships a topology-light visualisation: stages flow LEFT → RIGHT
// in topological order, and each adjacent pair gets a hairline wire
// between them.  This is the "horizontal flow" Mockup B/C show.
//
// True branching topology (event_study has two parallel branches that
// re-join at conditional_aggregate) is approximated for V1 by laying
// the topo-sorted order out left-to-right with a single wire between
// every pair — the substrate-true edge list is still surfaced as
// hover-tooltip detail on each wire so the user can verify which
// slot a given edge fills.
//
// Visual recipe (matches mocks B/C):
//   ──•──────•──►
//   |     |    |
//   start dot   end arrow
//
// The wire is rendered with a soft violet gradient + two small dots
// at the connection points — the same lineage palette used for hash
// chips and provenance markers across Ask + Library.
// ============================================================================

type Props = {
  /** Optional hover tooltip — typically the input slot name. */
  hint?: string;
};

export function EdgeConnector({ hint }: Props) {
  return (
    <div
      className="relative flex shrink-0 items-center self-stretch px-2"
      aria-hidden
      title={hint}
    >
      <div className="flex w-10 items-center gap-[3px]">
        <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-lineage-400/50 shadow-[0_0_4px_rgba(155,140,255,0.5)]" />
        <span
          className="h-px flex-1"
          style={{
            background:
              'linear-gradient(90deg, rgba(155,140,255,0.55), rgba(155,140,255,0.25))',
          }}
        />
        <svg
          width="8"
          height="8"
          viewBox="0 0 8 8"
          fill="none"
          className="shrink-0 text-lineage-400"
        >
          <path
            d="M1 1L6 4L1 7"
            stroke="currentColor"
            strokeWidth="1.25"
            strokeLinecap="round"
            strokeLinejoin="round"
            opacity="0.9"
          />
        </svg>
      </div>
    </div>
  );
}
