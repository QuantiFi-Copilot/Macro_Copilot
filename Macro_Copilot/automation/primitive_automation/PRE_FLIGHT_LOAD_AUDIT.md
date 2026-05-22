# Pre-Flight: load_audit SUCCESS Check

Before building any primitive, the orchestrator (and the builder it
dispatches) MUST confirm that the primitive's underlying playbook has
been extracted + ingested into the live database.

**"The playbook file exists" is NOT the same as "the data is in Postgres."**

This document is the mandatory pre-flight gate the orchestrator runs
before invoking the builder for any primitive — even one previously
flagged `todo`. See also `TESTING_AND_DB_VALIDATION_POLICY.md` (the
two-layer testing rule) and `NO_GO_RULES.md` §11.

## 1. The check

For each primitive about to be built, run:

```sql
SELECT playbook_name, status, ingested_at
  FROM macro_data.load_audit
 WHERE playbook_name = '<the primitive's required playbook>'
   AND status = 'SUCCESS'
 ORDER BY ingested_at DESC
 LIMIT 1;
```

The catalog entry's `pre_flight_load_audit` field names the playbook to
query. If the catalog entry has multiple `required_playbooks`, run the
check for each.

A non-empty result row = OK to build. An empty result = block.

## 2. How to run it

The container/dev stack exposes the DB; the canonical invocation is:

```bash
docker exec macro-tsdb psql -U quantuser -d macrodata -tAc \
  "SELECT playbook_name, status, ingested_at \
     FROM macro_data.load_audit \
    WHERE playbook_name = '<playbook>' \
      AND status = 'SUCCESS' \
    ORDER BY ingested_at DESC LIMIT 1"
```

The orchestrator may either shell out to `docker exec` or query the DB
via the same `database.database.get_db_engine()` path the primitives'
SQL-validation tests use — either is acceptable provided the connection
goes through the repo's `tsdb` container.

## 3. What to do on a miss

If no SUCCESS row exists for the playbook:

1. **Do NOT build the primitive.** Do NOT dispatch the builder.
2. **Mark the primitive `blocked`** in `primitive_catalog.yaml` with
   `block_reason: "<playbook> ingestion not yet recorded in load_audit"`.
3. **Surface to the human** via `primitive_runtime_state.yaml`:
   ```yaml
   last_stop_reason: pre_flight_load_audit_missing
   human_required:
     reason: pre_flight_load_audit_missing
     missing_playbook: "<playbook>"
     primitive_id: "<catalog entry id>"
   ```
4. **Continue to the next eligible primitive** in the same wake — a
   pre-flight miss on ONE primitive does not halt the catalog loop; it
   just blocks that primitive. The orchestrator advances through the
   queue.

A failed pre-flight is NOT a primitive defect — it is a data-pipeline
state. The orchestrator must not pretend the SQL-validation layer will
work when the underlying data is not in the DB.

## 4. Read-only — no fix-up

This check is read-only. The orchestrator MUST NOT seed, ingest, or
otherwise mutate the DB to "satisfy" the check — that violates
`TESTING_AND_DB_VALIDATION_POLICY.md` §4 (no DB mutation) and
`NO_GO_RULES.md` §12.

If the data is missing, that is a human decision (re-run the ingestion
playbook for that family, or accept the primitive is blocked). Not an
automation decision.

## 5. Why this gate exists

- **P6 (no silent failure).** A primitive whose `sql_validation` runs
  against a missing / partial / stale dataset can claim "tests pass"
  while producing meaningless empty outputs. The pre-flight makes
  "data present" an explicit, loud gate — not an implicit assumption.
- **P12 (Bloomberg Accuracy Boundary).** P12's three-question framework
  requires the substrate to actually have the data; without a load_audit
  SUCCESS row, the question can't be answered.
- **PR6 (metadata sufficient).** PR6 extends naturally to "metadata
  actually in the database," not just "playbook lists it." This gate
  enforces the stricter reading.

## 6. When the check is satisfied

Once the playbook has a SUCCESS row, the orchestrator proceeds normally:

- Builder is dispatched (`run_claude_builder.sh`).
- Builder writes the four-file primitive.
- SQL-validation test runs against the real DB rows.
- Reviewer dispatched (`run_codex_reviewer.sh` or, on Codex-quota
  fallback, `run_claude_reviewer.sh`).

The pre-flight gates entry to that pipeline; it does not replace any
step inside it.
