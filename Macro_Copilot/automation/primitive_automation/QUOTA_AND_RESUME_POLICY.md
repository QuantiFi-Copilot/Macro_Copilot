# Quota And Resume Policy

This file defines how the primitive orchestrator decides whether it can
continue working, when it must pause, and how it should resume later.

## 1. Core principle

The automation should process as much of the primitive catalog as
possible in one run.

It should not stop early just because one primitive finished.

It should stop only when:

- the catalog is exhausted
- a primitive is honestly blocked
- a hard provider/runtime error occurs
- or quota is too low to continue safely

## 2. Quota check contract

Before doing meaningful work, the orchestrator should try to determine
remaining limits for both:

- Claude Code CLI
- Codex CLI

The preferred path is to use each CLI's own status surface and
normalize the result to exactly this JSON shape:

```json
{
  "claude": {
    "five_hour_left_pct": 83,
    "weekly_left_pct": 67,
    "reset_at": "2026-05-07T23:50:00Z"
  },
  "codex": {
    "five_hour_left_pct": 85,
    "weekly_left_pct": 91,
    "reset_at": "2026-05-08T00:20:56Z"
  }
}
```

The orchestrator should obtain this by instructing the execution layer
to use the CLI status command path, for example:

- enter Claude CLI and use `/status`
- enter Codex CLI and use `/status`

The orchestrator should not rely on vague prose if the CLIs can return
more structured status.

However, the primitive factory runs non-interactively. If the CLIs do
not expose an automation-safe status path from that environment, the
orchestrator must not deadlock the whole factory on that limitation
alone.

## 3. When to check

The orchestrator must try to check limits:

1. once before entering the catalog-processing loop
2. before starting each new primitive
3. again after a heavy builder/reviewer cycle if quota is becoming tight
4. again before resuming a `waiting_quota` primitive

Do not start a new primitive from stale quota information if a fresh
automation-safe probe is actually available.

## 4. Threshold policy

Use these thresholds unless a human explicitly changes them.

These thresholds apply when the orchestrator has a fresh normalized
quota snapshot.

### Starting a new primitive

Only start a new primitive if:

- Claude 5-hour remaining >= 20%
- Claude weekly remaining >= 10%
- Codex 5-hour remaining >= 10%

### Continuing an already-active primitive

Continue an in-progress primitive if:

- Claude 5-hour remaining >= 8%
- Codex 5-hour remaining >= 8%

This policy is intentionally aggressive enough to deliver real
automation value, while still avoiding obviously wasteful starts.

## 5. Low-quota behavior

If limits are below threshold:

- do not mark the primitive failed
- do not lose catalog position
- set the primitive status to `waiting_quota`
- update runtime state with:
  - `quota_state: waiting_quota`
  - the normalized limit JSON
  - the reason the run stopped
- stop cleanly

The next background wake should try again.

## 6. Non-interactive probe fallback

If the orchestrator cannot obtain fresh quota numbers because the CLI
status surface is unavailable in non-interactive automation, do NOT
halt the factory on that fact alone.

Instead:

- record in runtime state that:
  - `quota_state` is effectively unknown / probe-unavailable
  - the preferred `/status` path was unavailable from automation
  - the run is proceeding in runtime-enforced quota mode
- preserve the last known numeric snapshot if one exists, but mark it
  stale
- continue to the next eligible primitive

In this fallback mode:

- do not pretend the quota numbers are fresh
- do not invent percentages
- do not block the factory just because `/status` is unavailable
- rely on real provider responses during builder/reviewer execution to
  detect actual quota exhaustion

This is the correct tradeoff for background automation because it
delivers real throughput while still stopping honestly when the
providers themselves refuse work.

## 7. Provider error behavior

If the run starts with acceptable limits but the provider later returns:

- rate limit
- temporary unavailability
- transport failure
- timeout caused by the provider/runtime

then:

- preserve the current primitive as `in_progress` or `waiting_quota`
- record the error in runtime state
- stop cleanly
- allow the recurring runner to retry later

Do not downgrade a temporary provider failure into a permanent
primitive failure.

If the provider/runtime clearly indicates quota exhaustion or rate-limit
exhaustion, prefer:

- `waiting_quota` when recovery is expected after reset
- `in_progress` only if a review/build cycle is still live and should
  resume before moving on

## 8. Human-required cases

Stop and surface to the human if:

- the CLI status output is contradictory or malformed
- the automation cannot tell whether a provider failure is quota-related
  or some materially different failure

Do not guess on quota numbers when structured data exists.

Do not stop the whole factory merely because the preferred `/status`
path is unavailable non-interactively.

## 9. Reviewer/build loop interaction

Quota policy must not break review integrity.

If a primitive is mid-loop:

- prefer finishing the current review/fix cycle when above the
  continuation threshold
- do not abandon a primitive just because another one is next in line

The automation must always resume unfinished work before starting a new
primitive.
