# Runbook — How to add a new playbook

> Step-by-step procedure for adding a new playbook to an agent. Use this when you are introducing a new instrument family, a new asset class, or splitting an over-broad existing playbook.

**Version:** v1.2
**Last reviewed:** 2026-05-20
**Audience:** any contributor (human or AI agent) introducing a new playbook.
**Prerequisite reading:** [`README.md`](README.md) in this folder — the contract every playbook honours. Read it once before starting; refer back when a step says *"per the contract."*
**Operationalises principles:** P1 (built right, not as a placeholder), P2 (accuracy), P3 (every playbook follows the same shape), P5 (honest disclosure), P7 (vendor SDK isolation), P8 (closed-family extension when adding a new `asset_class`), P11 (each agent owns its own playbooks), P12 (source-of-record boundary).
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

A new playbook is hard to renamed-or-restructured after the first ingestion (audit history is keyed on `playbook_name`). Seven decisions to make and document in the PR description before the YAML lands. **If any of these is uncertain, stop and ask the human (AC8).**

| # | Decision | What you are committing to |
|---|---|---|
| 1 | **Which agent owns this playbook?** | Per P11, each agent owns its own playbooks under `<agent>/playbooks/`. If the answer is "a new agent," the playbook PR depends on the new agent's package landing first. |
| 2 | **What is the `asset_class`?** | If the value already exists in `instrument_master.asset_class`, you are extending it — no ADR. If it is a new value, this is a P8 decision — file an ADR before drafting the playbook. |
| 3 | **What is the per-row family axis?** | The taxonomy primitives will filter on (rates uses `curve_family`; FX would likely use `pair_family`; etc.). Pick once for the asset class and use it consistently. If a family already exists in the asset class, you are extending it. |
| 4 | **What is the expected universe size?** | Rough count of `universe` rows. Drives the coverage-gate sanity check on first load (the gate is 80% of prior — first load has no prior, so the count is the baseline). |
| 5 | **What is the historical `start_date`?** | The earliest date the extractor will pull. This determines first-load size; later reductions delete history. Picking a too-early date wastes ingestion; picking a too-late date forces a re-pull later. |
| 6 | **Does the source-of-record vendor publish every field you need?** | List every `target_metric` and `reference_metric`. For each, confirm the field exists with the documented mnemonic. A missing vendor field is the most common reason a playbook fails its first ingestion. |
| 7 | **Which non-vendor convention metadata is manually encoded, and how was it verified?** | List every hard-coded convention or metadata field that is not pulled as a target/reference metric: day counts, roll conventions, settlement conventions, index lags, interpolation methods, fixing calendars, payment delays, etc. For each, record the verification source and the scope checked. Do not assume one value applies across every tenor, curve, country, or subtype until that uniformity has been verified. |

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

**Manual metadata validation while drafting.** Treat every manually encoded convention field as a data claim, even when it lands in `attributes` JSONB rather than in a typed column. Do not hard-code `index_lag`, `interpolation`, day-count conventions, roll conventions, calendars, settlement conventions, payment delays, or similar fields unless you have verified both:

1. **The value itself** — from Bloomberg if it exposes the convention cleanly, otherwise from a manual Bloomberg-screen check and/or authoritative market documentation.
2. **The scope where the value is reused** — across the maturities, tenors, curve families, countries, currencies, indices, and instrument subtypes where the YAML repeats it.

During Phase A / candidate drafting, mark unverified convention fields explicitly:

```yaml
index_lag: "3M"          # CANDIDATE CONVENTION — manually verify by market
interpolation: "Daily"   # CANDIDATE CONVENTION — manually verify by market
```

In the final playbook, replace the candidate marker with the verification evidence or cite it in the PR description:

```yaml
fixed_leg_day_count: ACT/360  # VERIFIED MANUAL 2026-05-20 — Bloomberg SWPM screen, USD SOFR all tenors
```

If the metadata varies, encode it row-by-row. The existing OIS playbook is the model: its leg day-count conventions vary by curve family, and its roll conventions vary by tenor for some curves. A reviewer should reject a broad copy-paste convention if the PR does not show that the convention was checked at the same granularity where it is applied.

**Domain-specific fields and `attributes`.** Any per-row field that is asset-class-specific and not already a typed column on `instrument_master` lands in the `attributes` JSONB after ingestion. The playbook still declares these fields explicitly per row; the ingester routes them. If a field becomes query-hot for the asset class, it gets promoted to a typed column via a schema migration in a separate PR (P8-flavoured; file an ADR for the migration).

## Step 3 — Sync the playbook to GCS (the GCP playbooks bucket)

**This step is easy to miss and is the most common reason a first extraction "doesn't see" the new playbook.** The extractor does **not** read playbooks from your local working tree; it pulls them from the GCS playbooks prefix (`gs://macro-storage-bucket/playbooks/`). You must push the local YAML up first.

The standard invocation:

```bash
python utils/push_playbooks.py
```

This walks the local `rates_agent/playbooks/` directory and uploads every `.yml` / `.yaml` file to `gs://macro-storage-bucket/playbooks/`.

**Two operational warnings on `push_playbooks.py`:**

1. **It is currently hardcoded to `rates_agent/playbooks/`.** If you are adding a playbook for a future non-rates agent (FX, credit, equities), the script as-shipped will not pick it up. The fix is either to generalise the script to walk every `<agent>/playbooks/` folder, or to run a per-agent equivalent. Treat this as a blocker for new-agent playbook work and address it in the same PR or as a documented prerequisite.
2. **It overwrites by filename.** A push will replace whatever YAML currently exists at that GCS path. There is no version checking at the sync layer. The version discipline lives at the `playbook_version` field inside the file (and is stamped into `load_audit` on every extraction).

After the sync, verify the playbook is in the bucket:

```bash
gsutil ls gs://macro-storage-bucket/playbooks/ | grep <playbook_name>
```

## Step 4 — Run the extraction

Run the historical extractor against the new playbook. The standard invocation:

```bash
python utils/historical_extractor.py --playbook <playbook_name>
```

(The CLI accepts the playbook stem with or without `.yml`.)

The extractor:

1. **Pulls all playbook YAMLs from GCS** into a temp directory (filtered to `--playbook` if specified).
2. Loads each YAML via `yaml.safe_load`.
3. Filters universe rows to those carrying a `ticker`; filters target metrics to those carrying a `bloomberg_field`. (No strict schema validator today — see the README's *Implementation reality* section.)
4. Pulls each `target_metric` and `reference_metric` from the source-of-record vendor (Bloomberg today) for every ticker in `universe`, over the date range `[start_date, today]`.
5. Computes the **raw `playbook_hash`** (SHA-256 of the YAML bytes) — used for provenance stamping in `load_audit`.
6. Runs the **extractor's 90% coverage gate**: if fewer than 90% of `universe` tickers returned data, the extractor refuses to upload the Parquet at all.
7. If the gate passes, writes a Parquet file to GCS under `gs://macro-storage-bucket/data/` with provenance columns (`playbook_hash`, `git_commit_hash`, `extractor_version`, `extracted_at`, `source_file`, etc.).

If the extractor aborts on the 90% gate, do not relax the threshold to push past it. Diagnose the failed tickers first (often a vendor field is wrong, or a ticker has been retired). The 90% gate exists to stop a partial extraction from ever reaching the ingester; bypassing it defeats its purpose.

## Step 5 — Run the ingestion

Trigger ingestion. The ingester:

1. Lists Parquet files for this playbook in `gs://macro-storage-bucket/data/`.
2. Computes the **normalised content hash** (over the Parquet content, excluding lineage stamps) and compares it to the `source_file_hash` of the latest successful `load_audit` row for the same `playbook_name`. If they match, **a fresh `SKIPPED_DUPLICATE` row is still inserted into `load_audit`** (so the audit trail records the dedup event) and the destructive section is skipped — that is the idempotency contract (Guarantee #3 in the contract).
3. If not a duplicate, inserts a `load_audit` row with `status = RUNNING` (this insert is **outside** the destructive transaction — so it persists even if the destructive section later rolls back, leaving a `RUNNING` row that the outer handler flips to `FAILED`).
4. Upserts `instrument_master` (also **outside** the destructive transaction — idempotent on `(vendor, vendor_ticker)`).
5. Runs the **ingester's 80% destructive coverage gate**: counts the unique instruments in the incoming Parquet, compares to the previous successful load's count. If the ratio is below 0.8, aborts the load, marks the audit `FAILED`, leaves all prior `market_data_daily` data intact.
6. If the destructive gate passes, enters the **critical (destructive) transaction**: deletes prior `market_data_daily` rows (full scope in `historical` mode, window only in `incremental`), upserts the new daily rows, flips `load_audit.status` to `SUCCESS`. All three operations inside one `with engine.begin() as conn:` block. Failure anywhere inside this block rolls all three back; the outer exception handler then flips the (already-persisted) audit row from `RUNNING` to `FAILED` on a separate transaction.

**First-load reality check.** On the very first load of a new playbook, there is no previous successful load to compare against — the ingester's 80% destructive gate trivially passes. The extractor's 90% gate still applies (it compares against the playbook's own universe), but neither gate gives you ticker-level data-quality assurance on first load. **Treat the first load with extra care:** verify the universe count matches your Pre-flight Decision 4, verify the date range coverage is what you expected, verify the loaded fields are populated for all tickers (not just for some). Step 6 below is the verification list.

## Step 6 — Verify

Before declaring the playbook live, verify:

1. **Row counts.** `SELECT count(*) FROM macro_data.instrument_master WHERE asset_class = '<your_value>' AND ...;` matches your expected universe size.
2. **Field coverage per ticker.** For each `target_metric`, every ticker has at least the expected number of days of data. The schema does not enforce this; you check it manually on first load.
3. **Reference fields.** `attributes` JSONB on `instrument_master` carries every domain-specific field you declared in the playbook, with no silent drops.
4. **Audit row.** `SELECT * FROM macro_data.load_audit WHERE playbook_name = '<your_name>' ORDER BY ingested_at DESC LIMIT 1;` shows `status = 'SUCCESS'`, a non-empty `playbook_hash`, and a non-empty `source_file_hash`.
5. **Dedup re-run check.** Re-run the ingestion on the same Parquet (or run the extractor + ingestion sequence a second time without any data changes). The second ingestion run should insert a fresh `SKIPPED_DUPLICATE` row into `load_audit` with the same `source_file_hash` as the first successful run, and the destructive section should not execute (no row count change in `market_data_daily`). If the destructive section runs again (re-deletes and re-inserts), the content-hash logic has a bug and the playbook does not yet satisfy P4 — block the merge until fixed.

## Step 7 — Wire downstream

A playbook only adds value once primitives use it. Confirm at least one of the following is true at merge time, or open a follow-up:

- **An existing primitive already filters by the family field** and will pick up the new instruments automatically (typical for adding instruments to an existing curve_family / pair_family / sector). Verify with a primitive call before merging.
- **A new primitive PR is queued** that will consume the new universe. The playbook can merge first only when its consumption is genuinely planned, not aspirational. Per P1, do not merge "for future use" without the future use being on someone's plate.
- **A new operator or workflow template depends on this playbook.** Same rule: the consumer should be planned, not hypothetical.

If a playbook is genuinely needed for analytical reach the platform has not yet built, that is fine — but say so explicitly in the PR description.

## Common pitfalls

Things reviewers see repeatedly:

- **Forgetting Step 3 (sync to GCS).** The most common first-time failure: the playbook is committed locally, the extractor is run, and nothing happens because the extractor never sees the new YAML — it reads from `gs://macro-storage-bucket/playbooks/`, not from the local working tree. Always `python utils/push_playbooks.py` before the extractor. (And remember the script is rates-hardcoded — see Step 3's warnings.)
- **Inconsistent family field across rows.** Some rows have `curve_family: UST`, others have `curve_family: ust` (case drift), others omit it entirely. Primitives filter by exact match — these instruments will be invisible. Lint the file before committing.
- **A `start_date` earlier than the vendor's live data for the instrument.** The extractor will return empty rows for the early years; the audit row's `requested_start_date` will not match the real data start. Investigate vendor-side coverage before picking the date.
- **Forgetting to bump `playbook_version` on a content-affecting change.** Cosmetic edits do not require a bump (the normalised dedup hash is unchanged), but adding/removing rows or metrics does. A wrong-version audit trail makes historical replay misleading.
- **Adding a new top-level key not in the eight-key contract.** The current extractor tolerates a handful of legacy operational keys, but new playbooks should not lean on them. A new *contractual* top-level key is a P8 decision and requires an ADR.
- **Universe rows that are duplicates by `ticker`.** The unique key on `instrument_master` is `(vendor, vendor_ticker)`. Two rows with the same ticker will collide on upsert; the second wins, the first is silently lost. Deduplicate in the YAML.
- **Using `is_active: false` as a soft delete.** Per the contract, just remove the row. Carrying inactive rows pollutes downstream queries.
- **Treating `attributes` as a junk drawer.** Every JSONB field has implicit downstream consumers. Document the shape your playbook expects in the PR description, and confirm no primitive is silently broken by the new shape.
- **Hard-coding convention metadata without evidence.** A value in `attributes` can still be wrong. If a playbook repeats `index_lag: 3M`, `interpolation: Daily`, `fixed_leg_day_count: ACT/360`, or similar convention metadata across rows, the PR must show that the value was verified and that the reuse scope is valid. If the convention varies by tenor, curve, country, index, or subtype, encode the variation row-by-row.
- **Skipping the first-load verification (Step 6).** Neither gate gives you ticker-level data-quality assurance on first load — Step 6 is your only protection. Reviewers should see the verification queries and their outputs in the PR description.
- **Bypassing the 90% extractor gate by ad-hoc lowering the threshold.** The gate exists to stop a partial extraction from ever reaching the ingester. Diagnose the failed tickers; do not lower the threshold to push past them.

## PR review checklist

The reviewer signs off when each item is met. Cite the matching principle by ID; do not paraphrase (AC2).

- [ ] **Pre-flight Decision 1–7** answered explicitly in the PR description.
- [ ] **Pre-flight Decision 7.** Every manually encoded convention / metadata field is listed with its verification source, scope checked, variation found, and final encoding.
- [ ] **P11.** Playbook lives under the correct `<agent>/playbooks/` folder; not shared across agents.
- [ ] **P8 (if applicable).** A new `asset_class` value is accompanied by an ADR.
- [ ] **Contract — top-level keys.** All eight required top-level keys are present; the playbook does not introduce new contractual top-level keys; if any of the legacy operational keys (`vendor`, `default_instrument_type`, `bdh_kwargs`, etc.) appear, the PR description justifies why.
- [ ] **Contract — required per-row fields.** Every row in `universe` carries `ticker` and `instrument_type`. The family field is present consistently across every row.
- [ ] **Vendor mnemonics.** Every `bloomberg_field` value is a real, documented field.
- [ ] **Manual metadata.** No hard-coded convention field remains unverified. Candidate conventions are either verified and documented, narrowed row-by-row, or deferred.
- [ ] **P1.** No `is_active: false` rows, no `# TODO: fix in v2` comments without a linked roadmap item, no placeholder universe rows ("just an example").
- [ ] **Step 3 done.** The local YAML has been synced to `gs://macro-storage-bucket/playbooks/` via `python utils/push_playbooks.py`. For a non-rates agent, any required `push_playbooks.py` generalisation is in this PR or an explicit prerequisite PR.
- [ ] **P4 idempotency.** Step 6's dedup re-run check shows a fresh `SKIPPED_DUPLICATE` audit row with matching `source_file_hash` on the second ingestion; no destructive section runs.
- [ ] **Atomicity verified.** Step 5's audit row shows `SUCCESS`; no orphan `RUNNING` row from a failed prior attempt.
- [ ] **First-load verification.** Step 6's row counts, field coverage, and audit-row inspection are in the PR description.
- [ ] **Downstream wired.** Step 7 — primitive consumers either exist or are explicitly planned in a linked follow-up issue.
- [ ] **No `attributes` schema drift.** If the playbook introduces a new JSONB field shape, the expected schema is documented in the PR description and no existing primitive is broken by the addition.
- [ ] **Description.** The playbook's `description` block is one paragraph, names what is covered and what is not.
- [ ] **AC6.** Commit message ends with `Operationalises: P1, P3, P4, P5, P7, P11; AC1, AC3, AC5, AC6.` (adjust IDs to whichever apply).

## Version log

| Version | Date | Change | ADR |
|---|---|---|---|
| v1.2 | 2026-05-20 | Added the manual metadata verification gate. New playbooks must now document every hard-coded convention field, the source used to verify it, and the scope checked for variation before the value can ship. Added candidate/final comment patterns, a common pitfall, and review checklist entries for convention metadata. | — |
| v1.1 | 2026-05-17 | Restructured to match the README's v1.1 factual corrections: (a) Inserted **Step 3 — Sync to GCS** as a first-class step (was the most common first-time failure: the extractor reads from `gs://…/playbooks/`, not the local working tree); flagged `push_playbooks.py`'s rates-hardcode as an operational gap for non-rates agents. (b) Renumbered the extraction / ingestion / verify / wire steps to 4 / 5 / 6 / 7; tightened the extractor description to name the actual YAML loader (`yaml.safe_load` with defensive filtering, no strict schema) and the 90% upload gate. (c) Tightened the ingester description to name the two coverage gates explicitly, clarify that the audit-RUNNING insert and `instrument_master` upsert happen outside the destructive transaction, and clarify that dedup re-runs insert a fresh `SKIPPED_DUPLICATE` audit row. (d) Common-pitfalls list updated: added "forgot Step 3 (sync to GCS)" as the leading pitfall; added "bypassing the 90% extractor gate." (e) PR review checklist: added a "Step 3 done" item; updated the top-level-keys check to match the README's eight-keys-plus-legacy-tolerance framing. | (pending) |
| v1 | 2026-05-17 | Initial runbook for adding a new playbook. Replaced by v1.1 the same day after a factual-review pass aligned with the README's corrections. | — |
