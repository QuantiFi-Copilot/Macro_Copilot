// ============================================================================
// startingPoints
// ----------------------------------------------------------------------------
// Personalized "starting points" rendered on the empty state.  Each is
// a single typographic line (NOT a chip / card) — clicking it loads the
// prompt into the composer.
//
// V1: a curated 3-line set drawn from the canonical workflow archetypes
// + a quick primitive scan.  These are the prompts the workflow router
// is best-tested against (see WORKFLOW_GAUNTLET.md), so they reliably
// produce a structured workflow card on first try — important first
// impression.
//
// V2: this becomes a personalization function over the user's recent
// activity, watched instruments, and overnight market state.  Signature
// is intentionally synchronous and free of side-effects so it can be
// swapped for an async fetch behind the same call site.
// ============================================================================

export type StartingPoint = {
  id: string;
  /** The prompt text inserted into the composer when clicked. */
  prompt: string;
  /** Short label rendered on the empty state.  May truncate or
   *  paraphrase the prompt for visual rhythm. */
  label: string;
};

const V1_STARTING_POINTS: StartingPoint[] = [
  {
    id: 'event_study_swap_spread',
    label:
      'Run an event study: when 2Y UST-SOFR swap spread widens >1.5σ, what does the 10Y UST do over 5 days?',
    prompt:
      "Over the last 5 years, when the 2Y OIS-Treasury spread widens by more than 1.5σ in a single day, what's the average 5-day forward move in the 10Y UST yield, and how does it compare to the unconditional 5-day move?",
  },
  {
    id: 'regime_conditioned_beta',
    label:
      'Estimate the rolling β of 10Y UST to 2Y SOFR, in steepening vs flattening 2s10s regimes',
    prompt:
      'Estimate the rolling beta of the 10Y UST yield change to the 2Y OIS rate change, and report how that beta differs in steepening vs flattening regimes of the 2s10s curve over the last 3 years.',
  },
  {
    id: 'btp_bund_overview',
    label:
      'How did BTP-Bund 10Y move overnight, and is the 5d change stretched on z-score?',
    prompt:
      'What is the BTP-Bund 10Y spread today, how did it move overnight, and where does the 5-day change sit on its 252-day z-score?',
  },
];

export function getStartingPoints(): StartingPoint[] {
  // V2: take a CopilotMessage[] (or backend personalization context)
  // and rank by recency × relevance.  Today: deterministic V1 set.
  return V1_STARTING_POINTS;
}
