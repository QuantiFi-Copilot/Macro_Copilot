# Discord Status Queries

These are the kinds of queries the Discord-facing `main` agent should
be able to answer by reading:

- `automation/primitive_automation/primitive_catalog.yaml`
- `automation/primitive_automation/primitive_runtime_state.yaml`

## Suggested queries

- `What is the primitive factory doing right now?`
- `Which primitive is active?`
- `What step is it on?`
- `What finished last?`
- `How many primitives are done, blocked, and remaining?`
- `What is waiting on quota?`
- `Why did the last run stop?`
- `What was the last Codex verdict?`
- `What are Claude and Codex limits right now?`

## Expected style

Status responses should be:

- factual
- concise
- grounded in repo state
- explicit about blockers or quota pauses

They should not:

- guess
- improvise hidden progress
- claim a primitive is done if the catalog/runtime state does not say so
