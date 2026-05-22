# OpenClaw Cron Runbook

This runbook describes the intended background scheduler shape for the
primitive factory.

## 1. Recommended execution style

Use an isolated recurring cron job.

Reasons:

- each wake gets a fresh transcript
- repo state remains the source of truth
- quota pauses and VM restarts are easier to recover from
- the main Discord chat does not get polluted with orchestrator context

## 2. Recommended cron behavior

The cron job should:

- run on a recurring schedule
- target the `primitive-orchestrator` agent
- use isolated session mode
- use extra-high thinking
- use `--no-deliver` so background runs do not spam Discord directly

Discord status should come from manual queries to the `main` agent,
which reads repo state.

## 3. Recommended command shape

The exact CLI surface may evolve, but the intended job shape is:

```bash
openclaw cron add \
  --name "Primitive factory" \
  --cron "*/15 * * * *" \
  --session isolated \
  --agent primitive-orchestrator \
  --thinking xhigh \
  --message "Run the primitive automation catalog loop from repo state. Read the primitive automation package, obey the quota policy, process as much of the catalog as possible, update runtime state, and stop cleanly on quota, blocker, human-required case, or catalog exhaustion." \
  --no-deliver
```

The schedule can later be tightened or loosened, but the semantics
should remain:

- recurring wakeups
- isolated runs
- no direct chat spam

## 4. Why not one immortal session

Do not rely on one endlessly running session because:

- provider quotas can interrupt it
- VM or gateway restarts can kill it
- hidden in-memory context is harder to trust than repo state

Recurring cron plus repo state is the durable model.

## 5. No-overlap rule

Only one active cron run may mutate catalog/runtime state at a time.

If OpenClaw exposes a concurrency control setting for cron, keep it at
one for this job.

## 6. Recovery behavior

When the cron wake stops because of:

- low quota
- provider error
- gateway restart
- runtime cap

the next wake should resume from:

- `primitive_catalog.yaml`
- `primitive_runtime_state.yaml`

without losing place in the queue.

## 7. Catalog-exhaustion short-circuit

When every primitive in `primitive_catalog.yaml` is in a terminal
status (`done` or `blocked`), the orchestrator sets
`orchestrator_status: catalog_exhausted` in
`primitive_runtime_state.yaml` (see
`BACKGROUND_EXECUTION_POLICY.md` §8 for the full state contract).

The cron entry-point MUST honour that flag:

- Before dispatching the orchestrator prompt to a worker engine,
  read `primitive_runtime_state.yaml`.
- If `orchestrator_status == "catalog_exhausted"`, log a single
  line like
  `catalog exhausted at <catalog_exhausted_at>; skipping wake`
  to the cron wake log and exit 0 immediately. Do NOT invoke
  Claude or Codex. Zero worker quota is spent.
- Otherwise, dispatch the orchestrator normally.

The cron job itself remains in OpenClaw's schedule — the
short-circuit is *inside* the entry-point script. This keeps the
factory armed to resume the moment a human re-arms it (by appending
a new catalog entry or by clearing `orchestrator_status`).

Recommended entry-point sketch (the actual script lives at
`automation/primitive_automation/run_orchestrator_wake.sh` or
equivalent — adapt to whatever the cron job's `--message` /
entry-point actually invokes):

```bash
#!/usr/bin/env bash
set -euo pipefail

cd "$REPO_ROOT"
bash automation/primitive_automation/check_branch.sh

STATE=automation/primitive_automation/primitive_runtime_state.yaml
status="$(awk -F': *' '$1=="orchestrator_status" {print $2; exit}' "$STATE")"
if [[ "$status" == "catalog_exhausted" ]]; then
  ts="$(awk -F': *' '$1=="catalog_exhausted_at" {print $2; exit}' "$STATE")"
  echo "$(date -Is) catalog exhausted at ${ts:-unknown}; skipping wake"
  exit 0
fi

# Normal dispatch path follows: write the orchestrator prompt to a temp
# file, then invoke the OpenClaw orchestrator agent (or the appropriate
# wrapper) with that prompt.
...
```

This rule exists to protect the user's token budget — there is no
point waking the factory if there is nothing eligible for it to do.

## 8. Reviewer-engine fallback awareness

The cron job itself does not need to know about Codex vs Claude
reviewer mode — that decision lives inside the orchestrator and
flips `reviewer_mode` in `primitive_runtime_state.yaml` per
`QUOTA_AND_RESUME_POLICY.md` §10.

The only cron-layer implication is that a cron wake which stops
with `last_stop_reason: all_reviewer_engines_quota_exhausted`
should be treated like any other `waiting_quota` stop — the next
scheduled wake retries, and the orchestrator re-probes engine
quotas at the top of that wake.
