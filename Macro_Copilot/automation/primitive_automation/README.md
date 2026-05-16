# Primitive Automation

This directory is the repo-local control surface for the first OpenClaw
automation vertical:

- process the primitive catalog one primitive at a time
- on branch `primitive_automation` only
- using the existing Claude Code -> Codex review loop
- following the repo's already-proven primitive architecture
- without inventing proxies or weakening methodology honesty

This package is intentionally repo-local so the automation reads the
same source of truth the human workflow already used successfully:

- architecture docs
- migrated reference tools
- current tests
- current workflow registration rules
- technical-debt blockers

## Scope of this automation

This automation is ONLY for building primitive tools.

It does NOT own:

- operators
- workflow templates
- frontend work
- orchestrator logic outside the primitive loop
- backlog / prioritisation decisions

It is also intentionally limited to:

- one primitive at a time
- one branch only: `primitive_automation`
- one build-review-fix-review loop at a time

## Primitive catalog

The primitive catalog now lives in:

- `primitive_catalog.yaml`

It contains only:

- the first VM automation batch
- primitives from the three remaining instrument families we selected
- entries whose required market data / metadata already exist in the
  current playbooks and repo

It intentionally excludes:

- blocked futures primitives requiring missing CTD / conversion-factor /
  repo metadata
- primitives that would require a proxy or opinionated shortcut

## Files in this package

- `DESIGN_PRINCIPLES.md`
  Core rules the builder and reviewer must obey.
- `STANDARD_TOOL_AND_YAML_RULES.md`
  Exact standard-tool definition plus YAML/config-offloading contract.
- `PRIMITIVE_BUILD_RULES.md`
  Step-by-step build rules for each primitive.
- `TESTING_AND_DB_VALIDATION_POLICY.md`
  Mandatory two-layer test policy: offline deterministic tests plus
  read-only DB-backed SQL validation in the repo container stack.
- `NO_GO_RULES.md`
  Hard stop conditions. If any of these fire, the primitive must be
  deferred, not approximated.
- `DONE_DEFINITION.md`
  What "finished" means for a primitive in this repo.
- `REPO_REFERENCE_MAP.md`
  Exact files the automation must consult before building/reviewing.
- `ORCHESTRATOR_PROMPT.md`
  Prompt for the OpenClaw orchestrator.
- `CLAUDE_BUILDER_PROMPT.md`
  Prompt for the Claude Code builder.
- `CODEX_REVIEWER_PROMPT.md`
  Prompt for the Codex reviewer.
- `primitive_catalog.yaml`
  The ordered primitive backlog for this automation slice.
- `primitive_runtime_state.yaml`
  Durable runtime/state surface for quota state, current primitive,
  current step, counts, and Discord-readable progress reporting.
- `run_claude_builder.sh`
  Canonical VM wrapper for Claude Code builder runs with explicit
  model, effort, branch check, and log capture.
- `run_codex_reviewer.sh`
  Canonical VM wrapper for Codex reviewer runs with explicit model,
  reasoning effort, sandbox mode, branch check, and log capture.
- `parse_codex_reviewer_log.py`
  Helper that extracts the reviewer heading and flags environment
  failures from the reviewer log.
- `QUOTA_AND_RESUME_POLICY.md`
  Limit checks, thresholds, stop conditions, and resume rules.
- `BACKGROUND_EXECUTION_POLICY.md`
  Background run model: recurring wakeups, catalog exhaustion loop, and
  restart semantics.
- `DISCORD_STATUS_POLICY.md`
  What status the orchestrator must write so Discord can answer useful
  progress questions.
- `OPENCLAW_CRON_RUNBOOK.md`
  Recommended background scheduler shape for the primitive factory.
- `DISCORD_STATUS_QUERIES.md`
  Sample progress questions the Discord-facing `main` agent should be
  able to answer from repo state.
- `check_branch.sh`
  Hard preflight that refuses to run unless the repo is checked out on
  `primitive_automation`.
- `pre-commit-branch-guard.sh`
  Local hook template that blocks commits on any branch other than
  `primitive_automation`.
- `pre-push-branch-guard.sh`
  Local hook template that blocks pushes from any branch other than
  `primitive_automation`.

## Intended loop

1. Run `check_branch.sh`.
2. Read the next eligible primitive catalog entry from
   `primitive_catalog.yaml`.
3. Read:
   - `DESIGN_PRINCIPLES.md`
   - `STANDARD_TOOL_AND_YAML_RULES.md`
   - `PRIMITIVE_BUILD_RULES.md`
   - `TESTING_AND_DB_VALIDATION_POLICY.md`
   - `NO_GO_RULES.md`
   - `DONE_DEFINITION.md`
   - `REPO_REFERENCE_MAP.md`
   - `QUOTA_AND_RESUME_POLICY.md`
   - `BACKGROUND_EXECUTION_POLICY.md`
   - `DISCORD_STATUS_POLICY.md`
4. Check Claude and Codex limits and normalize them into the required
   JSON contract before starting new work.
5. The orchestrator writes the builder prompt to a temp file and runs
   `run_claude_builder.sh` for the current primitive.
6. The orchestrator writes the reviewer prompt to a temp file and runs
   `run_codex_reviewer.sh` for an independent review.
7. Claude fixes valid findings only.
8. Codex re-reviews.
9. When a primitive is approved, the orchestrator immediately advances
   to the next eligible primitive in the same run.
10. The run stops only when:
   - the catalog is exhausted
   - a primitive is honestly blocked
   - quota is too low to continue safely
   - a hard platform/runtime error occurs
   - or human input is genuinely required

If a primitive is blocked after scaffold has already been generated:

- the blocker must be recorded truthfully
- the diff must not be left in-tree and allowed to contaminate the next
  primitive
- default behavior is to discard blocked scaffold and continue
- only an explicit human preservation instruction may keep blocked work
  in the tree

Approval requires:

- offline deterministic tests
- DB-backed SQL validation through the repo container/dev stack
- no DB mutation

Background operation is intentionally:

- wake up
- process as much of the catalog as possible
- stop cleanly on quota / blocker / completion
- wake again later and resume from repo state

It is not intended to depend on one immortal in-memory chat session.

## Branch rule

This automation may only:

- read
- write
- test
- commit

while the current branch is exactly:

`primitive_automation`

It may NOT:

- switch branches
- create side branches
- merge into any other branch
- rebase onto any other branch
- cherry-pick from any other branch
- push changes intended for any other branch

This is a deliberate simplification for V1.

## Optional local hook hardening

If you want an additional local guardrail beyond the prompts and
`check_branch.sh`, use:

- `pre-commit-branch-guard.sh`
- `pre-push-branch-guard.sh`

as templates for `.git/hooks/pre-commit` and `.git/hooks/pre-push`.

They are not auto-installed by this package, but they are provided as
strict local guardrail templates.

## Why this exists

The repo already proved one strong manual pattern:

- clear design principles
- Claude Code generates
- Codex reviews independently
- Claude fixes valid issues
- Codex signs off

This package exists to preserve that exact discipline while making the
loop automatable.

On the VM, the preferred worker path is now explicit wrapper scripts
rather than looser ad hoc worker invocations, so model, effort, sandbox,
and log behavior stay deterministic.
