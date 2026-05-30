# Discord Status Queries

These are the kinds of queries the Discord-facing `main` agent should
be able to answer by reading:

- `automation/frontend_automation/frontend_tool_catalog.yaml`
- `automation/frontend_automation/frontend_runtime_state.yaml`

## Suggested queries

- `What is the frontend factory doing right now?`
- `Which tool is active?`
- `What step is it on?`
- `What finished last?`
- `How many tools are done, blocked, and remaining?`
- `What is waiting on quota?`
- `Why did the last run stop?`
- `What was the last reviewer verdict?`
- `What are Claude limits right now?`

## Expected style

Status responses should be:

- factual
- concise
- grounded in repo state
- explicit about blockers or quota pauses

They should not:

- guess
- improvise hidden progress
- claim a tool is done if the catalog/runtime state does not say so
