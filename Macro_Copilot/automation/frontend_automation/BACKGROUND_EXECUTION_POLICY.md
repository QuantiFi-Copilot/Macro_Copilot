# Background Execution Policy

This file defines how the frontend automation should run in the
background.

## 1. Core model

The automation should be backgrounded through recurring OpenClaw
scheduling.

The scheduler's job is to wake the orchestrator repeatedly.

The orchestrator's job is to process as much of the frontend tool catalog
as possible during each wake.

Those are different responsibilities.

## 2. What one background wake should do

A single wake should:

1. verify the branch rule
2. read the repo automation package
3. read the frontend tool catalog
4. read current runtime state
5. check quotas
6. resume the highest-priority unfinished tool or pick the next
   `todo`
7. run the single-round Claude builder → Claude reviewer (→ optional Claude fix) loop for that
   tool until it reaches APPROVED, APPROVED-AFTER-FIX, or human_required
8. immediately continue to the next eligible tool
9. keep going until a stop condition is hit

It should not intentionally stop after only one tool if more
eligible work remains.

## 3. State priority

When looking for the next tool, use this order:

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

- every tool is `done` or `blocked`
- quota is below threshold
- a hard provider/runtime error occurred
- the current tool requires human judgment
- a branch-rule violation is detected
- a required DB-backed validation path cannot run honestly

These are clean stops, not silent failures.

## 4a. Dirty-tree rule after blocked work

If the current tool becomes `blocked` after generating code, the
background wake must resolve the working tree before it can continue.

Allowed behavior:

- if the blocked diff is discardable under repo policy, record the
  blocker and clean the tree, then continue to the next eligible
  tool in the same wake when feasible
- if a human explicitly wants the blocked scaffold preserved, stop and
  surface that preservation requirement as the reason for the halt

Disallowed behavior:

- leaving blocked generated code in the tree and starting the next
  tool on top of it
- repeatedly waking, seeing the same blocked dirty diff, and asking the
  same cleanup question forever when policy already implies discard

Default policy for repo-policy / no-go blockers:

- mark the tool blocked
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

- preserve tool/catalog state
- write the reason into runtime state
- let the next wake resume

## 7. No overlap rule

Only one orchestrator wake may actively mutate catalog/runtime state at
a time.

Do not run overlapping background wakes for this vertical slice.

The frontend factory is intentionally serial for quality reasons.

## 8. Catalog-exhaustion shutdown

When every tool in `frontend_tool_catalog.yaml` is `done` or
`blocked`, there is no eligible work left for the factory to do.
Continuing to wake the orchestrator at that point burns tokens against
the empty catalog loop with no possible useful output. The shutdown
rule prevents this.

### 8.1 Setting the exhaustion state

When the orchestrator finishes a wake and observes that no tool
is in any of the eligible-work states (`in_progress`,
`changes_required`, `waiting_quota`, `todo`), it MUST write:

```yaml
orchestrator_status: catalog_exhausted
last_stop_reason: catalog_exhausted
catalog_exhausted_at: <UTC iso timestamp>
```

into `frontend_runtime_state.yaml` before exiting. Counts
(`done`, `blocked`, `remaining`) must also be up to date so a human
inspecting the file can see how the catalog finished.

### 8.2 Cron entry-point short-circuit

The OpenClaw cron job calls the orchestrator entry-point script (see
`OPENCLAW_CRON_RUNBOOK.md` for the exact path). Before the
entry-point dispatches the orchestrator prompt to a worker engine, it
MUST:

1. Read `frontend_runtime_state.yaml`.
2. If `orchestrator_status == "catalog_exhausted"`:
   - Log a single line to the orchestrator wake log:
     `catalog exhausted at <catalog_exhausted_at>; skipping wake`.
   - Exit 0 immediately. Do NOT dispatch the orchestrator prompt.
   - Do NOT consume any worker-engine quota.
3. Otherwise, proceed with the normal dispatch.

The cron entry remains in OpenClaw's schedule — the short-circuit
happens inside the entry-point script, not by removing the cron job.
This means the system stays "armed" to resume the moment a human (or
a future automation step) introduces new work.

### 8.3 How a human re-arms the factory

The catalog-exhausted state clears when EITHER condition is met:

a. **New work appears in the catalog.** If a human (or a follow-on
   automation step) appends a new entry to `frontend_tool_catalog.yaml`
   with a non-terminal status (`todo` / `changes_required` /
   `waiting_quota` / `in_progress`), the next entry-point wake
   detects the new eligible work, clears
   `orchestrator_status: catalog_exhausted`, sets it to
   `processing`, and dispatches the orchestrator normally.

b. **Explicit reset.** A human edits
   `frontend_runtime_state.yaml` and sets
   `orchestrator_status: idle` (or removes the field). The next
   entry-point wake re-evaluates the catalog from scratch.

In both cases, the previous `catalog_exhausted_at` timestamp may be
preserved in `reviewer_mode_history`-style audit logs but should not
remain as the current `orchestrator_status`.

### 8.4 Why this rule exists

Without it, the cron would wake the orchestrator on every interval,
which would:

- spend Claude tokens reading the catalog and runtime state
  only to determine there is nothing to do, every single wake,
  forever;
- spam the Discord-facing status surface with identical "nothing to
  do" updates;
- mask a *real* problem if it ever co-occurs with the catalog being
  full (e.g. a stuck `in_progress` tool that genuinely needs
  attention).

A loud, file-based shutdown is the cheap, honest signal that the
factory has completed its current scope and is awaiting human
direction. It is recoverable in one line of YAML and consumes zero
tokens while idle.
