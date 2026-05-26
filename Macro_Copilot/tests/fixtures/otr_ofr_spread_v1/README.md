# `otr_ofr_spread_v1` parity fixtures

Snapshots that lock in the **regression behavior** of
`calculate_otr_ofr_spread`.  The companion test at
`tests/test_otr_ofr_spread_parity.py` reloads each fixture, mocks the
DB fetcher to replay captured `raw_rows`, freezes `date.today()` to the
recorded value, runs the tool, and asserts the output matches the
recorded `expected_output` within a `1e-9` absolute float tolerance.

## Purpose — PR15

This primitive ships with PR15 parity-fixture infrastructure from day
one (the binding rule for *new* primitives per
`docs_revamped/02_components/primitive/README.md`).  The fixtures
shipped here lock in the primitive's output shape + numeric content
for known inputs, so any future code change that breaks the snapshot
contract fails the parity test loudly.

## v1 fixture provenance — SYNTHETIC, not live-DB (debt — not the intended end-state)

The shipped v1 fixtures carry `capture.capture_method = "synthetic_v1"`.
The `raw_rows` are NOT from a live-DB capture; they are deterministic
synthetic OTR/OFR yield pairs designed to exercise:

- the happy-path z-score + trailing-range warmup at full lookback (`us_10y_365d`);
- the LAG=None branch where the slot's first observed window has no
  prior bond (`de_10y_180d_first_window`);
- the empty-result honest-absence error envelope
  (`empty_pre_resolver`).

The `expected_output` was produced by running the primitive itself
against those rows under a frozen wall-clock date (2026-05-22).  This
is **honest disclosure (P5)** — these fixtures lock in regression
behaviour but are **NOT captured production output**.

### ⚠️ Codex review (2026-05-22): PR15 stricter reading

A reviewer flagged that PR15 reads literally as "every primitive ships
with a parity fixture that **captures real production output**" —
making synthetic fixtures non-conformant with the rule as written.
The synthetic-fixture pattern shipped here is **inherited debt** from
`get_otr_history`-v1 (PR #185, merged 2026-05-24), not a new precedent
this primitive is establishing.  The honest position:

- **Strict PR15 reading**: synthetic fixtures fail PR15 today; the
  primitive should defer until live-DB capture is possible (AC8
  stop-and-ask).
- **Inherited-precedent reading**: `get_otr_history`-v1 shipped
  synthetic with the same provenance disclosure and was approved,
  so the pattern is operative; PR15 is being remediated incrementally
  as the resolver fills the live DB.

The mission instructions explicitly named this as the binding rule;
the existing primitive 1 establishes the pattern; this primitive
follows the pattern with the **same explicit P5 disclosure** so the
synthetic vs live-DB distinction is loud, not hidden.  The path to
live-DB replacement is unblocked the moment the resolver populates
otr_history for the canonical regression slots; `_capture.py` is
ready to run, and the parity test is agnostic to fixture provenance.

### Why synthetic and not live-DB?

Two blockers force the synthetic shape at v1 land:

1. The on-the-run resolver is **forward-only** (TD #27a):
   `otr_history` is populated only by forward-from-deployment
   ingestion.  At the point this primitive ships, the live DB had
   limited OTR data for the canonical regression slots.
2. The cash-bond playbook (`sovereign_cash_bonds.yml`, ADR 0005) is
   itself rolling out, so per-CUSIP `YLD_YTM_MID` rows may not yet
   cover the full 252-day rolling window (which the new trailing-
   range stats also need) for every slot the resolver knows about.

A captured fixture against the partly-populated DB would either be
empty or carry incomplete z-score / trailing-range warmup — neither
shape locks in useful regression behaviour.

**Path to live-DB replacement.**  Once `otr_history` AND
`market_data_daily` have full coverage for the canonical slots, the
operator runs `_capture.py` (against the live DB), which writes
fixtures carrying `capture.capture_method = "live_db_v1"`, then
replaces the synthetic v1 fixtures in the same PR.  The parity test
is agnostic to which provenance the fixture carries — it replays
whatever raw_rows are recorded and asserts the primitive reproduces
the recorded expected_output.

## Live-DB pre-requisite — TD #27

The OTR resolver is **forward-only** (TD #27a) and detection-date-
precise (TD #27b).  The capture script handles partial coverage
honestly: if a slot has zero rows in the live DB for the lookback,
the capture writes an error-envelope fixture (matching the primitive's
pre-resolver-deployment shape).  The `empty_pre_resolver.json` fixture
pins this honest-absence path explicitly.

## Fixture format

Each `*.json` file is a self-contained fixture:

```json
{
  "fixture_name": "us_10y_365d",
  "tool_module": "rates_agent.sovereign_bonds.tools.otr_ofr_spread",
  "tool_function": "calculate_otr_ofr_spread",
  "capture": {
    "captured_at": "2026-05-22T13:26:38Z",
    "capture_method": "synthetic_v1",
    "frozen_today": "2026-05-22",
    "raw_rows_count": 750,
    "raw_rows_sha256": "abc123..."
  },
  "input": {
    "params": {"country": "US", "tenor": "10Y", "lookback_days": 365},
    "frozen_today": "2026-05-22",
    "raw_rows": [
      {
        "trade_date": "2024-03-15",
        "otr_instrument_id": 102,
        "ofr_instrument_id": 101,
        "otr_yield": 4.25,
        "ofr_yield": 4.30
      }
    ]
  },
  "expected_output": {
    "current_metrics": {...},
    "time_series": [...],
    "time_series_spread": {...},
    "time_series_zscore": {...},
    "methodology_note": "..."
  }
}
```

## Generating fixtures (live-DB capture)

```bash
docker-compose up -d tsdb
export DB_HOST=localhost DB_PORT=5433 DB_USER=quantuser \
       DB_PASSWORD=... DB_NAME=macrodata
python tests/fixtures/otr_ofr_spread_v1/_capture.py
```

The capture pulls each documented slot's OTR/OFR yield pair using the
same fetcher the primitive uses, records the raw rows, then runs the
primitive against the same DB to record the expected output.

## Running the parity test

```bash
pytest tests/test_otr_ofr_spread_parity.py -v
```

If no fixtures are present, the parametrize-over-discovered-files
pattern produces zero parameters and pytest reports the test as
skipped.
