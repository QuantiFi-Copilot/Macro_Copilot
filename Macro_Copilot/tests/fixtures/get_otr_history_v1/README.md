# `get_otr_history_v1` parity fixtures

Snapshots that lock in the **regression behavior** of `get_otr_history`.
The companion test at `tests/test_get_otr_history_parity.py` reloads
each fixture, mocks the DB engine to replay captured `raw_rows`,
freezes `date.today()` to the recorded value, runs the tool, and
asserts the output matches the recorded `expected_output` byte-for-byte
(modulo `1e-9` float tolerance on any numerics, exact on identifiers /
dates).

## Purpose — PR15

This primitive ships with PR15 parity-fixture infrastructure from day
one (the binding rule for *new* primitives per
`docs_revamped/02_components/primitive/README.md`).  The fixtures
shipped here lock in the primitive's output shape + content for known
inputs, so any future code change that breaks the snapshot+transitions
contract fails the parity test loudly.

## v1 fixture provenance — synthetic, not live-DB

The shipped v1 fixtures carry `capture.capture_method = "synthetic_v1"`.
The `raw_rows` are not from a captured live-DB run; they are realistic
representative SCD2 windows for the labelled slots (`US 10Y`, `DE 10Y`,
plus the honest-absence `ZA 10Y` empty case).  The `expected_output`
was produced by running the primitive itself against those rows under
a frozen wall-clock date.  This is honest disclosure (P5) — these
fixtures lock in regression behavior but are **not** captured
production output.

**Why synthetic and not live-DB?**  The on-the-run resolver is
**forward-only** (TD #27a): `otr_history` is populated only by
forward-from-deployment ingestion, with no historical backfill.  At
the point this primitive shipped, the live DB had not yet been
populated for the canonical regression slots, so a captured fixture
would either (a) be empty for every slot (honest-absence-only
coverage), or (b) await operator deployment of the resolver.

**Path to live-DB replacement.**  Once the resolver has populated
`otr_history` for the canonical slots, the operator runs
`_capture.py` (against the live DB) which writes fixtures carrying
`capture.capture_method = "live_db_v1"`, then replaces the synthetic
v1 fixtures in the same PR.  The parity test is agnostic to which
provenance the fixture carries — it replays whatever raw_rows are
recorded and asserts the primitive reproduces the recorded
expected_output.

## Live-DB pre-requisite — TD #27

The OTR resolver is **forward-only** (TD #27a): `otr_history` may be
empty for slots the resolver has not yet observed.  The capture
script handles this honestly by recording captured rows for every
slot it attempts; if a slot has zero rows, the script records that
absence without inventing data (the fixture file is still written,
with `expected_output.transitions == []` and `current_metrics`
identity fields `None`).  The `empty_absence.json` fixture pins this
honest-absence path explicitly.

## Fixture format

Each `*.json` file is a self-contained fixture:

```json
{
  "fixture_name": "us_10y_252d",
  "tool_module": "rates_agent.sovereign_bonds.tools.get_otr_history",
  "tool_function": "get_otr_history",
  "capture": {
    "captured_at": "2026-05-24T15:23:01Z",
    "database_name": "macrodata",
    "frozen_today": "2026-05-24",
    "raw_rows_count": 3,
    "raw_rows_sha256": "abc123..."
  },
  "input": {
    "params": {"country": "US", "tenor": "10Y", "lookback_days": 252},
    "frozen_today": "2026-05-24",
    "raw_rows": [
      {
        "effective_from": "2025-08-15",
        "effective_to": "2025-11-14",
        "otr_instrument_id": 101,
        "cusip": "91282CKZ4",
        "isin": "US91282CKZ40",
        "vendor_ticker": "/cusip/91282CKZ4",
        "maturity_date": "2034-08-15"
      }
    ]
  },
  "expected_output": {
    "current_metrics": {...},
    "transitions": [...],
    "methodology_note": "..."
  }
}
```

## Generating fixtures (live-DB capture)

```bash
docker-compose up -d tsdb
export DB_HOST=localhost DB_PORT=5433 DB_USER=quantuser \
       DB_PASSWORD=... DB_NAME=macrodata
python tests/fixtures/get_otr_history_v1/_capture.py
```

The capture pulls each documented slot from `macro_data.otr_history`
using the same SQL the primitive issues, records the raw rows, then
runs the primitive against the same DB to record the expected output.

## Running the parity test

```bash
pytest tests/test_get_otr_history_parity.py -v
```

If no fixtures are present, the parametrize-over-discovered-files
pattern produces zero parameters and pytest reports the test as
skipped (deliberate — fixtures must be captured against a live DB
populated by the resolver).
