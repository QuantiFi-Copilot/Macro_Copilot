# No-Go Rules

If any rule in this file is triggered, the primitive must NOT be built
in the current run.

## 1. Missing required metadata

If the real primitive requires metadata the repo does not have, do not
ship:

- a proxy
- a degraded approximation
- a heuristic substitute
- a "good enough for now" version

Defer it.

## 2. Technical debt blocker

If `docs/technical_debt.md` explicitly says a certain class of tool
must wait until a blocker is resolved, obey that.

Example already documented in this repo:

- futures-specific analytics are blocked by rolling-contract metadata
  history issues until that debt is fixed

## 3. Not actually a new primitive concept

If the requested work is only:

- new coverage
- new universe support
- new mapping entries
- new examples

then do not create a new primitive.

Reclassify as expansion work.

## 4. Standardness failure

Do not build a primitive whose concept is not honest under the repo's
standard-tool definition.

Fail if:

- the concept is not desk-recognizable or standard in the intended
  category
- the methodology meaning would be ambiguous without hidden choices
- the primitive name would overclaim what the implementation actually
  computes

## 5. Hidden methodology in code

Do not build a primitive if its methodology choices are buried in code
when they belong in YAML.

## 6. YAML trying to own invariants

Do not move mathematical truths into YAML just to make the file look
more configurable.

Code-level invariants remain in code.

## 7. Input schema overreach

Do not expose methodology knobs as LLM inputs unless they are truly the
central user-facing choice that defines the tool.

## 8. Branch violation

If the current branch is not exactly:

`primitive_automation`

the automation must stop immediately.

It may not:

- switch branches
- create a new branch
- rebase
- merge
- cherry-pick

## 9. Incomplete repo wiring

Do not call a primitive done if it is missing required repo touchpoints
like:

- MCP wiring
- schema re-export
- workflow registration when applicable
- tests
- config lint compatibility

## 10. Reviewer not satisfied

If Codex has not clearly approved the primitive, do not commit it as
done.

Valid findings must be fixed.

If the reviewer is uncertain or the process cannot tell whether a
finding is valid, stop and ask for human input rather than guessing.

## 11. DB-backed validation skipped

Do not approve or commit a primitive if the required DB-backed
validation did not run.

Offline tests alone are not enough for this automation.

## 12. Mutating DB behavior

If the automation runs:

- ingestion
- upserts
- updates
- deletes
- DDL
- any other DB-mutating command

stop immediately.

This automation may validate against the DB, but it may not change DB
state.

## 13. SQL validation is weak or ceremonial

Do not treat a primitive as done if its SQL validation runner does not
independently validate the core logic as far as the repo currently
allows.

The validator must be a real deterministic cross-check, not a box-
ticking script.

## 14. Blocked scaffold left in-tree

If a primitive is blocked by repo policy, missing metadata, or another
no-go rule after code has already been generated, that blocked scaffold
may not remain in the working tree while the automation proceeds to the
next primitive.

Default behavior:

- record the blocker
- discard the blocked diff
- continue to the next eligible primitive

Only preserve blocked scaffold in-tree if a human explicitly instructs
that it should be preserved.
