# Testing And DB Validation Policy

This automation does NOT treat "pytest passed locally" as sufficient
validation for a primitive.

Every primitive must be tested extremely thoroughly in TWO layers:

1. offline deterministic tests
2. DB-backed validation against the real repo database stack

Both are mandatory unless a human explicitly overrides the rule.

## 1. Core principle

If a primitive computes something in Python, the automation must try to
validate that computation against an independent deterministic baseline.

In this repo, the strongest baseline is:

- equivalent SQL
- run against the real DB-backed container stack
- using read-only queries

This is the preferred grounding mechanism.

## 2. Required testing layers

### Layer A — Offline deterministic tests

These are still required:

- `tests/test_<tool>_compute.py`
- `tests/test_<tool>_wiring.py`
- schema/config validation
- `python -m shared.config.lint`

These should fail fast on:

- schema mistakes
- wiring mistakes
- config mistakes
- obvious math mistakes on synthetic data

### Layer B — DB-backed deterministic grounding

These are ALSO required:

- `tests/test_<tool>_sql_validation.py`
- any applicable parity fixture checks
- any equivalent SQL comparisons needed to validate the Python
  implementation

The DB-backed layer is not optional for a primitive in this automation.

If the primitive cannot yet be validated honestly against the DB, stop
and surface that as a blocker rather than pretending the primitive is
fully validated.

## 3. Container policy

DB-backed validation must be run through the repo's container/dev stack
so the test environment matches the repo's actual DB wiring as closely
as possible.

Relevant repo surfaces include:

- `docker-compose.yml`
- `rates-agent-dev`
- `tsdb`

Use the container/dev environment to run:

- SQL validation scripts
- DB-backed parity checks
- DB-backed deterministic cross-checks

Do NOT rely on a random host-local DB connection when the repo's
containerized path exists.

## 4. Read-only DB rule

The automation is NOT allowed to modify DB data.

Forbidden:

- `INSERT`
- `UPDATE`
- `DELETE`
- `UPSERT`
- `TRUNCATE`
- `CREATE`
- `DROP`
- `ALTER`
- ingestion jobs
- schema migrations
- data backfills
- playbook push/write jobs

Forbidden repo paths/jobs include, for example:

- `ingestion/ingest_parquet.py`
- mutating helpers in `database/database.py`
- any command that intentionally changes data or schema state

Allowed DB interaction is read-only validation only:

- `SELECT`
- deterministic comparisons
- read-only fixture capture when explicitly intended and reviewed

## 5. SQL-equivalent validation requirement

For every primitive, the automation should aim to answer:

> what is the closest independent SQL expression of this tool's logic,
> and does it match the Python result on real DB data?

That means the SQL validation runner should not be ceremonial.
It should independently reproduce the core computation as far as the
repo's current SQL surfaces allow.

Examples of what to validate independently:

- fetched rows / join logic
- spread arithmetic
- period changes
- rolling-window statistics where practical
- rank / percentile / z-score components where practical
- current snapshot values against DB-backed raw series

If a certain component cannot yet be reproduced honestly in SQL, state
that explicitly in the validator and compensate with other deterministic
checks. Do not silently skip the hard parts.

## 6. Parity and fixture discipline

If the primitive uses a parity-fixture pattern:

- fixture capture/regeneration must be deliberate
- fixture changes must be treated as methodology-sensitive
- fixtures must not be regenerated just to make a test pass

Parity is grounding, not a rubber stamp.

## 7. Minimum evidence required before claiming "done"

A primitive is not fully validated until the builder can point to:

- passing offline compute tests
- passing offline wiring tests
- passing config lint
- passing DB-backed SQL validation
- passing any applicable parity/fixture checks

Anything less must be described as incomplete.

## 8. Reviewer expectations

The reviewer should reject a primitive as not fully validated if:

- only offline tests were run
- SQL validation was skipped
- DB-backed validation was hand-waved
- the SQL validator does not independently reproduce the core logic
- the builder touched DB-writing code or ran mutating DB commands

## 9. If the DB-backed path is unavailable

If the container stack is unhealthy or unavailable, do NOT pretend full
validation happened.

Instead:

- report exactly what was run
- report exactly what DB-backed checks were skipped
- stop before approval

For this automation, "DB stack unavailable" means "cannot reach full
approval", not "ship anyway".
