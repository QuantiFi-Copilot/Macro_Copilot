# Background Execution Policy

This file defines how the primitive automation should run in the
background.

## 1. Core model

The automation should be backgrounded through recurring OpenClaw
scheduling.

The scheduler's job is to wake the orchestrator repeatedly.

The orchestrator's job is to process as much of the primitive catalog
as possible during each wake.

Those are different responsibilities.

## 2. What one background wake should do

A single wake should:

1. verify the branch rule
2. read the repo automation package
3. read the primitive catalog
4. read current runtime state
5. check quotas
6. resume the highest-priority unfinished primitive or pick the next
   `todo`
7. run the full Claude -> Codex -> Claude -> Codex loop for that
   primitive until it reaches a terminal checkpoint
8. immediately continue to the next eligible primitive
9. keep going until a stop condition is hit

It should not intentionally stop after only one primitive if more
eligible work remains.

## 3. State priority

When looking for the next primitive, use this order:

1. `in_progress`
2. `changes_required`
3. `waiting_quota`
4. `todo`

Skip:

- `done`
- `blocked`

This preserves serial quality discipline while still letting the
automation advance through the whole backlog.

## 4. Stop conditions for a single background wake

Stop the current wake if:

- every primitive is `done` or `blocked`
- quota is below threshold
- a hard provider/runtime error occurred
- the current primitive requires human judgment
- a branch-rule violation is detected
- a required DB-backed validation path cannot run honestly

These are clean stops, not silent failures.

## 4a. Dirty-tree rule after blocked work

If the current primitive becomes `blocked` after generating code, the
background wake must resolve the working tree before it can continue.

Allowed behavior:

- if the blocked diff is discardable under repo policy, record the
  blocker and clean the tree, then continue to the next eligible
  primitive in the same wake when feasible
- if a human explicitly wants the blocked scaffold preserved, stop and
  surface that preservation requirement as the reason for the halt

Disallowed behavior:

- leaving blocked generated code in the tree and starting the next
  primitive on top of it
- repeatedly waking, seeing the same blocked dirty diff, and asking the
  same cleanup question forever when policy already implies discard

Default policy for repo-policy / no-go blockers:

- mark the primitive blocked
- discard the blocked diff
- continue

## 5. Why recurring wakeups exist

Recurring wakeups exist so the system can recover from:

- quota resets
- VM restarts
- OpenClaw gateway restarts
- transient provider failures
- manual interruptions

Do not rely on one immortal conversation or one immortal harness
session.

Repo state is the continuity mechanism.

## 6. Runtime cap

Each wake may also enforce a practical runtime cap if the platform
needs it, but that cap should be long enough to make meaningful
progress through the catalog.

If a wake stops because of runtime cap:

- preserve primitive/catalog state
- write the reason into runtime state
- let the next wake resume

## 7. No overlap rule

Only one orchestrator wake may actively mutate catalog/runtime state at
a time.

Do not run overlapping background wakes for this vertical slice.

The primitive factory is intentionally serial for quality reasons.
