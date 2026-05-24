# `wirp_meeting_pricing_v1` parity fixtures

Snapshots that lock in the **regression behavior** of
`calculate_wirp_meeting_pricing` for the four supported central
banks (FOMC / ECB / BOE / BOJ).

## Purpose — PR15

This primitive ships with PR15 parity-fixture infrastructure from
day one (the binding rule for *new* primitives per
`docs_revamped/02_components/primitive/README.md`).

## v1 fixture provenance — LIVE-DB CAPTURES (strict PR15 compliance)

All four shipped v1 fixtures carry `capture.capture_method =
"live_db_v1"`.  The `raw_rows` are real captures from the live
TimescaleDB; `expected_output` is the primitive's actual output
against those rows.

| Fixture | Provenance | Purpose |
|---|---|---|
| `fomc_next_6.json` | `live_db_v1` (32 rows) | FOMC next 6 meetings — captures the standard US forward-strip read. |
| `ecb_next_6.json` | `live_db_v1` (32 rows) | ECB next 6 meetings — exercises the EZ0B region prefix. |
| `boe_next_6.json` | `live_db_v1` (32 rows) | BOE next 6 meetings — exercises the GB0B region prefix. |
| `boj_next_4.json` | `live_db_v1` (20 rows) | BOJ next 4 meetings — exercises the JP0B region prefix and the smaller-N path. |

**Strict PR15 compliance.**  Inherits the post-Codex-review
approach from cpi_surprise / nfp_surprise — fixtures are live-DB
captures from day one, not synthetic.  No edge-case synthetic
fixture is needed for v1: the four central banks already have
realised WIRP data ingested per ADR 0009.

## Live-DB pre-requisite — ADR 0009

The D-wirp playbook has populated `macro_data.instrument_master`
(synthetic `wirp_meeting` rows) and `macro_data.market_data_daily`
(the four WIRP fields per ADR 0009 §1) for FOMC / ECB / BOE / BOJ
through the WIRP horizon.

## Fixture format

Each `*.json` file is a self-contained fixture:

```json
{
  "fixture_name": "fomc_next_6",
  "tool_module": "rates_agent.ois.tools.wirp_meeting_pricing",
  "tool_function": "calculate_wirp_meeting_pricing",
  "capture": {
    "captured_at": "...",
    "capture_method": "live_db_v1",
    "database_name": "macrodata",
    "frozen_today": "...",
    "raw_rows_count": 32,
    "raw_rows_sha256": "abc..."
  },
  "input": {
    "params": {
      "central_bank": "FOMC",
      "selection_mode": "next_n_meetings",
      "n_meetings": 6
    },
    "frozen_today": "...",
    "raw_rows": [
      {
        "instrument_id": ...,
        "vendor_ticker": "WIRP:FOMC:2026-06-17",
        "meeting_date": "2026-06-17",
        "central_bank": "FOMC",
        "meeting_token": "JUN2026",
        "bloomberg_ticker_fr": "US0BFR JUN2026 Index",
        "bloomberg_ticker_pr": "US0BPR JUN2026 Index",
        "bloomberg_ticker_nm": "US0BNM JUN2026 Index",
        "bloomberg_ticker_ch": "US0BCH JUN2026 Index",
        "field_name": "WIRP_IMPLIED_RATE",
        "field_value": 3.637,
        "as_of_date": "2026-05-22"
      }
    ]
  },
  "expected_output": {...}
}
```

## Regenerating fixtures (live-DB capture)

Only after a deliberate methodology change.  When the WIRP horizon
advances (new meetings ingested), re-running the capture refreshes
the fixtures:

```bash
docker-compose up -d tsdb
export DB_HOST=localhost DB_PORT=5433 DB_USER=quantuser \
       DB_PASSWORD=... DB_NAME=macrodata
python tests/fixtures/wirp_meeting_pricing_v1/_capture.py
```

## Running the parity test

```bash
pytest tests/test_wirp_meeting_pricing_parity.py -v
```
