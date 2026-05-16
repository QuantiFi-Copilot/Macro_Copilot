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

- `automation/primitive_automation/primitive_catalog.yaml`
- `automation/primitive_automation/primitive_runtime_state.yaml`

The catalog owns per-primitive state.

The runtime-state file owns summary / current-run state.

## 3. Minimum state the orchestrator must maintain

At minimum, keep these fields current:

- overall orchestrator status
- current primitive id
- current primitive display/tool name
- current active step
- last successfully completed primitive
- last reviewer heading
- last stop reason
- last error
- done count
- blocked count
- remaining count
- quota state
- latest normalized quota JSON
- latest quota note / whether the factory is proceeding with stale or
  unavailable quota numbers
- last updated timestamp

## 4. Questions Discord should be able to answer

The written state should support queries like:

- What primitive is active right now?
- What step is it on?
- What finished last?
- How many are done, blocked, and remaining?
- Why did the last run stop?
- What is waiting on quota?
- What is blocked and why?
- What did Codex say last?
- What are Claude and Codex limits right now?
- Are the limit numbers fresh, stale, or unavailable?

## 5. Writing discipline

Update the runtime-state file after:

- selecting a primitive
- finishing a Claude build round
- finishing a Codex review round
- changing a primitive status
- stopping due to quota
- deciding to proceed under non-interactive quota-probe fallback mode
- stopping due to blocker
- exhausting the catalog
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
- the blocked primitive's catalog entry

Discord should be able to answer:

- whether blocked scaffold was discarded or explicitly preserved
- whether the next primitive is free to start
- whether the factory is blocked on quota
- or merely running without a fresh quota snapshot because `/status` is
  unavailable non-interactively

## 6. Tone of Discord status

Status should be factual and terse.

No hype.
No guessing.
No claiming success when the state only shows partial progress.
