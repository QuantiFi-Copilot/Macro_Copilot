# ADR 0004 — Event-calendar substrate: a dedicated `event_calendar` table

**Status:** Accepted
**Date:** 2026-05-20
**Closes:** none directly. Resolves the open ADR flagged in `tmp/primitive_expansion/phase3.md` ("Event substrate design — playbook attributes vs separate event_calendar table"). Substrate for the data-first roadmap's Track B.
**Operationalises principles:** P1 (built right — full substrate, no placeholder), P3 (consistency by contract — the helpers mirror the established `_normalize_* / upsert_* / get_*` shape and the `_txn`-Connectable contract; `event_category` is a free `VARCHAR` to match `asset_class` / `instrument_type`), P8 (closed-family discipline — `event_category` is a small documented set; `asset_class` is **not** extended; WIRP is **not** made a new asset class), P10 (single source of truth — `database/schema.sql` owns `macro_data`), P12 (Bloomberg Accuracy Boundary — WIRP is INGEST-not-recompute, which shapes the WIRP carve-out below).
**Scope:** Step 3 of the data-first roadmap, Track B — **infrastructure only.** This ADR is PR B1. It does NOT write an event playbook, build an event extractor, route event ingestion, touch Bloomberg, or ingest any data. The corresponding data PR (B2 — event extraction + ingestion) is explicitly out of scope and follows separately.

---

## Context

The data-first roadmap's Track B delivers the macro calendar: economic releases (CPI, NFP, retail sales, PMI, jobless claims for US/EU/UK/JP), the central-bank meeting calendar (FOMC, ECB, BoE, BoJ), Bloomberg WIRP per-meeting implied-rate pricing, and the sovereign auction calendar + results. `phase3.md` carries an explicit open ADR for this — *"Event substrate design — playbook attributes vs separate `event_calendar` table"* — to be decided **before** the D-econ / D-cb / D-auctions ingest begins. B1 is that decision plus the substrate it lands.

The shape mismatch is the crux. `macro_data.market_data_daily` is a long-format hypertable keyed by `(trade_date, instrument_id, field_name)` with a single `field_value`. An **event** does not fit that shape: a CPI release carries `actual`, `consensus_median`, a consensus range, `prior`, `revised_prior`, `surprise`, `surprise_std_dev`, a `release_date`, a `release_time`, and a reference `period` — a wide, structured row, observed once, not a daily time series. An auction carries a high yield, a bid-to-cover ratio, a tail, an indirect-bidder percentage. None of this is `(date, instrument, field, value)`.

Events are also not **instruments** — they are not tradable, they have no vendor ticker, they are not in `instrument_master`'s `(vendor, vendor_ticker)` identity space. So neither of the two existing `macro_data` storage tables is the right home.

## Decision

**Stand up a dedicated `macro_data.event_calendar` table** — one row per event, wide and structured, with an optional foreign key back to `instrument_master` for country / curve linkage. Land it with DB helpers (`upsert_event_calendar`, `get_events_in_window`, plus the pure-Python `_normalize_event_record`) following the established `_txn`-Connectable contract and the shape of the `instrument_metadata_history` / `otr_history` helper families.

Four design forks were resolved with the operator before any schema was written:

1. **Auction result fields are typed columns.** `high_yield`, `bid_to_cover`, `tail_bps`, `indirect_pct` are nullable `NUMERIC` columns on `event_calendar`, not `attributes` JSONB. The Phase-3 `auction_tail` primitive reads `tail_bps` + `indirect_pct` as its core series; promoting them gives that read a typed path.

2. **`event_category` is a free `VARCHAR(32)`, not a `CHECK`-constrained enum.** The valid set — `economic_release`, `central_bank_meeting`, `auction` — is the closed family `EVENT_CATEGORIES` in `database/database.py`. The DB *column* is a free `VARCHAR` for schema-consistency with `instrument_master.asset_class` / `instrument_type` (no DB `CHECK`); the closed set is **enforced at write time by the sanctioned helper** — `_normalize_event_record` raises `ValueError` on a category outside `EVENT_CATEGORIES`. This keeps the column free while making the family genuinely closed (P8): a write outside the set fails loudly (P6 — a contract-layer violation raises a typed exception, never a silent write), and extending the set is a deliberate ADR + code change to `EVENT_CATEGORIES`. A DB `CHECK` would instead be a new pattern in `macro_data` and would turn every category addition into a DDL constraint swap.

3. **WIRP does not go in `event_calendar`.** Bloomberg WIRP per-meeting implied-rate pricing is a *daily time series per meeting* — the implied rate for the March-2026 FOMC changes every trading day. That is exactly the `(trade_date, instrument_id, field_name, field_value)` shape `market_data_daily` already serves. WIRP will be stored as `market_data_daily` rows against a **synthetic per-meeting instrument** in `instrument_master` (one row per CB meeting); the `event_calendar` central-bank-meeting row links to that instrument via `related_instrument_id`. B1 only **decides and documents** this — B2 builds it.

4. **An auction is one row, updated post-auction.** A scheduled auction is inserted with its schedule fields; after it happens, `upsert_event_calendar`'s `ON CONFLICT DO UPDATE` fills the result columns in place — exactly how an economic release's `actual` is `NULL` before the print and filled after. An auction is one event; the result is its outcome becoming known. No "scheduled vs results" discriminator, no double-counting. **`upsert_event_calendar` is a full-row upsert** — on a natural-key conflict it overwrites *every* non-key column from the incoming record, and `_normalize_event_record` defaults omitted optional columns to `None`. The post-auction upsert therefore re-sends the *complete* event row (the schedule fields **plus** the now-known results), not a partial delta: a partial upsert would write `NULL` over the omitted schedule fields. This is the natural extraction flow (B2 re-pulls the whole calendar each run; a vendor's post-settlement auction record carries both schedule and results) and matches the sibling full-row upserts `upsert_instrument_metadata_history` / `upsert_otr_history`. A `COALESCE(excluded, existing)` merge was rejected — it would diverge from those siblings (P3) and would make it impossible to correct a wrongly-set field back to `NULL`. **B2 contract:** every `upsert_event_calendar` call passes the complete current state of the event.

### What this ADR explicitly does NOT do

- **Does not write an event playbook**, build an event extractor, or add an event ingestion route. The Bloomberg ECO-calendar surface is genuinely different from `bdh` / `bdp` and deserves its own design step — B2, exactly as the metadata-history extractor was its own PR (ADR 0002) after Step 0. The intended B2 approach is *documented* below so B2 is not blocked.
- **Does not ingest any event data.** `event_calendar` is landed empty.
- **Does not build the WIRP path.** B1 records the decision (WIRP → `market_data_daily` + synthetic instrument); B2 implements it.
- **Does not extend the `asset_class` closed family (P8).** Events are not instruments and have no `asset_class`; the WIRP synthetic instrument stays within existing `asset_class` semantics (`rates`). No closed-family extension is proposed.
- **Does not modify `market_data_daily`, `instrument_master`, `instrument_metadata_history`, `otr_history`, or `v_market_data_daily_enriched`.** `event_calendar` is a new, standalone table; the enriched view is unchanged (events are a separate query surface, not a market-data overlay).
- **Does not touch Alembic.** `macro_data` is owned by `database/schema.sql`.

## Alternatives considered

**Option A (chosen) — a dedicated `macro_data.event_calendar` table.** A purpose-built wide table whose columns are the event's structure. Pro: the `actual / consensus / prior / surprise` shape is first-class and typed; idempotent re-ingestion via a natural-key `ON CONFLICT`; an optional FK links an event to an instrument without forcing events into the instrument identity space. Con: a new table and a new helper family — but the helpers are a near-exact structural copy of the `otr_history` family, so the marginal cost is low.

**Option B — extend the playbook contract: cram events into `market_data_daily` + new `instrument_type` values (`economic_release`, `policy_meeting`, `auction`) on `instrument_master`.** Rejected. It forces a fundamentally non-instrument thing (an event) into the instrument identity space, and a fundamentally non-time-series thing (a one-shot structured observation) into the long `(date, instrument, field, value)` format. A CPI release's `actual` / `consensus_median` / `consensus_high` / `consensus_low` / `prior` / `revised_prior` / `surprise` / `surprise_std_dev` would become eight separate `market_data_daily` rows that the consumer must re-assemble, and `release_time` / `period` have nowhere natural to live. Every event-study primitive would then carry assembly logic that the table shape should have provided. This is the option `phase3.md` flagged as the weaker one, and it is.

**Option C — events in `attributes` JSONB on a single placeholder instrument.** Rejected: it abandons typed columns entirely, defeats indexing on `event_type` / `release_date`, and makes `surprise` uncomputable in SQL.

The chosen design also folds in the four sub-decisions above; the alternatives for each (`CHECK`-constrained `event_category`; auction fields in JSONB; a dedicated `wirp_pricing` table; two rows per auction) were weighed and are recorded inline in the Decision section.

## Design — schema (`database/schema.sql`)

New section 7. One table, no constraints beyond a natural-key `UNIQUE`, two FKs, five access-pattern indexes.

```sql
CREATE TABLE IF NOT EXISTS macro_data.event_calendar (
    event_id BIGSERIAL PRIMARY KEY,
    event_type     VARCHAR(64)  NOT NULL,   -- cpi_yoy, nfp, fomc_decision, ust_auction_10y
    event_category VARCHAR(32)  NOT NULL,   -- economic_release | central_bank_meeting | auction
    country        VARCHAR(16)  NOT NULL,
    currency       VARCHAR(16),
    central_bank   VARCHAR(32),             -- FOMC / ECB / BOE / BOJ — for central_bank_meeting rows
    release_date   DATE         NOT NULL,
    release_time   TIME,                    -- nullable — not always known
    period         VARCHAR(32),             -- reference period: "Mar 2026", "Q1 2026"
    -- Economic-release numeric fields (the cross-category surprise model):
    actual           NUMERIC(20, 8),
    consensus_median NUMERIC(20, 8),
    consensus_high   NUMERIC(20, 8),
    consensus_low    NUMERIC(20, 8),
    prior            NUMERIC(20, 8),
    revised_prior    NUMERIC(20, 8),
    surprise         NUMERIC(20, 8),
    surprise_std_dev NUMERIC(20, 8),
    -- Auction result fields (typed per the B1 decision):
    high_yield   NUMERIC(20, 8),
    bid_to_cover NUMERIC(20, 8),
    tail_bps     NUMERIC(20, 8),
    indirect_pct NUMERIC(20, 8),
    -- Optional linkage to an instrument (e.g. an auctioned CUSIP, or the
    -- synthetic per-meeting WIRP instrument for a central_bank_meeting row).
    related_instrument_id BIGINT REFERENCES macro_data.instrument_master(instrument_id),
    -- Escape hatch: central-bank decision (hike/hold/cut), auction sized_amount,
    -- statement classification, and any event-type-specific structured field.
    attributes JSONB,
    load_id BIGINT REFERENCES macro_data.load_audit(load_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_event_calendar_natural_key
        UNIQUE (event_type, country, release_date)
);
```

### The natural key — why `(event_type, country, release_date)` and not `period`

The natural key exists so re-ingesting the calendar is idempotent (`ON CONFLICT DO UPDATE`). It must be **stable across the announcement → results lifecycle** (decision 4 — one row per auction). `period` is deliberately **excluded**: it can be `NULL` at announcement and filled later, and a key column that changes between ingests would make the post-auction upsert insert a duplicate instead of updating. `(event_type, country, release_date)` is stable and known at first ingest, and is unique for the roadmap's coverage — one CPI/NFP/PMI/claims print per series per date; one CB decision per bank per date; one auction per tenor per date (distinct tenors carry distinct `event_type` values, e.g. `ust_auction_3y` vs `ust_auction_10y`). If a same-day same-`event_type` collision ever arises (a new-issue + reopening of the identical tenor on one day — essentially never), the `event_type` string encodes the distinction. `country` is `NOT NULL` because every event in scope is country/region-specific.

Indices:
- `idx_event_calendar_type_date` on `(event_type, release_date)` — "every CPI release over time".
- `idx_event_calendar_country_date` on `(country, release_date)` — "every US event in a window".
- `idx_event_calendar_category_date` on `(event_category, release_date)` — "every auction in a window".
- `idx_event_calendar_related_instrument` on `(related_instrument_id)` — the FK reverse lookup ("events linked to this instrument"); Postgres does not auto-index FK columns.
- `idx_event_calendar_attributes` GIN on `(attributes)` — same shape as the sibling tables.

The natural-key `UNIQUE` additionally backs `event_type`-prefixed lookups.

`event_calendar` is **not** an SCD2 table — events are point observations, not effective-dated windows — so it carries no `EXCLUDE` constraint and needs no `btree_gist`.

## Design — DB helpers (`database/database.py`)

All additive; all follow the `_txn`-Connectable contract.

- **`EVENT_CATEGORIES`** — a module-level tuple `("economic_release", "central_bank_meeting", "auction")` — the single code-level source for the valid `event_category` set (the closed family, per P8/P10).
- **`_normalize_event_record(record)`** — pure-Python; validates `event_type`, `event_category`, `country`, `release_date` are present, **raises `ValueError` if `event_category` is outside `EVENT_CATEGORIES`** (the closed family is enforced here, at the write path), casts the FK columns to `int`, and returns a uniform-key dict (every `event_calendar` value column present, `None` default) so SQLAlchemy bulk insert generates one coherent column list. Mirrors `_normalize_otr_record`.
- **`upsert_event_calendar(connectable, records)`** — `INSERT … ON CONFLICT (event_type, country, release_date) DO UPDATE`; idempotent re-runs and the post-auction result fill. Mirrors `upsert_otr_history`.
- **`get_events_in_window(engine, event_type, start_date, end_date)`** — returns the events of one `event_type` whose `release_date` falls in `[start_date, end_date]`, ordered by `release_date`. Returns a (possibly empty) list.

## Design — the B2 extraction / ingestion plan (documented, not built)

So B2 is not blocked, the intended approach:

- **Extraction.** Bloomberg's economic-calendar surface (the `ECO`/`CDR`-style calendar API) is not `bdh`/`bdp`-shaped — it returns calendar rows, not security time series. B2 adds an event extractor (a new mode on the existing extractor, or a sibling extractor following the same self-contained pattern as the metadata-history extractor) that pulls calendar rows and writes a wide parquet whose columns are `event_calendar`'s columns.
- **GCS prefix.** Event parquets land under a new prefix `gs://<bucket>/events/<dataset>/`, a sibling to `data/` and `metadata_history/`.
- **Ingestion.** `ingest_parquet.py` gains a third blob loop for the `events/` prefix that resolves each parquet into records and calls `upsert_event_calendar` inside the same atomic-txn / audit-row / dedup-hash machinery as the existing paths.
- **WIRP.** Per decision 3, WIRP is *not* an event-calendar concern: B2 creates one synthetic `instrument_master` row per CB meeting and ingests the daily implied rate + hike/hold/cut probabilities as ordinary `market_data_daily` rows (distinct `field_name` per series). The `event_calendar` central-bank-meeting row references the synthetic instrument via `related_instrument_id`.

None of this is built in B1.

## Consequences

**Positive:**
- B2 becomes a focused task — an event extractor + an `events/` ingestion route + the playbook/data — on a substrate that already exists.
- The four event-derived primitives (`cpi_surprise`, `nfp_surprise`, `fomc_surprise_label`, `auction_tail`) and the four event-study templates (CPI / NFP / FOMC / auction) get a typed, indexed surface to read.
- `event_calendar` is the fourth `macro_data` table to follow the `_normalize_* / upsert_* / get_*` helper shape — the pattern is now firmly established.
- The WIRP carve-out keeps a daily time series in the table built for daily time series; no parallel time-series store is created.

**Negative / known trade-offs:**
- A fourth table in `macro_data`. Mitigated: its helper family is a structural copy of the `otr_history` family.
- The auction result columns (`high_yield`, `bid_to_cover`, `tail_bps`, `indirect_pct`) are `NULL` for every non-auction row, and the eight economic-release numeric columns are `NULL` for every auction / CB-meeting row. This is the normal shape of a multi-category table with typed per-category fields; the columns are nullable and cheap, and the operator chose typed columns for the auction-result fields deliberately so `auction_tail` reads a typed path.
- `event_category` has no DB-level `CHECK`. Mitigated: the valid set is the `EVENT_CATEGORIES` constant and `_normalize_event_record` raises on a value outside it — the family is genuinely closed at the sanctioned write path, while the column type stays consistent with `asset_class` / `instrument_type`.

## Rollout plan

1. **This PR (B1):**
   - Land this ADR.
   - Land the `schema.sql` section 7 (`event_calendar` + indexes) for fresh setups.
   - Land the matching idempotent migration SQL for existing databases (below).
   - Land the DB helpers (`_normalize_event_record`, `upsert_event_calendar`, `get_events_in_window`, `EVENT_CATEGORIES`).
   - Land `MagicMock`-driven tests.
2. **PR B2 (next):** the event extractor + the `events/` ingestion route + the event playbook/data; the WIRP synthetic-instrument path; ingest D-econ / D-cb-meetings / D-cb-wirp / D-auctions with first-load coverage verification.
3. **Future:** the four event-derived primitives and the four event-study templates (Phase 3, Step 2).

### Migration SQL — run on an existing database

Idempotent and data-safe (`CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`; no `DELETE`, no `UPDATE`, no destructive rewrite; no extension needed — `event_calendar` carries no `EXCLUDE` constraint). Apply by exec-ing into Postgres (`docker exec -it <pg> psql -U <user> -d <db>`).

```sql
BEGIN;

CREATE TABLE IF NOT EXISTS macro_data.event_calendar (
    event_id BIGSERIAL PRIMARY KEY,
    event_type     VARCHAR(64)  NOT NULL,
    event_category VARCHAR(32)  NOT NULL,
    country        VARCHAR(16)  NOT NULL,
    currency       VARCHAR(16),
    central_bank   VARCHAR(32),
    release_date   DATE         NOT NULL,
    release_time   TIME,
    period         VARCHAR(32),
    actual           NUMERIC(20, 8),
    consensus_median NUMERIC(20, 8),
    consensus_high   NUMERIC(20, 8),
    consensus_low    NUMERIC(20, 8),
    prior            NUMERIC(20, 8),
    revised_prior    NUMERIC(20, 8),
    surprise         NUMERIC(20, 8),
    surprise_std_dev NUMERIC(20, 8),
    high_yield   NUMERIC(20, 8),
    bid_to_cover NUMERIC(20, 8),
    tail_bps     NUMERIC(20, 8),
    indirect_pct NUMERIC(20, 8),
    related_instrument_id BIGINT REFERENCES macro_data.instrument_master(instrument_id),
    attributes JSONB,
    load_id BIGINT REFERENCES macro_data.load_audit(load_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_event_calendar_natural_key
        UNIQUE (event_type, country, release_date)
);

CREATE INDEX IF NOT EXISTS idx_event_calendar_type_date
    ON macro_data.event_calendar (event_type, release_date);
CREATE INDEX IF NOT EXISTS idx_event_calendar_country_date
    ON macro_data.event_calendar (country, release_date);
CREATE INDEX IF NOT EXISTS idx_event_calendar_category_date
    ON macro_data.event_calendar (event_category, release_date);
CREATE INDEX IF NOT EXISTS idx_event_calendar_related_instrument
    ON macro_data.event_calendar (related_instrument_id);
CREATE INDEX IF NOT EXISTS idx_event_calendar_attributes
    ON macro_data.event_calendar USING GIN (attributes);

COMMIT;
```

**Post-migration verification:**

```sql
-- Table exists and is empty:
SELECT count(*) FROM macro_data.event_calendar;                -- expect 0

-- Natural-key uniqueness is live (this INSERT pair MUST error on the 2nd row):
BEGIN;
INSERT INTO macro_data.event_calendar (event_type, event_category, country, release_date)
VALUES ('cpi_yoy', 'economic_release', 'US', '2026-03-12');
INSERT INTO macro_data.event_calendar (event_type, event_category, country, release_date)
VALUES ('cpi_yoy', 'economic_release', 'US', '2026-03-12');
-- expect ERROR: duplicate key value violates unique constraint
ROLLBACK;

-- Existing tables untouched:
SELECT count(*) FROM macro_data.instrument_master;             -- unchanged vs pre-migration
SELECT count(*) FROM macro_data.market_data_daily;             -- unchanged vs pre-migration
```

**Rollback** (safe only before any data has been written to `event_calendar`):

```sql
BEGIN;
DROP TABLE IF EXISTS macro_data.event_calendar;
COMMIT;
```

## Verification

B1's acceptance criteria (no Bloomberg required):

- `database/schema.sql` parses and reflects the design above; the migration block is idempotent (running it twice is a no-op) and data-safe.
- `pytest Macro_Copilot/tests/state/test_event_calendar_substrate.py` passes — `_normalize_event_record` validates the required fields and shapes a uniform-key dict; `upsert_event_calendar` composes under a caller-owned `Connection` without opening a sub-transaction and targets the `(event_type, country, release_date)` natural key; `get_events_in_window` issues a single windowed query.
- The existing test suite is unchanged — B1 is purely additive (`pytest tests/state/` green).
- No production playbook is altered; no Alembic file is touched.
- The operator's post-migration verification queries return the expected results, and the natural-key duplicate-insert smoke test errors as designed.

## Version log

| Version | Date | Change |
|---|---|---|
| v1 | 2026-05-20 | Initial decision. Accepted. |
