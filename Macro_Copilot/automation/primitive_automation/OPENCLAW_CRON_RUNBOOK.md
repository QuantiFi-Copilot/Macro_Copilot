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
