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

## 10. Codex → Claude reviewer fallback

The primary reviewer engine is Codex (`run_codex_reviewer.sh`). Codex
quota is the most likely single point of failure for the automation
because the same engine reviews every primitive. To keep the factory
running when Codex quota is exhausted, the orchestrator falls back to
Claude as the reviewer (`run_claude_reviewer.sh`) using the SAME
`REVIEWER_PROMPT.md`.

### 10.1 Detection — quota-marker scan of the reviewer log

After every reviewer dispatch, the orchestrator runs:

```
python3 automation/primitive_automation/parse_reviewer_log.py <log_file>
```

The parser returns:

```json
{
  "ok": true,
  "heading": "APPROVED" | "CHANGES REQUIRED" | "DEFER / DO NOT BUILD" | null,
  "environment_failure": <bool>,
  "reviewer_quota_exhausted": <bool>,
  "log_file": "<path>"
}
```

`reviewer_quota_exhausted: true` means the parser found a known
quota / rate-limit marker in the log AND no valid heading was
emitted. The marker list is curated inside `parse_reviewer_log.py`
(`QUOTA_EXHAUSTED_MARKERS`) and includes the Codex CLI's "rate
limit", "429", "weekly limit", "5-hour limit", "limit reached" style
strings plus Anthropic's `anthropic-ratelimit-*` and
`overloaded_error` strings (in case a future build switches the
primary engine to Claude).

If a valid heading WAS emitted alongside a quota-marker substring,
the parser returns `reviewer_quota_exhausted: false` — the verdict
stands, since the worker reached a conclusion despite an
upstream blip.

### 10.2 State change on fallback trigger

When `reviewer_quota_exhausted: true` AND `heading: null`:

1. Read the current `reviewer_mode` from
   `primitive_runtime_state.yaml`.
2. If `reviewer_mode == codex`:
   - Append to `reviewer_mode_history`:
     ```yaml
     - at: <UTC iso>
       from: codex
       to: claude_fallback
       reason: codex_quota_exhausted
       primitive_id: <current primitive id>
     ```
   - Set `reviewer_mode: claude_fallback`.
   - Leave the *primitive* status untouched (it is still
     `in_progress`; no review verdict was produced).
   - Re-dispatch the SAME reviewer prompt for the SAME primitive
     through `run_claude_reviewer.sh`. Parse the resulting log
     normally.
3. If `reviewer_mode == claude_fallback`:
   - BOTH engines are now in quota-exhausted territory in this wake.
   - Set `last_stop_reason: all_reviewer_engines_quota_exhausted`.
   - Set the primitive to `waiting_quota`.
   - Stop the wake cleanly. The recurring scheduler will retry
     later.
   - Do NOT invent a verdict and do NOT downgrade the primitive's
     status to `blocked`.

### 10.3 State change on fallback recovery

The fallback is sticky on purpose — once swapped to
`claude_fallback`, the orchestrator does not flip back to `codex`
based on time alone. It MAY flip back when ALL of the following
hold at the top of a subsequent wake:

- a fresh quota probe (per §2) returns Codex 5-hour-remaining at or
  above the §4 "starting a new primitive" threshold, AND
- the previous primitive completed cleanly under
  `claude_fallback` (i.e. ended `done`, not `blocked` or
  `waiting_quota`), AND
- the orchestrator is between primitives (not mid-review-loop).

When all three hold, append the symmetric history entry
(`from: claude_fallback, to: codex, reason: codex_quota_recovered`)
and set `reviewer_mode: codex` for the next primitive.

If the §2 probe is unavailable in non-interactive mode (the §6
fallback path), stay in `claude_fallback`. Over-using Claude is not
a correctness problem; flipping back to a still-exhausted Codex is.

### 10.4 What the fallback does NOT change

- The reviewer prompt (`REVIEWER_PROMPT.md`) is byte-identical for
  both engines. The review bar does not relax in fallback mode.
- The review-adjudication rules in `ORCHESTRATOR_PROMPT.md` (which
  findings are mandatory-fix vs dismissable, the 4-round cap) apply
  unchanged. Verdicts from either engine count toward the cap.
- The two-layer testing rule, the no-DB-mutation rule, and the
  pre-flight `load_audit` gate apply unchanged.

### 10.5 Why this rule exists

Halting the factory on Codex quota exhaustion would leave Claude
quota — which is typically larger and on a different reset clock —
sitting unused. The fallback path uses available quota to keep
primitives advancing while staying honest about which engine
reviewed which build (the `reviewer_mode_history` audit trail is the
P5 disclosure).

The alternative — running everything through Claude as the primary
reviewer — gives up the model-independence the Codex primary
provides (a different lab's model is the most stress-testable second
opinion). The fallback structure preserves that independence on the
happy path and degrades gracefully when Codex quota runs dry.
