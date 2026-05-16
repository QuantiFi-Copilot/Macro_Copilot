# Runbook — How to add a new playbook

> Step-by-step procedure for adding a new playbook to an agent. Use this when you are introducing a new instrument family, a new asset class, or splitting an over-broad existing playbook.

**Version:** v1
**Last reviewed:** 2026-05-17
**Audience:** any contributor (human or AI agent) introducing a new playbook.
**Prerequisite reading:** [`README.md`](README.md) in this folder — the contract every playbook honours. Read it once before starting; refer back when a step says *"per the contract."*
**Operationalises principles:** P1 (built right, not as a placeholder), P3 (every playbook follows the same shape), P7 (vendor SDK isolation), P8 (closed-family extension when adding a new `asset_class`), P11 (each agent owns its own playbooks).
**AC class:** Adding a new playbook is **Load-bearing** per [`../../00_thesis/02_ai_agent_development_contract.md`](../../00_thesis/02_ai_agent_development_contract.md). Run the full self-check; do not skip the gate.

---

## When to use this runbook

Use this runbook when you are:

- **Adding a new asset class** the platform does not yet cover (e.g., introducing the first FX, credit, or equity playbook). This is also a P8 closed-family extension on `asset_class` — file an ADR before the playbook is drafted.
- **Adding a new instrument family within an existing asset class** (e.g., adding a credit-derivatives playbook alongside the existing cash-credit playbook). No ADR required for the asset class itself; the playbook PR is the load-bearing artefact.
- **Splitting an over-broad existing playbook** into two for clarity, isolation, or coverage-gate sanity (e.g., breaking out a vol-surface playbook from a spot playbook).

## When NOT to use this runbook

If you are only **adding tickers to an existing universe** or **adding a vendor field to an existing playbook**, this runbook is overkill. Those are in-place edits to a playbook YAML, plus a coverage-aware re-ingestion. The contract still applies; the procedure is much shorter.

The dividing line: a new playbook gets a new `playbook_name` and a new file. An in-place edit keeps both.

## Pre-flight check — decide these BEFORE writing any YAML

A new playbook is hard to renamed-or-restructured after the first ingestion (audit history is keyed on `playbook_name`). Six decisions to make and document in the PR description before the YAML lands. **If any of these is uncertain, stop and ask the human (AC8).**

| # | Decision | What you are committing to |
|---|---|---|
| 1 | **Which agent owns this playbook?** | Per P11, each agent owns its own playbooks under `<agent>/playbooks/`. If the answer is "a new agent," the playbook PR depends on the new agent's package landing first. |
| 2 | **What is the `asset_class`?** | If the value already exists in `instrument_master.asset_class`, you are extending it — no ADR. If it is a new value, this is a P8 decision — file an ADR before drafting the playbook. |
| 3 | **What is the per-row family axis?** | The taxonomy primitives will filter on (rates uses `curve_family`; FX would likely use `pair_family`; etc.). Pick once for the asset class and use it consistently. If a family already exists in the asset class, you are extending it. |
| 4 | **What is the expected universe size?** | Rough count of `universe` rows. Drives the coverage-gate sanity check on first load (the gate is 80% of prior — first load has no prior, so the count is the baseline). |
| 5 | **What is the historical `start_date`?** | The earliest date the extractor will pull. This determines first-load size; later reductions delete history. Picking a too-early date wastes ingestion; picking a too-late date forces a re-pull later. |
| 6 | **Does the source-of-record vendor publish every field you need?** | List every `target_metric` and `reference_metric`. For each, confirm the field exists with the documented mnemonic. A missing vendor field is the most common reason a playbook fails its first ingestion. |

Write these answers in the PR description. The reviewer reads them before reading the YAML.

## Step 1 — Decide where the playbook lives

The playbook YAML lives at `<agent>/playbooks/<playbook_name>.yml`.

- For an existing agent (rates today), this is `rates_agent/playbooks/<name>.yml`.
- For a new agent, the agent's package must already exist (per P11, agents are sibling packages, never inherited). The agent's `__init__.py` and `playbooks/` directory should land in an earlier PR or the same PR.

`<playbook_name>` is lowercase snake_case, singular noun phrase describing the universe — `sovereign_bonds`, `ois`, `bond_futures`, `inflation_swaps`. Match the filename stem exactly to the value of the `playbook_name` key inside the file.

## Step 2 — Draft the YAML

Write the file. Required top-level keys, in this order (the order is conventional, not enforced, but matching it speeds review):

```yaml
playbook_name: <new_name>
playbook_version: "1.0"           # always start at 1.0 for a new playbook
asset_class: <existing_or_adr_approved_value>
dataset_name: <usually equal to playbook_name>
description: |
  One paragraph. What this universe covers, why it exists, who reads it.
  Mention any asset-class-specific gotchas reviewers should know.

extraction:
  start_date: "YYYY-MM-DD"
  incremental_window_days: <int>

target_metrics:
  - metric_id: <internal_name>
    bloomberg_field: <VENDOR_MNEMONIC>
  # ...

reference_metrics:
  - column_name: <internal_name>
    bloomberg_field: <VENDOR_MNEMONIC>
  # ...

universe:
  - ticker: <vendor_ticker>
    instrument_type: <asset_class_appropriate_type>
    # ... per-row fields (family, country, currency, tenor, domain-specific) ...
  # ...
```

**Per the contract** ([`README.md`](README.md)), every row in `universe` must carry `ticker` and `instrument_type`. The other per-row fields are asset-class-specific; mirror the family axis decision from Pre-flight Decision 3 across every row.

**Vendor field validation while drafting.** For each `bloomberg_field`, confirm the mnemonic exists. If you are not certain, look it up before committing — invented mnemonics cost a failed extraction round-trip and confuse the audit trail. If a field genuinely does not exist in the vendor, the right answer is either to drop it from the playbook or to refuse the playbook (per P6) — never to ship a mnemonic that fails at runtime.

**Domain-specific fields and `attributes`.** Any per-row field that is asset-class-specific and not already a typed column on `instrument_master` lands in the `attributes` JSONB after ingestion. The playbook still declares these fields explicitly per row; the ingester routes them. If a field becomes query-hot for the asset class, it gets promoted to a typed column via a schema migration in a separate PR (P8-flavoured; file an ADR for the migration).

## Step 3 — Run the extraction

Run the historical extractor against the new playbook. The standard invocation:

```bash
python utils/historical_extractor.py --playbook <playbook_name>
```

(The CLI accepts the playbook stem with or without `.yml`.)

The extractor:

1. Loads the playbook YAML.
2. Validates its shape (the top-level keys + the per-row required fields).
3. Pulls each `target_metric` and `reference_metric` from the source-of-record vendor for every ticker in `universe`, over the date range `[start_date, today]`.
4. Computes the playbook hash and the source-file (content) hash.
5. Writes a Parquet file to object storage with provenance columns (`playbook_hash`, `git_commit_hash`, `extractor_version`, `extracted_at`, `source_file`).

If the extractor reports a coverage ratio significantly below 100% (e.g., many tickers returned zero rows), stop and investigate **before** running the ingestion. The 80% gate at ingestion time is the floor, not the target.

## Step 4 — Run the ingestion

Trigger ingestion. The ingester:

1. Lists Parquet files for this playbook.
2. Computes the content hash and compares to the latest successful `load_audit` for the same `playbook_name`. If they match, the load is skipped with status `SKIPPED_DUPLICATE` — that is the idempotency contract (Guarantee #3 in the contract).
3. Inserts a `load_audit` row with `status = RUNNING`.
4. Runs the **coverage gate**: counts the unique instruments in the incoming Parquet, compares to the previous successful load's count. If the ratio is below 0.8, aborts the load, marks the audit `FAILED`, leaves all prior data intact.
5. If the coverage gate passes, enters the atomic transaction: upserts to `instrument_master`, deletes prior `market_data_daily` rows (full scope in historical mode, window only in incremental), upserts the new daily rows, flips `load_audit` status to `SUCCESS`. All in one Postgres transaction. Failure anywhere rolls everything back.

**First load reality check.** On the very first load of a new playbook, there is no previous successful load to compare against — the coverage gate trivially passes. **This is the only run where the gate provides no protection.** Treat the first load with extra care: verify the universe count matches your Pre-flight Decision 4, verify the date range coverage is what you expected, verify the loaded fields are populated for all tickers (not just for some).

## Step 5 — Verify

Before declaring the playbook live, verify:

1. **Row counts.** `SELECT count(*) FROM macro_data.instrument_master WHERE asset_class = '<your_value>' AND ...;` matches your expected universe size.
2. **Field coverage per ticker.** For each `target_metric`, every ticker has at least the expected number of days of data. The schema does not enforce this; you check it manually on first load.
3. **Reference fields.** `attributes` JSONB on `instrument_master` carries every domain-specific field you declared in the playbook, with no silent drops.
4. **Audit row.** `SELECT * FROM macro_data.load_audit WHERE playbook_name = '<your_name>' ORDER BY ingested_at DESC LIMIT 1;` shows `status = 'SUCCESS'`, a non-empty `playbook_hash`, and a non-empty `source_file_hash`.
5. **Hash stability re-run.** Re-run the ingestion on the same Parquet. The second run should land with status `SKIPPED_DUPLICATE` (idempotency contract, Guarantee #3). If it does not, the content-hash logic has a bug and the playbook does not yet satisfy P4 — block the merge until fixed.

## Step 6 — Wire downstream

A playbook only adds value once primitives use it. Confirm at least one of the following is true at merge time, or open a follow-up:

- **An existing primitive already filters by the family field** and will pick up the new instruments automatically (typical for adding instruments to an existing curve_family / pair_family / sector). Verify with a primitive call before merging.
- **A new primitive PR is queued** that will consume the new universe. The playbook can merge first only when its consumption is genuinely planned, not aspirational. Per P1, do not merge "for future use" without the future use being on someone's plate.
- **A new operator or workflow template depends on this playbook.** Same rule: the consumer should be planned, not hypothetical.

If a playbook is genuinely needed for analytical reach the platform has not yet built, that is fine — but say so explicitly in the PR description.

## Common pitfalls

Things reviewers see repeatedly:

- **Inconsistent family field across rows.** Some rows have `curve_family: UST`, others have `curve_family: ust` (case drift), others omit it entirely. Primitives filter by exact match — these instruments will be invisible. Lint the file before committing.
- **A `start_date` earlier than the vendor's live data for the instrument.** The extractor will return empty rows for the early years; the audit row's `requested_start_date` will not match the real data start. Investigate vendor-side coverage before picking the date.
- **Forgetting to bump `playbook_version`.** A version of `1.0` on an edit that adds new rows produces a misleading audit trail (the new rows look like they were always there). Bump on every content change.
- **Adding a top-level key the contract does not have.** The five top-level keys are the universal contract. Adding a sixth is a P8 decision and requires an ADR.
- **Universe rows that are duplicates by `ticker`.** The unique key on `instrument_master` is `(vendor, vendor_ticker)`. Two rows with the same ticker will collide on upsert; the second wins, the first is silently lost. Deduplicate in the YAML.
- **Using `is_active: false` as a soft delete.** Per the contract, just remove the row. Carrying inactive rows pollutes downstream queries.
- **Treating `attributes` as a junk drawer.** Every JSONB field has implicit downstream consumers. Document the shape your playbook expects in the PR description, and confirm no primitive is silently broken by the new shape.
- **Skipping the first-load verification (Step 5).** The coverage gate does not run on the first load — Step 5 is your only protection. Reviewers should see the verification queries and their outputs in the PR description.

## PR review checklist

The reviewer signs off when each item is met. Cite the matching principle by ID; do not paraphrase (AC2).

- [ ] **Pre-flight Decision 1–6** answered explicitly in the PR description.
- [ ] **P11.** Playbook lives under the correct `<agent>/playbooks/` folder; not shared across agents.
- [ ] **P8 (if applicable).** A new `asset_class` value is accompanied by an ADR.
- [ ] **Contract — top-level keys.** All five required top-level keys are present; no extra top-level keys; the order is conventional.
- [ ] **Contract — required per-row fields.** Every row in `universe` carries `ticker` and `instrument_type`. The family field is present consistently across every row.
- [ ] **Vendor mnemonics.** Every `bloomberg_field` value is a real, documented field.
- [ ] **P1.** No `is_active: false` rows, no `# TODO: fix in v2` comments without a linked roadmap item, no placeholder universe rows ("just an example").
- [ ] **P4 idempotency.** Step 5's re-run check shows `SKIPPED_DUPLICATE` on the second ingestion.
- [ ] **Atomicity verified.** Step 4's audit row shows `SUCCESS`; no orphan `RUNNING` row from a failed prior attempt.
- [ ] **First-load verification.** Step 5's row counts, field coverage, and audit-row inspection are in the PR description.
- [ ] **Downstream wired.** Step 6 — primitive consumers either exist or are explicitly planned in a linked follow-up issue.
- [ ] **No `attributes` schema drift.** If the playbook introduces a new JSONB field shape, the expected schema is documented in the PR description and no existing primitive is broken by the addition.
- [ ] **Description.** The playbook's `description` block is one paragraph, names what is covered and what is not.
- [ ] **AC6.** Commit message ends with `Operationalises: P1, P3, P4, P5, P7, P11; AC1, AC3, AC5, AC6.` (adjust IDs to whichever apply).

## Version log

| Version | Date | Change | ADR |
|---|---|---|---|
| v1 | 2026-05-17 | Initial runbook for adding a new playbook. Pre-flight check (six decisions before any YAML), six-step procedure (location → draft → extract → ingest → verify → wire), common-pitfalls list, PR review checklist with principle-ID citations. | (pending) |
