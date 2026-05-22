# ADR 0009 — WIRP: per-meeting implied-rate time series — synthetic-instrument modelling, generated universe, cross-pipeline link

**Status:** Proposed
**Date:** 2026-05-22
**Builds on:** ADR 0008 (event-playbook contract — §5 carved WIRP *out* of the event-playbook contract and named it a time-series carve-out, explicitly deferring "the full D-wirp build — universe generation, the synthetic-instrument modelling — one-instrument-per-meeting with FR/PR/NM/CH as fields vs. one-instrument-per-metric" to "its own increment"; this ADR is that increment), ADR 0004 (event-calendar substrate — the `event_calendar` table and the `related_instrument_id` column reserved for "the per-meeting WIRP implied-rate instrument"), ADR 0007 (the OTR resolver — the *rendered-universe* precedent: a DB-derived universe expanded locally, uploaded to `gs://<bucket>/playbooks/`, with `push_playbooks.py` skipping the seed so the static file never clobbers the rendered one).
**Operationalises principles:** P1 (built right — WIRP is modelled as the entity it is, a meeting, not bent to fit a one-row-per-ticker shape), P2 (accuracy or refuse — the ticker grammar ships only because two operator verification rounds confirmed it; the *full-calendar coverage* is not yet verified, so Stage B probes it before the playbook ships — far-horizon meetings are bounded, not guessed), P3 (consistency — D-wirp is an ordinary `--mode time-series` playbook ingested through the unchanged PHASE 2 path; the one contract amendment, `related_instrument_id` preservation, is justified below), P4 (determinism — the rendered universe carries a provenance header; extraction is replayable), P5 (honest disclosure — v1's incremental-only scope and the far-horizon coverage bound are recorded), P8 (closed-family — `instrument_type: wirp_meeting` is a new instrument_type, declared the ordinary per-playbook way; no `asset_class` or `event_category` change), P12 (Bloomberg Accuracy Boundary — WIRP is ingested verbatim as Bloomberg's own series; nothing is recomputed).
**Scope:** Work order B2, the **D-wirp** increment. Fixes how the WIRP per-meeting implied-rate series is modelled, how its universe is generated from the central-bank-meeting calendar, how it is extracted and ingested, and how the `event_calendar` central-bank-meeting rows are linked to their WIRP instrument. D-econ and D-cb have already shipped (ADR 0008, ingested 2026-05-21). D-auctions stays deferred (TD #28).

---

## Context

WIRP — Bloomberg's *World Interest Rate Probability* — prices, per scheduled
central-bank meeting, the rate path the market implies for that meeting. ADR
0004 reserved `event_calendar.related_instrument_id` for it; ADR 0008 §5
established three things and deferred the rest to this increment:

1. **WIRP is a daily time series, not an event.** It belongs in
   `market_data_daily`, not `event_calendar`. So D-wirp is an ordinary
   `--mode time-series` playbook — no `event_calendar:` section — and ingests
   through the existing PHASE 2 → `market_data_daily` route unchanged.
2. **The ticker grammar is verified.** Fixed-meeting virtual tickers
   `{REGION}0B{METRIC} {MMMYYYY} Index` — region prefixes `US0B` (FOMC),
   `EZ0B` (ECB), `GB0B` (BOE), `JP0B` (BOJ); metrics `FR` / `PR` / `NM` / `CH`;
   a clean daily `bdh(PX_LAST)` series each; past-meeting tickers retain their
   full history. The B2 verification probe (`scripts/event_data_bloomberg_check.py`
   Section 3, run 2026-05-21) confirmed 32/32 (ticker, metric) probes — but it
   probed only one *next* and one *~6-month-past* meeting per bank. **Coverage
   across the whole meeting calendar is not yet verified.**
3. **The universe is meeting-dated and grows** as the central-bank calendar
   extends — so it is *generated*, not hand-listed, "analogous to the OTR
   resolver's rendered universe."

What ADR 0008 §5 left open, and what this ADR fixes:

- **The synthetic-instrument model** — `one-instrument-per-meeting with
  FR/PR/NM/CH as fields` *vs.* `one-instrument-per-metric`. The time-series
  extractor's loop does `bdh(<one ticker>, [fields])` per universe row, but a
  WIRP meeting is *four* tickers (one per metric). The two models are not
  interchangeable: they produce different `instrument_master` rows, a different
  `market_data_daily.field` axis, and a different shape of
  `related_instrument_id` link.
- **Universe generation** — what reads the meeting calendar, what it emits, and
  how the rendered file reaches the extractor.
- **The `related_instrument_id` link** — `upsert_event_calendar` is, by its
  documented contract (ADR 0008), a **full-row upsert**: on a natural-key
  conflict every non-key column, `related_instrument_id` included, is
  overwritten from the incoming record. The D-cb event extractor always emits
  `related_instrument_id = NULL` (it runs on the Bloomberg PC and cannot know a
  DB-assigned `instrument_id`). So *any* later D-cb re-run wipes a backfilled
  link unless this is addressed.

## Decision

### 1. Synthetic-instrument model — one instrument per meeting; FR/PR/NM/CH are `field` values

A WIRP **instrument is one central-bank meeting.** Its four metrics —
`FR` / `PR` / `NM` / `CH` — are four `field` values in `market_data_daily`, each
a daily series under the same `instrument_id`. `one-instrument-per-metric`
(four instruments per meeting) is rejected.

The metric → `market_data_daily.field` mapping:

| WIRP code | `field`              | Meaning                                                |
|-----------|----------------------|--------------------------------------------------------|
| `FR`      | `WIRP_IMPLIED_RATE`  | post-meeting implied effective policy rate             |
| `PR`      | `WIRP_MOVE_PROB`     | probability of a single 25bp hike (+) / cut (−)        |
| `NM`      | `WIRP_NUM_MOVES`     | number of 25bp moves priced                            |
| `CH`      | `WIRP_RATE_CHANGE`   | implied change from the current effective rate         |

**Units are ingested verbatim (P12) — the `field` name asserts a *quantity*,
never a unit.** `WIRP_IMPLIED_RATE` is a rate, `WIRP_MOVE_PROB` a probability,
`WIRP_NUM_MOVES` a count, `WIRP_RATE_CHANGE` a change in the rate. `CH` was
named `WIRP_BP_CHANGE` in the v1 draft — that baked in a "basis points" unit
the raw Bloomberg `PX_LAST` value is **not confirmed** to use (the verified `CH`
prints look like small decimals, plausibly rate units, not bp). The field is
renamed to the unit-neutral `WIRP_RATE_CHANGE`; **no conversion is applied** —
WIRP is stored exactly as Bloomberg returns it. The Stage-B coverage probe (§6)
reports the observed value range of all four metrics so their units are
documented from verified data, not from a metric description — and the shipped
`wirp.yml` records the confirmed units in a comment.

Rationale:

- **It is the honest model (P1, P3).** A meeting is one entity; WIRP gives four
  *metrics about it*. `market_data_daily`'s `(instrument_id, field, value)`
  shape exists precisely to carry several metrics per instrument.
  `one-instrument-per-metric` would force `field = 'PX_LAST'` — a placeholder —
  and push the real metric into the ticker string and `instrument_type`.
- **It makes `related_instrument_id` a true 1:1 link.** ADR 0004 / ADR 0008 §3
  both name it, singular, "the per-meeting WIRP implied-rate instrument." With
  one instrument per meeting the `event_calendar` central-bank-meeting row's
  `related_instrument_id` is an unambiguous FK. `one-instrument-per-metric`
  leaves four candidate rows per meeting and no non-arbitrary FK target.
- **It serves the consumer.** The Phase-3 `fomc_surprise` primitive and the
  FOMC event-study template want, for a meeting, "the implied-rate path and how
  it moved" — one `instrument_id`, filter by `field`. Under
  `one-instrument-per-metric` the consumer holds one instrument and must
  string-parse the ticker to find its three siblings.

The cost — the extractor must pull four tickers for one instrument — is paid by
a small, **section-gated** WIRP branch (§4), additive and isolated exactly as
`--mode event-calendar` was. The ingestion path is byte-identical to any other
time-series playbook (§4).

**Instrument identity and the typed/attributes mapping.** Each WIRP meeting
instrument is keyed by a synthetic, deterministic `vendor_ticker`:
`WIRP:{central_bank}:{meeting_date}` — e.g. `WIRP:FOMC:2026-06-17`. It is
obviously synthetic (it is not a real Bloomberg ticker — the four real tickers
are derived from it), unique, and stable across runs. `vendor` stays
`Bloomberg` (the data is Bloomberg's). This mirrors the OTR convention of a
synthetic-but-canonical `vendor_ticker` (`/isin/<ISIN>`).

`instrument_master` carries **no `central_bank` or `meeting_date` typed
column** — it has only the generic typed columns plus an `attributes` JSONB
(verified against `database/schema.sql`). So the WIRP meeting instrument maps:

- **Typed columns:** `instrument_type = wirp_meeting`, `asset_class = rates`,
  `curve_family = WIRP`, `country`, `currency`, and **`maturity_date` = the
  meeting date.** Reusing the typed `maturity_date` for the meeting date gives
  the link backfill (§5) a clean typed-column join key — a WIRP meeting
  instrument's `maturity_date` is the natural date its ticker stops updating.
- **`attributes` JSONB:** `central_bank`, `meeting_date` (ISO string, mirrors
  `maturity_date`), `wirp_region_prefix`, `wirp_meeting_token`, and the **four
  source Bloomberg tickers** — one per metric — as flat scalar keys
  `wirp_ticker_fr` / `wirp_ticker_pr` / `wirp_ticker_nm` / `wirp_ticker_ch`.
  Storing the real tickers (not only the synthetic `vendor_ticker`) is
  mandatory provenance: every `market_data_daily` field must be traceable to
  the exact Bloomberg ticker it came from. They are stored as flat scalar
  string keys (not a nested object) so the parquet→`_build_instrument_attributes`
  round-trip is lossless and each is directly queryable
  (`attributes->>'wirp_ticker_pr'`).

### 2. D-wirp is a time-series playbook with a `wirp:` marker section

`wirp.yml` carries the ordinary lineage keys (`playbook_name`,
`playbook_version`, `asset_class: rates`, `dataset_name`, `description`) and an
`extraction` block — plus one new top-level section, **`wirp:`**, the marker
that makes it a WIRP playbook (the same sectioning discipline as
`event_calendar:`, `metadata_history:`, `otr_resolution:`).

**It is a documented exception to the standard time-series playbook contract.**
The normal time-series playbook declares its metrics as `target_metrics`
(`metric_id` → `bloomberg_field`, all pulled via `bdh` on each universe row's
single `ticker`). A WIRP meeting's four metrics are **ticker-borne, not
field-borne** — each is a *different* Bloomberg ticker, not a different field on
one ticker — so `wirp.yml` carries **no `target_metrics` / `reference_metrics`**
and declares its metrics in the `wirp:` section instead. This is a sanctioned
variant, gated by the `wirp:` section exactly as ADR 0008 made the event
playbook "a distinct KIND": a playbook with a `wirp:` section IS a WIRP
playbook, dispatched to the WIRP branch (§4); a playbook without one is
unaffected. The exception is recorded here so a future reader does not mistake
the missing `target_metrics` for a malformed playbook.

```yaml
playbook_name: wirp
playbook_version: "1.0"
asset_class: rates
dataset_name: wirp
description: |
  WIRP per-meeting implied-rate time series ...
extraction:
  incremental_window_days: 1000   # deep — must span every meeting's run-up

wirp:
  bloomberg_field: PX_LAST                 # VERIFIED — the daily series field
  metrics:                                 # WIRP code -> market_data_daily.field
    - { code: FR, field: WIRP_IMPLIED_RATE }
    - { code: PR, field: WIRP_MOVE_PROB }
    - { code: NM, field: WIRP_NUM_MOVES }
    - { code: CH, field: WIRP_RATE_CHANGE }
  region_prefixes:                         # central_bank -> ticker region prefix
    FOMC: US0B
    ECB:  EZ0B
    BOE:  GB0B
    BOJ:  JP0B

universe: []     # GENERATED — see §3. The seed ships empty.
```

The `universe` is left **empty in the git seed** — it is generated (§3). The
seed is a versioned, operator-reviewed config artifact; the universe is data
derived from the meeting calendar.

### 3. The universe is generated from the central-bank-meeting calendar

`event_calendar` already holds the authoritative meeting calendar — 88
`event_category = 'central_bank_meeting'` rows, ingested by D-cb on 2026-05-21.
A new local renderer, **`utils/render_wirp_universe.py`**, expands the empty
seed into the effective `wirp.yml`:

- It reads `macro_data.event_calendar WHERE event_category = 'central_bank_meeting'`
  — `central_bank`, `country`, `currency`, `release_date` per meeting. It does
  **not** call Bloomberg: the calendar is already materialised in the DB by
  D-cb, which is precisely why D-wirp depends on D-cb.
- For each meeting it emits one `universe` row — the synthetic instrument:

  ```yaml
  - ticker: "WIRP:FOMC:2026-06-17"     # synthetic vendor_ticker (§1)
    instrument_type: wirp_meeting
    curve_family: WIRP
    country: US
    currency: USD
    maturity_date: 2026-06-17          # = meeting date -> typed instrument_master column (§1)
    central_bank: FOMC                 # -> attributes (no typed column exists)
    meeting_date: 2026-06-17           # -> attributes (ISO string mirror)
    wirp_region_prefix: US0B           # -> attributes; the extractor builds tickers from it
    wirp_meeting_token: JUN2026        # -> attributes; MMMYYYY ticker month token
    wirp_ticker_fr: "US0BFR JUN2026 Index"   # -> attributes; source-ticker provenance (§1)
    wirp_ticker_pr: "US0BPR JUN2026 Index"
    wirp_ticker_nm: "US0BNM JUN2026 Index"
    wirp_ticker_ch: "US0BCH JUN2026 Index"
  ```

  The renderer computes the four `wirp_ticker_*` values itself, from the region
  prefix and the meeting token, so the rendered universe carries the exact
  Bloomberg tickers the extractor will pull — they are config-reviewable and
  become the per-field provenance stored on `instrument_master` (§1).

- It renders the complete `wirp.yml` (provenance header + body) and uploads it
  to `gs://<bucket>/playbooks/wirp.yml`, exactly as
  `render_effective_universe.py` does for OTR. The provenance header records the
  seed hash, the rendered-body hash, the `event_calendar` meeting-row count and
  latest `created_at`, and the render time — so the universe behind any
  extraction run is auditable and replayable (P4).
- **`push_playbooks.py` skips `wirp.yml`** — a new `wirp:`-section check
  alongside the existing `otr_resolution` check — so the static empty-universe
  seed never clobbers the rendered effective universe in the bucket. The
  renderer owns the upload. `render_wirp_universe.py` runs in the same
  `push-playbooks` docker service, after `render_effective_universe.py`.
- This is the OTR rendered-universe pattern (ADR 0007 §7) reused: config flows
  down (git seed → render → GCS), facts flow up as data. The git seed is never
  machine-edited.

**Horizon, and the render-then-probe order.** WIRP only prices meetings inside
the rate curve's horizon, so a far-future meeting may have no WIRP ticker yet;
a far-past meeting's tickers may have aged out of the daily-history window. The
renderer therefore accepts optional `forward_horizon` / `past_horizon`
parameters (`wirp:`-section keys) and emits a universe row only for meetings
inside `[today − past_horizon, today + forward_horizon]`. These bounds are **set
from the Stage-B coverage probe (§6), not guessed (P2)** — which creates a
deliberate two-pass order:

1. **First render — unbounded.** With no horizon set, the renderer emits a row
   for *every* `central_bank_meeting` in `event_calendar`. This rendered
   `wirp.yml` is the Stage-B probe's input (§6).
2. The probe reports which meetings/metrics actually return data → the verified
   horizons are written into the `wirp:` section.
3. **Production render — bounded.** Subsequent renders apply the horizons, so
   the live universe stays inside the verified-coverage band and the strict
   coverage gate (§4) is never tripped by a known-empty far-horizon meeting.

### 4. The extractor — a section-gated WIRP branch, incremental-only

`run_incremental_extraction()` gains a WIRP branch, dispatched on the `wirp:`
section being present — the same early-dispatch point as the existing
`event_calendar` skip. For a WIRP playbook the branch, per universe row:

1. Reconstructs the four real Bloomberg tickers from the row's
   `wirp_region_prefix` + `wirp_meeting_token`: `{prefix}{code} {token} Index`
   for each metric `code`.
2. `bdh(<ticker>, PX_LAST, window)` each, over the `incremental_window_days`
   window.
3. Emits long-format rows on the **standard parquet contract**
   (`trade_date, ticker, field_name, field_value` + the lineage/identity
   columns), with `ticker` = the synthetic `vendor_ticker` and `field_name` =
   the metric's mapped `field` (`WIRP_IMPLIED_RATE`, …).
4. Uploads to `gs://<bucket>/data/wirp/` — the ordinary time-series data
   prefix.

From there **PHASE 2 ingestion is byte-identical to any other time-series
playbook** — `_infer_instrument_type` reads the explicit `wirp_meeting`,
`upsert_instrument_master` + `upsert_market_data_daily` fold it into
`market_data_daily`. No ingester change for WIRP market data.

**Coverage gate — strict per-meeting 4/4.** A "1-of-4 tickers returned" rule
would let `WIRP_IMPLIED_RATE` load while `WIRP_MOVE_PROB` / `WIRP_NUM_MOVES` /
`WIRP_RATE_CHANGE` silently vanish, and the meeting would still pass — a
partial, corrupt load ingesting as a clean `SUCCESS`, exactly what the
coverage-gate discipline exists to prevent. So the WIRP branch enforces a
**strict per-meeting rule: every included meeting must return all of its
*required* metrics** — the meeting fails extraction otherwise. The required set
is the four metrics in the `wirp:` section, **minus** any a metric explicitly
marks unavailable. A metric may carry `available: false` for a region the
Stage-B probe (§6) confirmed has no such WIRP series — the same documented
opt-out shape as D-econ's `survey: false` (ADR 0008 §2); its column is then
deliberately absent and the playbook records the absence rather than the
extractor silently dropping it. On top of the per-meeting rule the existing
per-playbook 90% coverage gate still applies across meetings, and — because the
§3 horizon keeps the universe inside the verified band — a known-empty
far-horizon meeting is simply not in the universe rather than failing the gate.

**Incremental-only (no historical extractor change).** WIRP extraction is added
to `incremental_extractor.py` **only**. `historical_extractor.py` is left
untouched and learns to **skip** `wirp:` playbooks (the same clean skip it
already does for `event_calendar:` playbooks) so a WIRP playbook in the bucket
is never mis-processed or mis-reported as a failure. This is deliberate and
consistent with the whole B2 family (event extraction is incremental-only, ADR
0008 §4; OTR resolution is incremental-only, ADR 0007 §2):

- `incremental_window_days` is set **deep** (1000 days) so a single run spans
  every meeting's full run-up — realised meetings included. This is the same
  "incremental extractor, deep re-pull window, idempotent upsert" pattern the
  D-econ / D-cb playbooks use (`lookback_days: 1000`). It is "incremental" in
  the dispatch sense, not the narrow-window sense.
- Leaving `historical_extractor.py` untouched is the lowest-risk choice for the
  backward-compatibility constraint: zero existing time-series behaviour can
  change.

### 5. The `related_instrument_id` link — durable preservation + a backfill step

The `event_calendar` central-bank-meeting row and its WIRP meeting instrument
share `(central_bank, meeting_date)` — `release_date` on the event row equals
`meeting_date` on the instrument. The link is set in two parts:

**(a) The backfill step — `utils/backfill_wirp_links.py`.** A local,
idempotent step run after WIRP ingestion. It joins on the typed `maturity_date`
column (= the meeting date, §1) and the `central_bank` stored in `attributes`
(`instrument_master` has no `central_bank` typed column), and links **only WIRP
instruments that actually have market data**:

```sql
UPDATE macro_data.event_calendar ec
   SET related_instrument_id = im.instrument_id
  FROM macro_data.instrument_master im
 WHERE im.instrument_type   = 'wirp_meeting'
   AND ec.event_category    = 'central_bank_meeting'
   AND ec.central_bank      = im.attributes->>'central_bank'
   AND ec.release_date      = im.maturity_date
   AND EXISTS (SELECT 1 FROM macro_data.market_data_daily md
                WHERE md.instrument_id = im.instrument_id)
   AND ec.related_instrument_id IS DISTINCT FROM im.instrument_id;
```

The `EXISTS (market_data_daily …)` clause is not optional. The ingester upserts
`instrument_master` **outside** the critical market-data transaction (verified —
`ingest_parquet.py` step 5 commits it before the `engine.begin()` block, by
design, since `instrument_master` is idempotent on `(vendor, vendor_ticker)`).
So a WIRP load that fails *after* the instrument-master upsert leaves orphan
`wirp_meeting` instruments with **zero** `market_data_daily` rows. Linking an
`event_calendar` row to such an orphan would be a dangling FK to a
data-less instrument. The `EXISTS` clause makes the backfill self-validating: a
WIRP instrument is linked only once its daily series is genuinely present.

It runs each operator cycle (cheap, idempotent) so newly-created WIRP
instruments — new future meetings — get linked as the calendar extends, and so
a link wiped by a D-cb re-run earlier in the same cycle is re-established.

**(b) `upsert_event_calendar` preserves a set `related_instrument_id`.** The
backfill alone is not enough: the D-cb event extractor re-pulls the whole
calendar every run and re-upserts the central-bank-meeting rows with
`related_instrument_id = NULL`, and the full-row upsert would wipe the link.
Therefore `upsert_event_calendar`'s `ON CONFLICT DO UPDATE` is amended for this
**one** column:

```
related_instrument_id = COALESCE(EXCLUDED.related_instrument_id,
                                 event_calendar.related_instrument_id)
```

Every other column keeps the full-row-overwrite contract unchanged. This
surgical exception is justified — and is *not* the `COALESCE(excluded,
existing)` merge ADR 0008 deliberately rejected for the row as a whole:

- `related_instrument_id` is **structurally different** from every other
  `event_calendar` column. The others are vendor-supplied event attributes
  written by one pipeline (the Bloomberg-PC event extractor). This column is a
  **DB-internal FK written by a different pipeline** (D-wirp's local backfill).
  The event extractor *never* has a real value for it — it has only `NULL`,
  always.
- ADR 0008 gave two reasons for rejecting a merge: (i) consistency with the
  sibling full-row upserts; (ii) preserving the ability to correct a field back
  to `NULL`. Reason (ii) does not apply — the event extractor has no
  non-NULL→NULL correction to make for this column; if a link ever must be
  cleared, the backfill step (the only writer that sets it) clears it
  explicitly. Reason (i) is outweighed: a cross-pipeline FK that one pipeline
  can only ever NULL is a genuinely different case, and silently wiping a valid
  FK every D-cb run is the worse inconsistency.
- It is backward-compatible: `related_instrument_id` is `NULL` for every
  `event_calendar` row today, so changing overwrite→COALESCE changes the
  behaviour of *no* current data flow — it is purely enabling.

`upsert_event_calendar`'s docstring is updated to record this one documented
exception and why.

### 6. Verification — a full-calendar WIRP coverage probe (Stage B)

The ticker grammar is verified; full-calendar coverage is not. Before
`wirp.yml` ships, an operator-run probe constructs the WIRP tickers for **every
meeting** and reports, per meeting and metric, whether `bdh(PX_LAST)` returns
data, the series span, and the observed value range (the last feeds the §1
units note). This establishes the real forward and past horizons empirically
(P2 — accuracy or refuse, not a guessed horizon) and feeds the §3 horizon
parameters and the strict §4 coverage gate.

**The probe's input — the rendered `wirp.yml`, not the database.** The probe
runs on the Bloomberg PC, which has **no Postgres access** — it cannot read
`event_calendar` directly. The handoff is therefore explicit and ordered:

1. Locally, run `render_wirp_universe.py` **unbounded** (§3, first render) — it
   reads `event_calendar` and emits a `wirp.yml` whose `universe` carries every
   meeting with its `wirp_region_prefix` + `wirp_meeting_token`.
2. The operator copies that rendered `wirp.yml` to the Bloomberg PC alongside
   the probe script.
3. The probe reads the rendered `wirp.yml`'s `universe`, builds the four
   tickers per meeting from the prefix + token, and `bdh`'s each.

The probe thus verifies the **exact** universe the extractor will use — derived
from the DB-owned D-cb calendar — without re-pulling meeting dates from
Bloomberg `bds` (which would verify a *different* calendar than the one D-cb
ingested). The probe reuses the Section-3 machinery already in
`scripts/event_data_bloomberg_check.py`, iterating the rendered universe rather
than one next + one past meeting per bank.

## Alternatives considered

- **`one-instrument-per-metric`** (each WIRP ticker its own instrument). Fits
  the vanilla `bdh(one ticker, [fields])` loop with zero extractor code.
  Rejected: it dishonours the data model (`field = 'PX_LAST'` placeholder; the
  metric buried in the ticker), and it leaves `related_instrument_id` with four
  candidate targets per meeting and no non-arbitrary FK — defeating the column
  ADR 0004 created. The saved extractor code is a small, isolated, section-gated
  branch; not worth the modelling debt.
- **Generate the universe from a fresh `bds(ECO_RELEASE_DT_LIST)` call** rather
  than from `event_calendar`. Rejected: the meeting calendar is already
  authoritative in `event_calendar` after D-cb; re-pulling it from Bloomberg
  would add a terminal dependency to a step that otherwise runs purely locally,
  and risks the rendered universe disagreeing with the ingested D-cb calendar
  it must link to.
- **Add WIRP to `historical_extractor.py` for a one-time deep backfill, then
  incremental.** Rejected for v1: a deep `incremental_window_days` on the
  incremental extractor captures every meeting's run-up in one pass, and
  touching `historical_extractor.py` widens the backward-compatibility blast
  radius for no v1 benefit. Consistent with the rest of B2 being
  incremental-only.
- **Leave `related_instrument_id` to a pure full-row re-upsert** (the backfill
  re-sends the complete event row including the FK). Rejected: the *next* D-cb
  extraction would still wipe it — durability requires either the COALESCE
  preservation (§5b) or the event extractor knowing DB ids, and the latter is
  impossible by design.
- **Derive the event↔WIRP link at query time** by joining on
  `(central_bank, date)` instead of storing `related_instrument_id`. Rejected:
  ADR 0004 created the column for exactly this link; a stored FK is indexed
  (`idx_event_calendar_related_instrument`) and is the contract Phase-3
  consumers expect.

## Consequences

**Positive.** WIRP is modelled as the entity it is — a meeting with four
metrics — giving the Phase-3 `fomc_surprise` primitive and the FOMC event-study
template a clean `instrument_id`-keyed series and an unambiguous
`related_instrument_id` link. The universe self-extends as the central-bank
calendar grows, with no playbook hand-editing and no machine writing to git
(the OTR rendered-universe pattern, reused). WIRP market data ingests through
the unchanged PHASE 2 path. `historical_extractor.py` is untouched.

**Negative / accepted.** A section-gated WIRP branch is added to
`incremental_extractor.py` (isolated, additive — non-WIRP playbooks take a
byte-identical path). `upsert_event_calendar` gains one documented column-level
exception to its full-row-overwrite contract (§5b, justified). v1 is
incremental-only — no separate deep historical mode — mitigated by a deep
re-pull window. The WIRP universe horizon is bounded by what the Stage-B probe
verifies; meetings beyond the WIRP pricing edge are deliberately not in the
universe until they enter it (P5 — disclosed, not silently dropped).

## Rollout

1. **This ADR** — the design.
2. **Stage A** — `upsert_event_calendar` `related_instrument_id` preservation +
   docstring; `utils/backfill_wirp_links.py`; tests. *(Independent of B–D.)*
3. **Stage B** — `utils/render_wirp_universe.py` + the initial `wirp.yml` seed
   (ticker grammar, metrics, region prefixes; no horizons → unbounded render);
   `push_playbooks.py` skips `wirp:` playbooks; the full-calendar WIRP coverage
   probe (`scripts/wirp_coverage_check.py`). The renderer and seed are built
   here because the probe consumes the rendered `wirp.yml` (§6); the
   `push_playbooks.py` skip is built here too — not deferred to Stage C — since
   the skip must exist the moment the seed file does, else a `push_playbooks`
   run would upload the empty-universe seed and clobber the rendered universe.
   Operator: render locally → copy the rendered `wirp.yml` + probe to the
   Bloomberg PC → run the probe → report coverage, series spans, and observed
   value ranges.
4. **Stage C** — the WIRP extractor branch in `incremental_extractor.py`;
   `historical_extractor.py` skips `wirp:` playbooks; `docker-compose.yml`
   wires `render_wirp_universe.py` into the `push-playbooks` service; tests.
5. **Stage D** — finalise `wirp.yml` (verified horizons + unit comments + any
   `available: false` from Stage B); operator render → extract → ingest →
   backfill → SQL-verify.

## Version log

| Version | Date | Change |
|---|---|---|
| v1 | 2026-05-22 | Initial decision. Proposed. |
| v2 | 2026-05-22 | Amended per Codex review (all 7 findings, verified valid): fixed the §5 backfill join to the real `instrument_master` schema (no `central_bank`/`meeting_date` typed columns — join `maturity_date` + `attributes->>'central_bank'`); renamed `WIRP_BP_CHANGE`→`WIRP_RATE_CHANGE` (no unverified unit in the name, no conversion); strict per-meeting 4/4 coverage gate with an `available: false` opt-out; source Bloomberg tickers stored in `instrument_master.attributes`; the Stage-B probe consumes the rendered `wirp.yml` (the Bloomberg PC has no Postgres); the backfill links only WIRP instruments with `market_data_daily` rows (`instrument_master` upsert commits outside the critical txn); `wirp.yml`'s missing `target_metrics` documented as a sanctioned contract exception. |
| v3 | 2026-05-22 | Rollout refinement during the Stage-B build: the `push_playbooks.py` `wirp:` skip moved from Stage C to Stage B — the skip must exist the moment the `wirp.yml` seed file does, or a `push_playbooks` run would upload the empty-universe seed and clobber the rendered universe. |
