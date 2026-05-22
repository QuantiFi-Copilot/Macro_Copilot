# ADR 0008 — Event-playbook contract: declarative economic-release & central-bank-meeting extraction

**Status:** Accepted
**Date:** 2026-05-21
**Builds on:** ADR 0004 (event-calendar substrate — the `event_calendar` table, `upsert_event_calendar`, and the *documented intent* for an event extractor + `events/` ingestion route), ADR 0002 (the precedent: a new extractor `--mode` for a non-`bdh`/`bdp` surface). The B2 Stage-A work already shipped the consuming half — `ingestion/event_calendar.py` + `_process_event_blob` + the PHASE 5 `events/` ingestion route.
**Operationalises principles:** P1 (built right — a real declarative contract, no placeholder), P2 (accuracy or refuse — only the mnemonics two operator verification rounds confirmed ship; auctions are deferred, not guessed), P3 (consistency — the event playbook reuses the established lineage-key + named-section shape), P4 (determinism — event extraction is incremental-only and replayable), P5 (honest disclosure — the v1 econ-history scope limit is recorded), P8 (closed-family — `event_category` stays the closed `EVENT_CATEGORIES` set; `asset_class` is not extended), P12 (Bloomberg Accuracy Boundary — WIRP is ingested as Bloomberg's own series; event ingestion leaves `surprise` NULL unless Bloomberg supplies it, and a primitive may later compute the exact identity `actual − consensus_median` with explicit disclosure — never a recomputed proxy shipped under a standard name).
**Scope:** Work order B2, Stage C — the event-playbook contract and the `--mode event-calendar` extractor design. Covers the **economic-release** and **central-bank-meeting** families. **WIRP** is carved out (time-series, not an event playbook — §5). **Auctions** are deferred (TD #28).

---

## Context

B2 delivers the macro event layer. ADR 0004 landed the `event_calendar` table and helpers; B2 Stage A added the ingestion half — a wide event parquet under `gs://<bucket>/events/` is parsed by `ingestion/event_calendar.py` and folded into `event_calendar` by `_process_event_blob` (PHASE 5), through `upsert_event_calendar`. What B2 still needs, and what this ADR fixes:

1. **A playbook contract that declares WHAT events to extract** — the existing playbook contract is time-series-shaped (`target_metrics` / `reference_metrics` / `universe`, an instrument universe pulled via `bdh`/`bdp`). An event is not an instrument and not a `(date, instrument, field, value)` series — it needs a different declaration.
2. **The extractor that reads it** and emits the wide event parquet Stage A already consumes.

Two operator Bloomberg verification rounds (`scripts/event_data_bloomberg_check.py`) empirically established the access patterns:

- **Economic releases** — `bdh(<indicator>, [PX_LAST] + survey fields)` for the actual + consensus series (indexed by reference *period*); `bds(<indicator>, ECO_RELEASE_DT_LIST)` for the release-date list. 11/11 indicators verified.
- **Central-bank meetings** — `bds(<rate ticker>, ECO_RELEASE_DT_LIST)` for the meeting calendar; `bdh(<rate ticker>, PX_LAST)` for the policy rate. 4/4 banks verified.
- **WIRP** — fixed-meeting virtual tickers `{REGION}0B{METRIC} {MMMYYYY} Index`, daily series. 32/32 probes verified, past-meeting history retained.
- **Auctions** — `bdp(<bond>, MOST_RECENT_*)`; three result fields unresolved → deferred (TD #28).

## Decision

### 1. The event playbook is a distinct playbook KIND

An event playbook is **not** a time-series playbook with an extra section (that was ADR 0002's dual-purpose model). It carries only the **lineage keys** — `playbook_name`, `playbook_version`, `dataset_name`, `description`, `extraction` — plus one new top-level section, **`event_calendar:`**. It deliberately does **not** carry `asset_class` / `target_metrics` / `reference_metrics` / `universe`: those are the instrument-time-series keys, and an event has no instrument identity (ADR 0004 — events are not in the `asset_class` closed family, and `asset_class` is not extended).

**The marker is the section.** A playbook that carries an `event_calendar:` section IS an event playbook. The time-series extractor (`--mode time-series`) skips any playbook with an `event_calendar:` section; the new `--mode event-calendar` processes only those. This mirrors how the `metadata_history:` section gates the metadata-history flow (ADR 0002).

```yaml
playbook_name: economic_releases
playbook_version: "1.0"
dataset_name: economic_releases
description: |
  Daily macro economic-release events ...
extraction:
  lookback_days: 800        # how far back to pull the bdh actual/survey window

event_calendar:
  family: economic_release          # economic_release | central_bank_meeting
  event_category: economic_release  # the event_calendar.event_category value
  ...
  events:
    - { event_type: ..., country: ..., ticker: ... }
```

### 2. The `economic_release` family

```yaml
event_calendar:
  family: economic_release
  event_category: economic_release
  actual_field: PX_LAST                       # bdh -> event_calendar.actual
  release_date_field: ECO_RELEASE_DT_LIST     # bds bulk field -> release_date
  survey_fields:                              # bdh field -> event_calendar column
    consensus_median:  BN_SURVEY_MEDIAN
    consensus_high:    BN_SURVEY_HIGH
    consensus_low:     BN_SURVEY_LOW
    surprise_std_dev:  FORECAST_STANDARD_DEVIATION
  events:
    - { event_type: cpi_yoy, country: US, currency: USD, ticker: "CPI YOY Index" }
    - { event_type: nfp,     country: US, currency: USD, ticker: "NFP TCH Index" }
    # ... 11 verified indicators
```

Per event the extractor: `bdh(ticker, [actual_field] + survey field values)` over the `lookback_days` window → the actual + consensus series indexed by reference **period** (month-end); `bds(ticker, ECO_RELEASE_DT_LIST)` → the release-date list.

It then joins release dates to observations by an **explicit, run-date-aware algorithm** — *not* a loose "newest pairs with newest", which would mispair the next scheduled release with the latest actual:

1. Split the bds release dates against the extraction **run date**: `realized = [d ≤ run_date]`, `scheduled = [d > run_date]`.
2. Map each realized release date `R` to its reference **period** = the bdh observation whose period date is the *latest date strictly before* `R` (a release publishes the most-recently-completed period). This is lag-agnostic — it works for the monthly and the weekly cadences alike.
3. Each period is claimed by at most one release (newest release wins); if a more-recent release already claimed a period, the older release's actual is not yet posted — that release is demoted to a scheduled placeholder.
4. A realized release older than the entire bdh window (no observation before it) is skipped with a warning. The extractor also warns loudly if realized release dates outnumber available bdh observations.

Each realized `(release_date, period)` pair → one `event_calendar` row: `release_date` (the bds date), `period` (the bdh index date), `actual`, `consensus_*`, `surprise_std_dev`. Each `scheduled` release date → a **placeholder row** (`actual` / `period` / consensus all NULL), filled on a later run by the full-row upsert — the announce→results lifecycle ADR 0004 defined for auctions. `prior` is the previous realized observation's `actual` (an exact identity). `surprise` is left **NULL** by event ingestion — it is the exact identity `actual − consensus_median`, computed downstream by the Phase-3 surprise primitives *with disclosure* (P12), never baked in here. `surprise_std_dev` carries the *forecast* standard deviation — the dispersion that standardises a surprise — its intended downstream use.

An individual event may set **`survey: false`** to opt out of consensus extraction — for an indicator Bloomberg exposes no survey for (e.g. Japan's composite PMI, confirmed survey-less by the verification run). Its consensus columns are then deliberately NULL, and the playbook *documents* the absence rather than the extractor silently emitting empty consensus.

Verified data characteristics carried forward: Japan composite PMI exposes no consensus (survey-less — actual-only); the PMI tickers' actual history is ~3 years.

### 3. The `central_bank_meeting` family

```yaml
event_calendar:
  family: central_bank_meeting
  event_category: central_bank_meeting
  meeting_calendar_field: ECO_RELEASE_DT_LIST  # bds on the rate ticker
  rate_field: PX_LAST                          # bdh on the rate ticker
  events:
    - { event_type: fomc_decision, country: US, currency: USD,
        central_bank: FOMC, rate_ticker: "FDTR Index" }
    # ECB (EUORDEPO Index), BoE (UKBRBASE Index), BoJ (BOJDTR Index)
```

Per bank: `bds(rate_ticker, ECO_RELEASE_DT_LIST)` → meeting dates; `bdh(rate_ticker, PX_LAST)` → the daily policy-rate series. Each meeting date → one `event_calendar` row: `release_date` = meeting date, `country`, `central_bank`, `currency`.

The decision rate uses an **explicit as-of rule** against the daily rate series — both lookups are "last observation strictly before a cutoff date", so a meeting on a non-trading day needs no special-casing:

- `actual` (the rate the meeting decided) = the last rate observation strictly *before the NEXT meeting* — i.e. the rate that held over the whole inter-meeting period this meeting opened. For the most recent meeting (no next meeting yet) this resolves to the latest observation = the current rate.
- `prior` = the last rate observation strictly *before this meeting's own date* — equivalently, the previous meeting's `actual`.

The hike/hold/cut classification (an exact identity from `actual − prior`) rides in `attributes`. A meeting in the future (`> run_date`) is a **scheduled placeholder** row — `actual` / `prior` NULL, filled post-meeting by the full-row upsert. `related_instrument_id` links to the WIRP synthetic instrument for that meeting — populated when D-wirp lands (§5); NULL until then, backfilled by the full-row upsert.

### 4. The extractor — `--mode event-calendar`, incremental-only

A `run_event_calendar_extraction()` function is added to **`utils/incremental_extractor.py` only**, dispatched by `--mode event-calendar`. It is **not** added to `historical_extractor.py`: the `ECO_RELEASE_DT_LIST` bds surface is itself a recent-plus-forward window (§6), so a deep historical backfill is not available to pull — event extraction is intrinsically a forward/incremental operation, exactly like the OTR resolver (ADR 0007). The extractor stays self-contained (inlined, narwhals-agnostic helpers — the established discipline).

It reads each event playbook's `event_calendar:` section, runs the family-appropriate extraction (§2 / §3), assembles a **wide parquet whose columns are `event_calendar`'s columns** + the lineage columns, and uploads it to `gs://<bucket>/events/<dataset>/`. From there the already-built Stage-A route (`_process_event_blob`, PHASE 5) folds it into `event_calendar` via `upsert_event_calendar`. The extractor produces exactly the schema `ingestion/event_calendar.parquet_to_event_records` parses — that contract is fixed by Stage A.

**Coverage gate.** Every configured event must yield rows. If any event fails extraction — a `bdh`/`bds` error, a missing ticker, or a zero-realized-row join — the playbook's extraction **fails and NO parquet is uploaded**: a partial event artifact must never ingest as a clean `SUCCESS` (the data-PR discipline, mirroring the time-series extractor's coverage gate).

**Malformed sections fail loudly.** `_resolve_event_calendar_section` returns `None` only when the playbook carries *no* `event_calendar:` key (a genuine non-event playbook — skipped). A section that IS present but malformed (unknown `family`, missing `event_category`, no `events`) raises — it is a config bug and must fail loudly, never be silently treated as a non-event playbook (P6).

### 5. WIRP is NOT an event playbook — time-series carve-out

Per ADR 0004 decision 3, WIRP is a *daily time series per meeting*, not a one-shot event — it belongs in `market_data_daily`, not `event_calendar`. The verification confirmed the mechanism: fixed-meeting virtual tickers `{REGION}0B{METRIC} {MMMYYYY} Index` (regions `US0B`/`EZ0B`/`GB0B`/`JP0B`; metrics `FR` post-meeting implied rate, `PR` hike/cut probability, `NM` number of 25bp moves, `CH` implied bp change), each a clean daily `bdh(PX_LAST)` series, with **past-meeting tickers retaining full history**.

Therefore **D-wirp is an ordinary `--mode time-series` playbook** — its `universe` is the WIRP tickers, ingested through the existing PHASE 2 → `market_data_daily` path. It is NOT an event playbook and carries no `event_calendar:` section. Its only non-standard aspect is that the universe is meeting-dated and grows as the central-bank calendar extends — the universe is generated from the meeting calendar (the same `bds ECO_RELEASE_DT_LIST` §3 reads), analogous to the OTR resolver's rendered universe (ADR 0007). The `event_calendar` central-bank-meeting row references the WIRP synthetic instrument via `related_instrument_id`. The full D-wirp build (universe generation, the synthetic-instrument modelling — one-instrument-per-meeting with FR/PR/NM/CH as fields vs. one-instrument-per-metric) is its own increment; this ADR fixes only that WIRP is time-series and out of the event-playbook contract.

### 6. D-econ v1 scope — the release-date window

`ECO_RELEASE_DT_LIST` returns a column literally named *"Future Ecostats Release Date Time"* — a **recent-past-plus-forward** window (~23–116 rows per indicator), not a full historical mapping. The bdh actual series goes back ~6 years; the release-date list does not. Since `event_calendar.release_date` must be the true release date, **B2 v1 ingests only the release-dated window** (~the last ~1.5 years plus forward scheduled releases). The deeper pre-window history (2020–2024) is a documented follow-up — it needs a release-date source the current field does not provide. This is the same forward-oriented, honestly-scoped choice as the OTR resolver (ADR 0007); event studies over the covered window are fully served.

### 7. Auctions deferred

The fourth B2 family — sovereign auctions — is deferred (technical-debt #28): two verification rounds left the tail, settled-auction-date, and bidder-percentage fields unresolved, and the consuming `auction_tail` primitive is Phase-3 and does not yet exist. The `event_calendar` auction-result columns stay empty; no schema change is needed when D-auctions is later picked up. This ADR's contract is auction-ready (an `event_category: auction` family slots into the same shape) but no auction family is specified here.

## Alternatives considered

- **Cram events into the existing playbook contract** (events as `instrument_type` values, releases as `market_data_daily` rows). Rejected by ADR 0004 already — it forces a non-instrument into the instrument identity space and a structured one-shot observation into the long time-series format.
- **An `event_calendar:` section on a dual-purpose playbook** (the ADR 0002 metadata-history pattern). Rejected: a metadata-history playbook has a genuine time-series half (the futures it also pulls); an event playbook has no instrument half at all. Making it a distinct kind (no `asset_class`/`universe`) is honest and keeps the time-series keys from being filled with placeholders.
- **One combined extractor mode handling auctions too, now.** Rejected — auctions are unverified (TD #28); building speculative auction handling violates P2.

## Consequences

**Positive.** B2's event extraction has a real declarative contract that mirrors the established playbook shape; the two verified families (economic releases, central-bank meetings) can be written as playbooks and extracted; the extractor produces exactly the parquet Stage A already ingests; WIRP reuses the existing time-series pipeline rather than spawning a parallel one; the contract is auction-ready for when TD #28 is closed.

**Negative / accepted.** D-econ v1 history is bounded by the `ECO_RELEASE_DT_LIST` window (§6, follow-up). The economic-release period↔release-date join is chronological-alignment rather than a vendor-provided key — robust because releases are strictly one-per-period and monotone, but it is an alignment, disclosed here. WIRP's synthetic-instrument modelling is left to the D-wirp increment. Event extraction is incremental-only — no deep historical event backfill (§4), consistent with the source surface.

## Rollout

1. **This ADR** — the contract.
2. **Stage C.2** — `run_event_calendar_extraction()` + `--mode event-calendar` on the incremental extractor; the time-series extractor learns to skip `event_calendar:` playbooks; tests.
3. **Stage D** — the `economic_releases` and `central_bank_meetings` event playbooks (VERIFIED mnemonics); the `wirp` time-series playbook; operator extract → ingest → verify, per family.
4. **Follow-ups** — D-auctions (TD #28); the D-econ deep-history backfill (§6).

## Version log

| Version | Date | Change |
|---|---|---|
| v1 | 2026-05-21 | Initial decision. Accepted. |
