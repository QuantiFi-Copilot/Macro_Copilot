// ============================================================================
// EdgeConnector — slim arrow rendered between consecutive stage cards.
// ----------------------------------------------------------------------------
// PR A ships a topology-light visualisation: stages flow LEFT → RIGHT
// in topological order, and each adjacent pair gets a hairline arrow
// between them.  This is the "horizontal flow" Mockup B/C show.
//
// True branching topology (event_study has two parallel branches that
// re-join at conditional_aggregate) is approximated for V1 by laying
// the topo-sorted order out left-to-right with a single arrow between
// every pair — the substrate-true edge list is still surfaced as
// hover-tooltip detail on each arrow so the user can verify which
// slot a given edge fills.
//
// PR B will add a layered topology view (a force-directed or column-
// stacked render) when the workspaces grow past the ~8 nodes that
// fit cleanly in one horizontal row.  The connector lives in its
// own file so that future renderer can swap this implementation
// without touching ``DagView``.
// ============================================================================

import { ArrowRight } from 'lucide-react';

type Props = {
  /** Optional hover tooltip — typically the input slot name. */
  hint?: string;
};

export function EdgeConnector({ hint }: Props) {
  return (
    <div
      className="flex shrink-0 items-center px-1.5"
      aria-hidden
      title={hint}
    >
      <ArrowRight
        size={14}
        strokeWidth={1.5}
        className="text-fg-faint/70"
      />
    </div>
  );
}
