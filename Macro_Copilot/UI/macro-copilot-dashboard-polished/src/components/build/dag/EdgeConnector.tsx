// ============================================================================
// EdgeConnector — three-dot trail between consecutive stage cards.
// ----------------------------------------------------------------------------
// PR A shipped a single hairline arrow; PR C upgrades the visual to
// the mockup's three-dot trail: ●·· — a stronger leading dot anchored
// at the predecessor card's right edge, followed by two lighter dots
// trailing toward the next stage.  The pattern reads as "data flows
// this way" without needing an arrowhead, which let the DAG row keep
// its quiet aesthetic.
//
// True branching topology (event_study has two parallel branches that
// re-join at conditional_aggregate) is still approximated as a single
// linear flow in V1 — the substrate-true edge list is surfaced via the
// hover tooltip (``slot: <name>``) so a curious reader can verify
// which slot a given edge fills.
//
// PR D (or whenever) can swap this for a layered topology view that
// handles ≥8 nodes; the connector lives in its own file specifically
// so that future renderer is a single-file replacement.
// ============================================================================

type Props = {
  /** Optional hover tooltip — typically the input slot name. */
  hint?: string;
};

export function EdgeConnector({ hint }: Props) {
  return (
    <div
      className="flex shrink-0 items-center gap-[3px] px-2"
      role="presentation"
      title={hint}
    >
      <span
        aria-hidden
        className="h-1 w-1 rounded-full bg-violet-400/70"
      />
      <span
        aria-hidden
        className="h-[3px] w-[3px] rounded-full bg-violet-400/45"
      />
      <span
        aria-hidden
        className="h-[3px] w-[3px] rounded-full bg-violet-400/30"
      />
    </div>
  );
}
