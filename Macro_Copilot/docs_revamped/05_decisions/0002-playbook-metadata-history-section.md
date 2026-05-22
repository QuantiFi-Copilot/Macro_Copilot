# ADR 0002 — `metadata_history` playbook section + extractor/ingester routing

**Status:** Accepted
**Date:** 2026-05-19
**Closes:** none directly. Builds on ADR 0001 (sibling SCD2 `instrument_metadata_history` table). Substrate for the data-first plan's Step 1.1 backfill.
**Operationalises principles:** P3 (one extractor / one ingester contract, no forked scripts), P8 (closed-family-style extension to the playbook contract), P10 (single source of truth for Bloomberg-fetch logic), P11 (each agent owns its playbooks under `<agent>/playbooks/`).
**Scope:** PR A1 of the data-first roadmap — **infrastructure only.** This ADR does NOT verify or commit to any production Bloomberg mnemonics; the example fields shown below are illustrative and live in fixtures. Production playbook edits (`bond_futures.yml`, `policy_futures.yml`) belong to PR A2, gated on operator-side mnemonic verification.

---

## Context

ADR 0001 created `macro_data.instrument_metadata_history` and the supporting DB helpers (`upsert_instrument_metadata_history`, `close_open_metadata_window`, `get_instrument_metadata_at`). The table is empty by design until a backfill populates it for the ~44 rolling-generic tickers (TY1, FV1, …, SFR1-8, ER1-8, SFI1-8, etc.).

The substrate question for PR A1 is: **where does the code that performs that backfill live, and what's the playbook-level declaration that triggers it?**

The existing extractor [`utils/historical_extractor.py`](../../utils/historical_extractor.py) is parameterised over `(playbook universe, target_metrics, reference_metrics, date window)`. It is flexible *within* one query pattern (`bdh()` time-series + `bdp()` reference-stamping) and one output shape (long-format parquet keyed by `(trade_date, ticker, field_name)`). The rolling-metadata backfill needs a fundamentally different query pattern (`bds(generic, "FUT_CHAIN", overrides…)` to enumerate the chain → `bdp(underlying_contract, static_fields…)` per chain member) and a fundamentally different output shape (effective-dated rows, one per `(instrument_id, effective_window)`).

The earlier draft of this work was a one-off script `utils/backfill_rolling_metadata.py`. That is the path of least resistance and the wrong call: every future rolling-contract universe (commodity futures, FX futures, eventual fixings) would face the same fork-vs-extend decision, and "two extractors in `utils/`" is exactly the kind of fragmentation that compounds across agents (P10 erosion).

## Decision

**Extend the existing extractor with a new declarative mode driven by a new optional playbook section, and extend the ingester with a path-prefix-driven route to `instrument_metadata_history`.** Same parquet → GCS → ingester → DB pattern as the rest of the system, so idempotency (dedup hash), audit trail (`load_audit` row per run), coverage gates, and atomicity (delete + upsert + audit-flip in one txn) are all reused — not re-implemented.

Three coordinated changes land in PR A1:

1. **Playbook contract gains an optional `metadata_history` section** (this ADR's primary load-bearing surface).
2. **`utils/historical_extractor.py` gains `--mode metadata-history`**, alongside the existing default `--mode time-series` (which is the entire existing behaviour, renamed only for clarity in CLI; the runtime path for time-series extraction is byte-identical).
3. **`ingestion/ingest_parquet.py` partitions GCS blobs by path prefix**: blobs under `gs://<bucket>/data/` continue to route to `market_data_daily` exactly as today; blobs under `gs://<bucket>/metadata_history/` route to `instrument_metadata_history` via the Step-0 helpers, with a defence-in-depth overlap-validation gate that runs before any DB write.

### What this ADR explicitly does NOT do

- **Does not commit to any production Bloomberg mnemonic** (`FUT_CHAIN`, `LAST_TRADEABLE_DT`, `FUT_FIRST_TRADE_DT`, `FUT_DLV_DT_LAST`, …). Every mnemonic shown below is illustrative. The verification step lives in PR A2 alongside the actual playbook edits, following the existing manual verification workflow.
- **Does not edit `bond_futures.yml` or `policy_futures.yml`.** Production playbooks gain a `metadata_history` section only in PR A2, after operator-side mnemonic verification.
- **Does not change the existing time-series extraction path.** No new SQL is run, no existing parquet shape changes, no existing test breaks. PR A1 is purely additive.
- **Does not modify `instrument_master` schema or `upsert_instrument_master`.** That stays as ADR 0001 left it.
- **Does not wire a "live append on every ingestion" path for rolling-contract metadata.** That is deferred to a later PR after the Step 1.1 backfill has populated the table once and proved out the read path.

## Playbook contract — new optional section

The playbook contract documented in [`docs_revamped/02_components/playbook/README.md`](../02_components/playbook/README.md) gains one new optional top-level key: `metadata_history`. **Existing playbooks are unaffected** — the field is opt-in, and the extractor's default `--mode time-series` does not read it. The contract version bumps from v1.1 → v1.2 alongside this ADR.

Shape:

```yaml
# Optional. Only present in playbooks whose universe includes rolling-contract
# tickers AND for which per-day-correct historical metadata is required by a
# consuming primitive.
metadata_history:
  enabled: true                                # Master switch. When false or absent,
                                               # this section is ignored even under
                                               # --mode metadata-history.
  chain_field: "FUT_CHAIN"                     # Bloomberg bds-mnemonic that enumerates
                                               # the underlying chain for a rolling
                                               # generic. (Illustrative; verify in A2.)
  chain_overrides:                             # Optional dict of override key-value
                                               # pairs passed to bds(). Use to pull
                                               # the full historical chain rather than
                                               # only currently-listed contracts.
                                               # (Illustrative; verify in A2.)
    INCLUDE_EXPIRED_CONTRACTS: "Y"
  roll_convention:                             # How to compute effective_from /
                                               # effective_to per underlying contract.
    type: "expiry_roll"                        # The only value supported in A1.
                                               # Future values (first_notice_roll,
                                               # n_days_before_expiry, …) require
                                               # an ADR amendment.
    roll_field: "LAST_TRADEABLE_DT"            # Per-contract bdp() field naming the
                                               # date on which the contract stops
                                               # being the front. The effective
                                               # window of contract C_i is
                                               #   from prior(C_i-1.roll_field) + 1
                                               #   to   C_i.roll_field
                                               # for sorted contracts within the
                                               # chain. The oldest contract starts at
                                               # its first-trade-equivalent field
                                               # below.
    first_trade_field: "FUT_FIRST_TRADE_DT"    # Used only for the chain's earliest
                                               # contract to anchor the window's
                                               # leftmost date.
  static_fields:                               # Per-underlying-contract bdp() fields.
                                               # column_name MUST match a typed
                                               # column on instrument_metadata_history
                                               # (see ADR 0001 schema); unknown names
                                               # are routed to the row's attributes
                                               # JSONB.
    - column_name: "contract_code"
      bloomberg_field: "TICKER"
    - column_name: "expiry_date"
      bloomberg_field: "LAST_TRADEABLE_DT"
    - column_name: "maturity_date"
      bloomberg_field: "FUT_DLV_DT_LAST"
    - column_name: "security_name"
      bloomberg_field: "SECURITY_DES"
    - column_name: "settlement_date"
      bloomberg_field: "FUT_DLV_DT_FIRST"
    - column_name: "tick_size"
      bloomberg_field: "FUT_TICK_SIZE"
    - column_name: "contract_size"
      bloomberg_field: "FUT_CONT_SIZE"
    - column_name: "exchange_code"
      bloomberg_field: "FUT_EXCH_NAME_SHRT"
    - column_name: "underlying_ticker"
      bloomberg_field: "UNDL_SPOT_TICKER"
```

The set of valid `column_name` values is exactly the set of typed columns on `instrument_metadata_history` declared in [`database/schema.sql`](../../database/schema.sql) section 4:

`contract_code, expiry_date, maturity_date, security_name, settlement_date, accrual_start_date, accrual_end_date, tick_size, tick_value, contract_size, exchange_code, underlying_ticker`

Any other `column_name` lands in the row's `attributes` JSONB. The validator catches typos at extraction time (a `column_name: "expirydate"` will neither hit a typed column nor be obviously useful in JSONB; the extractor warns but does not abort).

### Which universe rows are picked up by `--mode metadata-history`

Only rows in `universe` whose `is_rolling_contract: true` are passed to the chain-enumeration loop. Non-rolling rows in the same playbook are silently skipped — the same playbook can mix rolling and non-rolling tickers (rare in practice, but the contract allows it) and the metadata-history extraction is a no-op for the non-rolling rows.

## Extractor extension — concrete

The CLI gains `--mode {time-series,metadata-history}`. Default is `time-series` so existing operational scripts (`python utils/historical_extractor.py --playbook bond_futures`) continue to behave identically.

When `--mode metadata-history` is set:

1. The extractor pulls all playbook YAMLs from GCS exactly as today.
2. For each playbook with `metadata_history.enabled: true`:
   - Compute `playbook_hash`, `git_commit_hash`, `extractor_version`, `extracted_at` (same lineage stamps as the time-series flow).
   - For each `universe` row with `is_rolling_contract: true`:
     - `bds(generic_ticker, chain_field, **chain_overrides)` → list of underlying contracts (e.g. `["TYZ20 Comdty", "TYH21 Comdty", …]`).
     - For each underlying contract: `bdp(contract, [bloomberg_field, …])` for the union of `static_fields[*].bloomberg_field` plus `roll_convention.roll_field` plus `roll_convention.first_trade_field`.
     - Sort underlying contracts by `roll_field` ascending; pair consecutive ones into effective windows per `expiry_roll` semantics (see below). The latest contract's window is open-ended (`effective_to = NULL`).
3. Apply a coverage gate analogous to the existing 90% gate: refuse to upload if more than 10% of the playbook's rolling tickers returned an empty chain.
4. Apply a pre-write overlap-validation gate (see below) over the assembled rows — a redundant safety net before the parquet is uploaded.
5. Write a wide-format parquet to `gs://<bucket>/metadata_history/<dataset_name>/<dataset_name>_metadata_history_<timestamp>.parquet` with the columns enumerated under "Parquet shape" below.

### `roll_convention.type = "expiry_roll"` — definition

Given a list of underlying contracts `C_1, C_2, …, C_n` sorted ascending by `C_i.roll_field`:

- **First contract** (`C_1`): `effective_from = C_1.first_trade_field`, `effective_to = C_1.roll_field`.
- **Middle contracts** (`C_i`, `2 ≤ i ≤ n-1`): `effective_from = C_{i-1}.roll_field + 1 day`, `effective_to = C_i.roll_field`.
- **Last contract** (`C_n`): `effective_from = C_{n-1}.roll_field + 1 day`, `effective_to = NULL` (currently in effect).

This convention treats the roll as occurring on the day after the prior contract's `roll_field` (the day after last-tradeable, in the canonical default). The boundary semantics align with the `EXCLUDE USING GIST … daterange(effective_from, effective_to, '[]') WITH &&` constraint shipped in ADR 0001: adjacent windows share no day. The `close_open_metadata_window` helper from ADR 0001 sets the prior open window's `effective_to = new.effective_from − 1`, which matches.

If A2 verification reveals that a market's roll convention differs (e.g., bond futures rolling N days before expiry), the playbook either picks a different `roll_field` (e.g., `FUT_NOTICE_FIRST` instead of `LAST_TRADEABLE_DT`) or this ADR is amended to introduce a new `roll_convention.type` value.

### Pre-write overlap-validation gate

Codex's review of ADR 0001 called for explicit overlap detection at write time, in addition to the DB-level `EXCLUDE` constraint. PR A1 lands the gate in two places:

1. **Inside the extractor**, after assembling windows and before writing the parquet. If any instrument's assembled windows overlap or share a boundary day with another, the extractor aborts the run for that playbook, logs the offending rows, and does not upload a parquet. This catches source-data anomalies (Bloomberg returning duplicate chain entries, inconsistent `roll_field` values) before they reach the DB.
2. **Inside the ingester**, after reading the parquet and before any DB write. Same logic. The second run is defence-in-depth — a parquet that passed the extractor gate but was modified or replaced between extract and ingest is still validated. If this gate fails, the audit row is marked `FAILED` and the destructive transaction is never opened; the DB's `EXCLUDE` constraint would also catch the violation, but failing earlier produces a cleaner audit trail.

## Ingester extension — concrete

`ingestion/ingest_parquet.py` gains a second top-level blob loop. The current loop lists `gs://<bucket>/data/` and treats every parquet under it as time-series. After PR A1:

```text
for blob in list_blobs(prefix="data/"):
    process_timeseries_parquet(blob)            # EXISTING — unchanged path

for blob in list_blobs(prefix="metadata_history/"):
    process_metadata_history_parquet(blob)      # NEW — routes to instrument_metadata_history
```

The new `process_metadata_history_parquet(blob)` function:

1. Downloads the parquet to the temp dir.
2. Computes `source_file_hash` over canonicalised content (same `ingestion.hashing.compute_normalized_data_hash` used by the time-series path).
3. Dedup check against `load_audit` for the same `playbook_name` + `extraction_mode='metadata_history'`. If matched, insert a `SKIPPED_DUPLICATE` audit row and archive the blob, mirroring the time-series dedup behaviour exactly.
4. Insert a `RUNNING` audit row with `extraction_mode='metadata_history'`.
5. Run the pre-write overlap-validation gate (defence-in-depth — see above).
6. Resolve each row's `vendor_ticker` to an `instrument_id` via `instrument_master`. Refuse to write rows whose `vendor_ticker` is not present in `instrument_master` (this means the corresponding time-series playbook has not been ingested yet — operator error, not a substrate bug).
7. Inside a single `with engine.begin() as conn:` block:
   - Per instrument, call `close_open_metadata_window(conn, instrument_id, new_effective_from=earliest_row.effective_from)` to close any prior open row before reinserting. This handles the case of a re-run that supersedes prior state.
   - `upsert_instrument_metadata_history(conn, records)` writes the new rows. The `ON CONFLICT (instrument_id, effective_from)` semantics make this idempotent for re-runs of the same window set.
   - Flip the audit row to `SUCCESS`.
8. Archive the blob to `gs://<bucket>/archive/metadata_history/<dataset_name>/<file>.parquet`.

The destructive critical section (close + upsert + audit-flip) is the **same atomic-transaction shape** as the existing time-series path. The `EXCLUDE USING GIST` constraint on the history table catches any overlap the gate missed; on violation, the entire txn rolls back and the outer handler flips the audit row to `FAILED` on a separate txn.

### What about the existing 80% destructive coverage gate?

The existing time-series ingester's 80% gate compares incoming-instrument-count against prior-successful-load-instrument-count. This makes sense for daily-time-series ingestion where universes shrink only deliberately. For metadata-history ingestion, the equivalent question is "did this parquet shrink the count of rolling tickers covered?" — which would be a legitimate concern (a Bloomberg outage on `FUT_CHAIN` could shrink the set silently). PR A1 implements the gate **only for the rolling-ticker count**, not the row count, because the row count varies naturally (a long-running ticker accumulates more windows over time). Threshold matches the time-series default: 80%.

## Parquet shape

`gs://<bucket>/metadata_history/<dataset_name>/<dataset_name>_metadata_history_<timestamp>.parquet`

| Column | Type | Source | Notes |
|---|---|---|---|
| `vendor` | str | playbook default | `"BLOOMBERG"` today |
| `vendor_ticker` | str | universe row | the **generic** rolling ticker, e.g. `"TY1 Comdty"` |
| `effective_from` | date | extractor (computed) | window start, inclusive |
| `effective_to` | date | extractor (computed) | window end, inclusive; NULL for currently-effective |
| One column per `static_fields[*].column_name` | varies | per-underlying-contract `bdp()` | the per-window metadata that was true during this window |
| `attributes_json` | str (JSON-encoded) | extractor | any `static_fields` with unrecognised `column_name` are packaged here |
| `playbook_name`, `playbook_version`, `playbook_hash`, `git_commit_hash`, `extractor_version`, `requested_start_date`, `requested_end_date`, `extracted_at` | varies | lineage stamps | same as time-series path |
| `extraction_mode` | str | extractor | always `"metadata_history"` for this parquet |
| `dataset_name` | str | playbook | for ingester routing parity with the time-series shape |

The ingester reads the parquet, maps `column_name` columns to the table's typed columns via `upsert_instrument_metadata_history`'s existing record shape, and decodes `attributes_json` into the JSONB column.

## Alternatives considered

**Option A — separate one-off `utils/backfill_rolling_metadata.py` script.** Smaller PR (~700 lines vs ~1800), but creates a second extractor entry point, duplicates auth / lineage / coverage / audit machinery, and would have to be re-forked or generalised for the next rolling-contract universe (commodity futures, FX futures). P10-erosive; rejected.

**Option B (alternative shape) — new playbook entirely, e.g. `bond_futures_metadata_history.yml`.** Would isolate the metadata-history declaration cleanly, but creates two playbooks per rolling-contract universe (the time-series one and the metadata-history sibling), which doubles operational surface and risks the two drifting on universe membership. Rejected in favour of a single playbook with an optional section.

**Option B (alternative routing) — discriminator column in the time-series parquet shape instead of a separate GCS prefix.** Considered; would put time-series and metadata-history parquets in the same `data/` prefix with a `record_type` column. Rejected because: (a) the parquet shapes are genuinely different (time-series is long, metadata-history is wide with multi-column rows), so co-locating them makes the ingester branch on every parquet read; (b) per-prefix `list_blobs` is cheaper than scanning a discriminator column on every blob; (c) GCS path is a structural type-tag that survives parquet-content mutations.

**Option B (alternative timing) — make this a "live append on every ingestion" change instead of a backfill mode.** Considered and rejected for PR A1. The live-append path makes sense after Step 1.1 has populated the history table once and proven the read path. Wiring live append now means every existing ingestion of `bond_futures.yml` would start writing history rows immediately, which is a behaviour change to a currently-working path with no upside until backfill verifies the shape.

## Consequences

**Positive:**

- One canonical extractor entry point. Operationally: `python utils/historical_extractor.py --mode {time-series,metadata-history} --playbook X`.
- Every new rolling-contract universe (commodity futures, FX futures, eventual fixings) just adds `metadata_history:` to its playbook YAML — zero infrastructure work to onboard.
- The parquet → GCS → ingester pattern's idempotency, audit, dedup, and atomic-txn guarantees are reused, not re-implemented.
- The Step-0 helpers (`upsert_instrument_metadata_history`, `close_open_metadata_window`) get their first real consumer, completing the substrate-to-data wiring.

**Negative / known trade-offs:**

- `historical_extractor.py` grows from ~615 lines to ~1100+ lines. The new mode is in a separate function (`run_metadata_history_extraction`), so the time-series code path is unchanged, but the file is bigger.
- `ingestion/ingest_parquet.py` gains a parallel processing loop. Same atomicity contract; more code surface.
- The playbook contract gains an optional 9th top-level key (was 8). The contract README v1.2 documents this; no existing playbook is forced to declare the new key.
- The "live append on every ingestion" path is deferred — until it lands, every full re-extraction of a metadata-history playbook overwrites prior windows via `upsert ON CONFLICT`. That's intentionally safe because the EXCLUDE constraint prevents window corruption, but operationally the backfill is "all-or-nothing per playbook" until live-append lands.

## Rollout plan

1. **This PR (A1):**
   - Land this ADR + the contract README v1.2 amendment.
   - Land the extractor extension (`--mode metadata-history`) with the `expiry_roll` convention.
   - Land the ingester extension (`metadata_history/` blob loop) with the defence-in-depth overlap gate.
   - Land tests with fixture playbooks (NOT production playbooks) and mocked Bloomberg responses.
   - **Do NOT edit `bond_futures.yml` or `policy_futures.yml`.** Production playbooks are A2.
2. **PR A2 (next session):**
   - Manually verify the production mnemonics (`FUT_CHAIN` overrides, `LAST_TRADEABLE_DT`, `FUT_FIRST_TRADE_DT`, `FUT_DLV_DT_LAST`, `FUT_TICK_SIZE`, `FUT_CONT_SIZE`, etc.) per the existing operator workflow.
   - Add the verified `metadata_history` section to `bond_futures.yml` and `policy_futures.yml`.
   - Push playbooks to GCS.
   - Run `python utils/historical_extractor.py --mode metadata-history --playbook bond_futures` (and similarly for policy_futures).
   - Run the ingester.
   - Verify rows in `instrument_metadata_history` via the four sanity queries (count, point-in-time spot-check, EXCLUDE smoke test, audit row).
3. **Future:** live-append path inside `ingest_parquet.py` so every ingestion of a metadata-history-enabled playbook appends new windows on roll detection. Separate PR; depends on A2's backfill being clean.

## Verification

PR A1's acceptance criteria (no Bloomberg required):

- `pytest Macro_Copilot/tests/state/test_metadata_history_extraction.py` and `…/test_metadata_history_ingestion.py` pass.
- The extractor compiles and `python utils/historical_extractor.py --help` shows the new `--mode` flag.
- The ingester recognises and partitions blobs under the new prefix in unit tests with a mocked GCS client.
- The existing `--mode time-series` path is unchanged — verified by re-running the existing extraction smoke test.
- No production playbook is altered (`git diff` is empty for `rates_agent/playbooks/*.yml`).

## Version log

| Version | Date | Change |
|---|---|---|
| v1 | 2026-05-19 | Initial decision. Accepted. |
