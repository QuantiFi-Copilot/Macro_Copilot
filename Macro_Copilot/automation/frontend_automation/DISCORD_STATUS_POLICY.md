# Discord Status Policy

This file defines what state the orchestrator must write so a separate
Discord-facing `main` agent can answer progress questions accurately.

## 1. Goal

The Discord agent should be able to answer useful status questions
without relying on hidden orchestrator memory.

That means the orchestrator must write durable repo state after every
meaningful transition.

## 2. Primary status surfaces

The Discord agent should read:

- `automation/frontend_automation/frontend_tool_catalog.yaml`
- `automation/frontend_automation/frontend_runtime_state.yaml`

The catalog owns per-tool state.

The runtime-state file owns summary / current-run state.

## 3. Minimum state the orchestrator must maintain

At minimum, keep these fields current in
`frontend_runtime_state.yaml`. Names listed here are the canonical
keys the Discord agent reads — do not invent synonyms.

| Field | Value domain | Notes |
|---|---|---|
| `orchestrator_status` | `idle`, `processing`, `waiting_quota`, `catalog_exhausted`, `human_required` | The single top-level state. `catalog_exhausted` triggers the cron entry-point short-circuit (`BACKGROUND_EXECUTION_POLICY.md` §8). |
| `current_tool` | catalog entry id, or `null` | The tool being actively worked on. |
| `current_tool_tool_name` | tool slug, or `null` | Display name for human/Discord readability. |
| `current_step` | free text but bounded vocabulary: `pre_flight_backend_audit`, `building`, `reviewing`, `applying_findings`, `committing`, `idle` | What the orchestrator is doing right now. |
| `last_successful_tool` | catalog entry id | Most recent tool that reached `done`. |
| `last_reviewer_heading` | `APPROVED` / `CHANGES REQUIRED` / `DEFER / DO NOT BUILD` / `null` | From `parse_reviewer_log.py`. |
| `last_stop_reason` | bounded vocabulary including `catalog_exhausted`, `pre_flight_backend_audit_missing`, `waiting_quota`, `reviewer_findings_structural_no_fix_attempt`, `reviewer_claude_quota_exhausted`, `environment_failure`, `human_required` | Why the previous wake stopped. |
| `last_error` | free text | Truncated error message from the last failure, if any. |
| `done` / `blocked` / `remaining` | int | Counts across the catalog. |
| `quota_state` | `ok`, `tight`, `waiting_quota`, `probe_unavailable` | Summary; numeric detail goes in `quota_snapshot`. |
| `quota_snapshot` | the normalized JSON from `QUOTA_AND_RESUME_POLICY.md` §2, or `null` | Last known 5h / weekly percentages per engine. |
| `quota_snapshot_fresh` | bool | False if the orchestrator is in §6 non-interactive fallback mode. |
| `reviewer_mode` | `claude_only` (single-engine factory; no fallback) | Which engine is currently active. See `QUOTA_AND_RESUME_POLICY.md` §10. |
| `reviewer_mode_history` | append-only list of `{at, from, to, reason, tool_id?}` | Single-entry record explaining the single-engine choice. P5 honest disclosure. |
| `catalog_exhausted_at` | UTC iso timestamp or `null` | Set when `orchestrator_status` first transitions to `catalog_exhausted`. |
| `last_updated` | UTC iso timestamp | Touched on every meaningful state transition. |
| `status_notes` | append-only list of `{at, kind, text}` | Where dismissed reviewer findings, blocked-scaffold discard decisions, and other audit notes go. |
| `human_required` | nullable object `{reason, tool_id, details}` | Set when the orchestrator escalates. |

## 4. Questions Discord should be able to answer

The written state should support queries like:

- What tool is active right now?
- What step is it on?
- What finished last?
- How many are done, blocked, and remaining?
- Why did the last run stop?
- What is waiting on quota?
- What is blocked and why?
- What did the reviewer say last?
- **Which reviewer engine is currently active, and why?** (read
  `reviewer_mode` + the tail of `reviewer_mode_history`)
- **Has the catalog been exhausted, and when?** (read
  `orchestrator_status` + `catalog_exhausted_at`)
- What are Claude limits right now?
- Are the limit numbers fresh, stale, or unavailable?

## 5. Writing discipline

Update the runtime-state file after:

- selecting a tool
- finishing a Claude build round
- finishing a reviewer round (either engine)
- (single-engine factory — no engine swap; quota exhaustion → clean `waiting_quota` stop per `DESIGN_PRINCIPLES.md` §10)
- changing a tool status
- stopping due to quota
- deciding to proceed under non-interactive quota-probe fallback mode
- stopping due to blocker
- **failing the pre-flight backend audit** for a tool (set
  `last_stop_reason: pre_flight_backend_audit_missing` and add a
  `status_notes` entry naming the missing artifact — backend folder, MCP wrapper, tool_metadata row, OR mockup PNG)
- **exhausting the catalog** (set `orchestrator_status:
  catalog_exhausted` and `catalog_exhausted_at`)
- reviewer tooling/runtime failure
- discarding blocked scaffold after a no-go / policy block

The status should never lag so badly that Discord reports stale or
misleading progress.

If the reviewer fails for an environment reason, that must be reflected
explicitly in:

- `last_error`
- `last_stop_reason`
- `current_step`
- `last_reviewer_heading` when available

If blocked generated code is discarded so the queue can continue, that
must also be reflected explicitly in:

- `last_stop_reason` when the run stops there
- `status_notes`
- the blocked tool's catalog entry

Discord should be able to answer:

- whether blocked scaffold was discarded or explicitly preserved
- whether the next tool is free to start
- whether the factory is blocked on quota
- or merely running without a fresh quota snapshot because `/status` is
  unavailable non-interactively

## 6. Tone of Discord status

Status should be factual and terse.

No hype.
No guessing.
No claiming success when the state only shows partial progress.
