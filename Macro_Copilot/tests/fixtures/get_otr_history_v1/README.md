# `get_otr_history_v1` parity fixtures

Snapshots of the **real production output** of `get_otr_history`
captured against the live TimescaleDB.  The companion test at
`tests/test_get_otr_history_parity.py` reloads each fixture, mocks the
DB engine to replay captured `raw_rows`, freezes `date.today()` to the
recorded value, runs the tool, and asserts the output matches the
recorded `expected_output` byte-for-byte (modulo `1e-9` float tolerance
on any numerics, exact on identifiers / dates).

## Purpose — PR15

This primitive is shipping with PR15 parity-fixture infrastructure from
day one (the binding rule for *new* primitives in the contract at
`docs_revamped/02_components/primitive/README.md`).  Capturing the
fixture data requires a live `macro_data.otr_history` table populated
by the resolver; the test skips cleanly when no fixtures are present.

## Live-DB pre-requisite — TD #27

The OTR resolver is **forward-only** (TD #27a): `otr_history` may be
empty for slots the resolver has not yet observed.  The capture script
handles this honestly by recording captured rows for every slot it
attempts; if a slot has zero rows, the script records that absence
without inventing data (the fixture file is still written, with
`expected_output.transitions == []` and `current_metrics` identity
fields `None`).  This matches the primitive's honest-absence shape and
keeps the parity fixture a faithful production snapshot.

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
