# Quota And Resume Policy — Frontend Factory

This file defines how the frontend orchestrator decides whether it can safely start (or continue) a wake, and what to do on quota exhaustion.

**Single-engine note (vs primitive factory).** Unlike the primitive factory which has Codex (primary) + Claude (fallback), this factory uses **Claude for both builder and reviewer with no fallback**. Quota exhaustion → clean `waiting_quota` stop → next cron wake retries.

**Version:** v1
**Status:** load-bearing operational policy.

---

## 1. The quota contract

The orchestrator tracks Claude CLI limits in `frontend_runtime_state.yaml` under `quota_state` + `quota_snapshot`.

Acceptable normalized values for `quota_state`:

- `not_probed` — initial state; the orchestrator hasn't tried to read limits yet
- `healthy` — fresh probe; current usage well below threshold; safe to start new work
- `tight` — fresh probe; usage approaching threshold; complete the current tool but do NOT start a new one
- `exhausted` — fresh probe OR runtime signal; provider has refused; STOP the wake
- `probe_unavailable` — the CLI status path is unreadable from non-interactive automation; proceed in runtime-enforced mode

`quota_snapshot` carries the raw probe output (capacity values, reset times, percentages) for the audit trail.

`quota_snapshot_fresh` is a boolean: true if the probe was taken in the current wake; false if it's stale from a prior wake.

## 2. Quota check points

The orchestrator probes (or attempts to probe) Claude limits at:

1. **Wake start** — before any tool dispatch.
2. **Before starting each new tool** — after the previous tool committed.
3. **After every CHANGES REQUIRED → Dispatch 3** — fix passes can be expensive; re-check before the next tool.

The probe is best-effort:

```bash
claude /status   # may not be available in non-interactive mode
```

If the probe path is unreadable (e.g. `stdin is not a terminal` error), set `quota_state: probe_unavailable` and proceed.

## 3. Runtime-enforced quota mode

When the CLI status path is unavailable, the orchestrator falls back to **runtime detection**:

- It does NOT halt the factory on the missing probe alone.
- It proceeds with the next tool dispatch.
- When the actual provider returns a quota signal (HTTP 429, "you're out of usage", "rate-limited", "weekly limit"), the dispatch fails.
- The reviewer log is parsed by `parse_reviewer_log.py`, which sets `reviewer_quota_exhausted: true` when a quota marker is found AND no valid heading was emitted.
- The orchestrator then sets `quota_state: exhausted`, marks the run `waiting_quota`, and stops the wake cleanly.

This is the **single-engine equivalent** of the primitive factory's Codex→Claude fallback. There is NO fallback engine here — quota exhaustion means the factory waits.

## 4. Quota-threshold rules

Before starting a new tool, if `quota_state` is fresh:

- `healthy` → proceed
- `tight` → finish the in-progress tool (if any) AND mark `waiting_quota` after that; do NOT start a new one
- `exhausted` → STOP the wake cleanly; mark `orchestrator_status: waiting_quota`
- `probe_unavailable` → proceed in runtime-enforced mode

If `quota_state` is stale (`quota_snapshot_fresh: false`):

- Re-probe at wake start. If still unreadable, proceed in runtime-enforced mode.

## 5. The reviewer-quota detection pipeline

After each reviewer dispatch:

```bash
python3 automation/frontend_automation/parse_reviewer_log.py \
  /tmp/claude_frontend_reviewer_run_<tool_id>_round2.log
```

The parser returns JSON:

```json
{
  "ok": true,
  "heading": "APPROVED" | "CHANGES REQUIRED" | "DEFER / DO NOT BUILD" | null,
  "environment_failure": false,
  "reviewer_quota_exhausted": false,
  "log_file": "..."
}
```

- `reviewer_quota_exhausted: true` AND `heading: null` → set `quota_state: exhausted`, `last_stop_reason: reviewer_claude_quota_exhausted`, mark `waiting_quota`, STOP the wake.
- `environment_failure: true` → set `last_stop_reason: reviewer_environment_failure`, mark `waiting_quota`, STOP the wake.
- valid heading → proceed per `ORCHESTRATOR_PROMPT.md` §"Reviewer result rule" Cases C/D/E.

## 6. Resume rule

When a wake stops due to quota:

- `orchestrator_status: waiting_quota`
- `current_tool` stays set to the in-flight tool
- `dispatch_round` stays at whichever round was active
- `frontend_tool_catalog.yaml` entry stays `in_progress`

The next cron wake will:

1. Re-probe Claude limits.
2. If `healthy` (or `probe_unavailable`), re-enter the catalog loop with selection order in_progress → changes_required → waiting_quota → todo.
3. The in-flight tool's `current_step` indicates where to resume:
   - If `current_step: builder_dispatched` → re-dispatch the builder (the prior dispatch likely failed mid-stream; safe to retry).
   - If `current_step: reviewer_dispatched` → re-dispatch the reviewer (same).
   - If `current_step: fix_dispatched` → re-dispatch the fix (same).

Idempotency: each dispatch overwrites its working-tree changes; no per-dispatch state leaks across retries.

## 7. Stop conditions related to quota

- Claude probe returns `exhausted` → STOP wake.
- `reviewer_quota_exhausted: true` from `parse_reviewer_log.py` → STOP wake.
- Builder log shows a quota marker (the orchestrator greps the builder log post-dispatch with the same patterns the parser uses) → STOP wake.
- `quota_state: probe_unavailable` for more than 5 consecutive wakes with no actual quota signal → log warning; continue runtime-enforced.

## 8. Cost note (vs primitive factory)

The frontend factory's dispatches are typically:

- Builder Dispatch 1: ~80–150k tokens (read mockups + docs + reference module + write 8 files)
- Reviewer Dispatch 2: ~40–80k tokens (read diff + docs + mockups; emit verdict)
- Builder Dispatch 3 (optional fix): ~30–60k tokens (apply targeted findings)

Per-tool cost: ~120–290k tokens. Spread across 10 tools: ~1.2–2.9M tokens for the first batch.

Compared to the primitive factory (which has more rounds + Codex + Claude reviewer alternation), the frontend factory is lighter per tool BUT runs against the same Claude weekly limit.

## 9. Quota-related runtime fields

```yaml
quota_state: healthy | tight | exhausted | probe_unavailable | not_probed
quota_snapshot: |
  <raw probe output>
quota_snapshot_at: <iso>
quota_snapshot_fresh: true | false
quota_note: |
  <human-readable summary of last probe + decision>
```

## 10. Anti-patterns

- Attempting to switch reviewer engines on quota exhaustion (there is no alternative engine in this factory; primitive factory has Codex+Claude but this one does not).
- Continuing to dispatch builders after `reviewer_quota_exhausted: true` (the builder shares the same Claude quota; one signal triggers a clean wake stop).
- Marking the in-flight tool `blocked` on quota exhaustion (it's not blocked by anything intrinsic; it's waiting for quota — keep status `in_progress`).
- Probing quota mid-dispatch (the probe path is unreliable mid-stream; rely on the parser's post-dispatch signal).
- Falsely declaring `quota_state: healthy` when the probe was actually `probe_unavailable` (audit trail integrity).

## 11. Links

- `ORCHESTRATOR_PROMPT.md` §"Quota rule" + §"Reviewer result rule" Case A
- `parse_reviewer_log.py` — the quota-marker detection logic
- `DISCORD_STATUS_POLICY.md` — the `quota_state` field is exposed to the Discord `main` agent
- `BACKGROUND_EXECUTION_POLICY.md` — how the next wake resumes after a quota stop
