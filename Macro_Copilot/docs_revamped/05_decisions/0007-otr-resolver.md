# ADR 0007 — On-the-run resolver: the resolver runs as an extractor capability, writes data not config

**Status:** Accepted
**Date:** 2026-05-21
**Builds on:** ADR 0003 (cash-bond substrate — `otr_history` SCD2 table, `sovereign_cash_bond` instrument_type, the `_normalize_otr_record` / `upsert_otr_history` / `close_open_otr_window` / `get_otr_at` helpers), ADR 0005 (the two-playbook split and the curated, OTR-seeded `sovereign_cash_bonds.yml` universe).
**Operationalises principles:** P1 (built right — the resolver is a declarative playbook capability, not a bolted-on script), P2 (accuracy or refuse — no Bloomberg mechanism recovers historical OTR windows, so they are not fabricated), P4 (determinism — resolution is scoped to the incremental extractor so historical backfills stay replayable; the playbook stays a versioned git artifact), P5 (honest disclosure — forward-only and detection-date limits recorded in `docs/technical_debt.md` #27), P6 (typed failure — failed/rejected slots produce no `otr_history` change), P8 (closed-family — no new `asset_class`/`instrument_type`), P12 (Bloomberg Accuracy Boundary — the resolver reads identifier fields, recomputes nothing).
**Scope:** Work order A4-4, the on-the-run resolver. This ADR fixes *how* the OTR mapping is kept current over time. It builds on the `sovereign_cash_bonds.yml` universe (ADR 0005) and the `otr_history` substrate (ADR 0003).

---

## Context

`sovereign_cash_bonds.yml` (ADR 0005) ships a curated universe of individual
cash bonds — the *current* on-the-run (OTR) benchmark per (country, tenor)
slot. But "the OTR bond" is not static: after each auction a freshly-issued
bond becomes on-the-run and the prior one becomes off-the-run. The universe
must therefore grow over time, and *which bond was on-the-run when* is a
time series — exactly what the `otr_history` SCD2 table (ADR 0003) exists to
hold. ADR 0003 landed that table empty; this ADR is the mechanism that fills
it.

Two hard constraints shape the design:

1. **Resolution needs a live Bloomberg terminal.** Finding the current OTR
   bond for a slot is `bdp(<GT-generic>, ID_ISIN)` — it runs on the Bloomberg
   PC. The A4-4 probe (`scripts/a4_ofr_resolver_probe.py`) confirmed `bdp` on
   each `GT.. Govt` generic resolves cleanly to its current OTR ISIN, and
   separately proved that `bdh` of a reference field does NOT historise the
   OTR chain — there is **no** Bloomberg mechanism to recover *past* OTR
   windows.

2. **The playbook and the database live on the local PC.** The git playbook
   is a versioned, operator-reviewed artifact; the database is local.

A naive design — have the Bloomberg-PC resolver append new bonds to the
playbook YAML — was rejected: it makes a batch job mutate a git-tracked config
artifact outside version control, splits the source of truth between git / GCS
/ local, destroys the audit trail, and breaks replayability (P4).

## Decision

### 1. Config flows down, data flows up. The resolver writes DATA, never the playbook.

The OTR mapping over time is *data* and its home is `otr_history`. The
playbook is *config* and stays a curated git artifact. The resolver emits a
**resolution artifact** (parquet) that rides the existing data up-channel
(Bloomberg PC → GCS → local ingestion → Postgres). Nothing mutates the
playbook.

### 2. Resolution is a declarative, opt-in capability of the INCREMENTAL extractor.

A playbook opts in with an `otr_resolution:` block (slot → generic-ticker map,
resolution field, reference fields, `confirmation_runs`). `utils/incremental_extractor.py`,
seeing the block, runs `resolve_otr()` as an extra step. It lives in the
**incremental extractor only** — never `historical_extractor.py`: `bdp` on a
generic returns *today's* OTR, so resolving during a historical backfill would
stamp today's mapping onto backfilled dates and corrupt `otr_history`.
Resolution is intrinsically a "what is true now" operation. One operator
command, two independent steps writing two independent artifacts.

### 3. The resolution artifact: dedicated GCS prefix, isolated audit scope.

`resolve_otr()` writes one row per slot to `gs://<bucket>/otr_resolution/<dataset>/`.
The ingester scans the `otr_resolution/` prefix and routes it to a new branch,
`_process_otr_resolution_blob`, whose destination is `otr_history`. The
artifact's `playbook_name` is **suffixed** (`<playbook>__otr_resolution`) so
the ingester's playbook-keyed dedup / `load_audit` scope is isolated from the
market-data load history of the same playbook.

### 4. false-roll protection: distinct-date two-run confirmation.

A single bad Bloomberg `bdp` print must never close an OTR window. A roll is
recorded only after `confirmation_runs` (default 2) resolver observations, on
**distinct dates**, agree on the same new ISIN. The unconfirmed candidate is
parked in the open `otr_history` row's `attributes` JSONB (`pending_candidate`)
— no schema change, no staging table. The pure state machine
(`ingestion/otr_resolution.plan_otr_transition`) is one of: BOOTSTRAP /
NO_CHANGE / RECORD_CANDIDATE / CONFIRM_ROLL / CLEAR_CANDIDATE / SKIP_STALE. A
confirmed roll backdates `effective_from` to the *first* confirmed sighting
(the most accurate honest date) and closes the prior window at
`first_observed - 1 day`, matching the `'[]'`-inclusive EXCLUDE GIST
constraint. The first observation of a slot with no history is a BOOTSTRAP
insert — it establishes the baseline, closes nothing, and is not gated; a bad
bootstrap self-heals once the genuine ISIN is confirmed. A second per-slot
sanity layer lives in the extractor (`expected_isin_prefix` match + maturity
plausibility) and marks a bad print `rejected` before it ever leaves the
Bloomberg PC.

### 5. Forward-only. No historical OTR backfill; detection-date effective dating.

`otr_history` is populated from the first resolver run onward. Pre-resolver
OTR windows are NOT reconstructed (no Bloomberg mechanism exists; the manual
1st-off-the-run seed was deliberately skipped rather than guessed). A roll's
`effective_from` is the first *confirmed* observation date, not the true
auction date. Both limits are recorded in `docs/technical_debt.md` #27.

### 6. instrument_master + otr_history commit atomically.

`upsert_instrument_master` was migrated to the `Connectable` contract (it was
the one mutating helper still opening its own transaction) so the
resolution-side instrument upsert and the `otr_history` SCD2 write commit in
one transaction. The resolved bond's `instrument_master` row uses the
canonical `/isin/<ISIN>` `vendor_ticker` — identical to the market-data
universe convention — so resolution-side and market-data-side upserts converge
on one `instrument_id` per bond.

### 7. The universe is rendered, not hand-edited.

`utils/render_effective_universe.py` (run locally) expands the curated seed
into the **effective universe** = seed bonds ∪ every non-matured bond ever in
`otr_history`, and uploads that rendered artifact to `gs://<bucket>/playbooks/`.
`push_playbooks.py` skips resolver-enabled playbooks so the static seed never
clobbers the rendered effective universe. The rendered file carries a
provenance header (seed hash, rendered-body hash, `otr_history` snapshot,
render time) for replayability. The git seed is never modified.

## Consequences

- **Positive.** The OTR universe self-extends with no playbook edits and no
  machine writing to git. Every OTR roll from the first run forward is captured
  in an SCD2 table with full `load_audit` lineage. A transient bad `bdp` print
  cannot corrupt `otr_history`. Historical backfills stay replayable. The
  `otr_resolution` block is a reusable pattern for any future playbook that
  needs a stable generic resolved to a rolling underlying.
- **Negative / accepted.** No pre-resolver OTR history (TD #27a). Roll dates
  carry detection-date precision (TD #27b). A newly-detected OTR bond enters
  the market-data universe one run later (the render step runs before the next
  extraction) — harmless, because a freshly-auctioned OTR bond is a new issue
  with near-zero history that the incremental window covers anyway.
- **Operator workflow.** Per cycle: (1) locally, render the effective playbook
  and push config; (2) on the Bloomberg PC, run the incremental extractor —
  market-data parquet + otr-resolution parquet; (3) locally, run the ingester
  — `market_data_daily` + `otr_history`/`instrument_master`.
