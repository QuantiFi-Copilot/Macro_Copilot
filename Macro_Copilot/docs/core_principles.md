# CORE PRINCIPLE: Bloomberg Accuracy Boundary

## The Rule

**Never build a tool that recomputes what Bloomberg already provides, unless the recomputation achieves accuracy that is indistinguishable from Bloomberg's output by the standards of macro trading workflows and research.**

"Indistinguishable" means: a portfolio manager comparing our output to the equivalent Bloomberg screen would not find a discrepancy that changes their analytical conclusion or trading decision. Single-digit basis point differences on a 5Y5Y forward are acceptable. A meeting-pricing probability that diverges by 15 percentage points from WIRP is not.

## The Decision Framework

For any new tool, ask three questions in order:

**1. Does Bloomberg already compute this?**
If yes → default to ingesting Bloomberg's output as a data field. Do not recompute unless Question 2 is satisfied.

**2. Can we recompute it with reasonable accuracy?**
"Reasonable" = the difference between our calculation and Bloomberg's is not statistically meaningful or analytically misleading by market standards. Z-scores computed from the same underlying data: yes, these match. Forward rates using annual compounding on par OIS curves: close enough for directional macro analysis. Meeting-by-meeting probabilities from linearly interpolated OIS swaps: no — the methodology is structurally wrong (ramp vs staircase), and the output diverges materially from WIRP. If we cannot achieve reasonable accuracy → ingest Bloomberg's pre-computed output. Do not ship an inferior approximation.

**3. Does Bloomberg NOT compute this at all?**
If Bloomberg has no equivalent screen, function, or output for what our tool does → this is where we build custom analytics. This is the product's genuine value. Examples: cross-universe z-score scanners, regime classification, multi-tool conditional orchestration chains.

## What This Means in Practice

**INGEST from Bloomberg (do not recompute):**
- Meeting-by-meeting rate expectations and cut probabilities (WIRP)
- Bootstrapped forward rate curves at specific tenors (FWCV)
- Carry and roll-down analytics (FWCV / YAS)
- Option-adjusted spreads, ASW spreads (YAS / ASW)
- Anything requiring a full multi-curve bootstrap or options pricing model

**COMPUTE ourselves (genuine value-add):**
- Rolling z-scores and percentile ranks across the full instrument universe
- Cross-instrument scanners that rank by statistical extremes
- Curve regime classification (bull steepener, bear flattener, etc.)
- Multi-tool orchestration chains (scan → filter → compare → synthesise)
- Custom cross-market relative value with historical context
- Any analytical framing that Bloomberg provides raw data for but does not pre-package as a unified view

**COMPUTE ourselves (where accuracy is achievable):**
- Two-point curve spreads (trivial subtraction — exact match)
- Cross-market yield/rate differentials (trivial subtraction — exact match)
- Butterfly spreads (2×belly − short − long — exact match)
- Period changes in bps (trivial subtraction — exact match)
- Simple forward rates where both endpoints are ≤1Y (money market compounding — acceptable accuracy)

## Why This Principle Exists

The end user is a discretionary macro PM at an institutional hedge fund. They have Bloomberg Terminal open on three screens. If our tool gives them a number that visibly contradicts Bloomberg, they will not investigate why — they will stop using our tool. Trust is binary: one wrong WIRP number kills adoption of the entire copilot.

Our competitive advantage is NOT replicating Bloomberg's pricing engine. It is building an intelligence layer on top of Bloomberg's data: natural language access, cross-instrument scanning, automated regime detection, and multi-step analytical reasoning that would take the PM 30-60 minutes of manual work on Bloomberg screens.

Bloomberg is the pricing engine. We are the intelligence layer.

Another core principle: WE ABSOLUTELY DO NOT UNDER ANY CIRCUMSTANCES BUILD SOMETHING HALF ASSED UNDER the deterministic mode: 
For e.g. in OIS, do not just half ass it with linear interpolation if the market pricing of central bank positioning is a step shaped curve for instance. DO NOT GUESS OR ESTIMATE. IF you MUST THEN ALLOW FOR ANALYST OVERRIDE AND LET THE ANALYST CHOOSE WHICH METHOD HE WANTS TO USE. 