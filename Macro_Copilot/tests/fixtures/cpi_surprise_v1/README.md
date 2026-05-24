# `cpi_surprise_v1` parity fixtures

Snapshots that lock in the **regression behavior** of
`calculate_cpi_surprise`.  The companion test at
`tests/test_cpi_surprise_parity.py` reloads each fixture, mocks the
event-calendar fetcher to replay captured `raw_rows`, freezes
`date.today()` to the recorded value, runs the tool, and asserts the
output matches the recorded `expected_output` within a `1e-9`
absolute float tolerance.

## Purpose — PR15

This primitive ships with PR15 parity-fixture infrastructure from
day one (the binding rule for *new* primitives per
`docs_revamped/02_components/primitive/README.md`).  The fixtures
lock in the primitive's output shape + numeric content for known
inputs, so any future code change that breaks the snapshot contract
fails the parity test loudly.

## v1 fixture provenance — LIVE-DB CAPTURES + one synthetic empty-case fixture

The shipped v1 fixtures are a **mix** of live-DB captures and one
synthetic edge-case fixture.  Each `*.json` file declares its
provenance under `capture.capture_method`:

| Fixture | Provenance | Purpose |
|---|---|---|
| `us_cpi_16releases.json` | `live_db_v1` (`macrodata`, 16 raw rows) | Real captured US CPI YoY releases (Jan 2025 → May 2026 — within ECO_RELEASE_DT_LIST window per TD #28b). 16 realised → 16-row display + post-warmup z-score on the trailing 10 rows. |
| `eu_hicp_18releases.json` | `live_db_v1` (`macrodata`, 20 raw rows) | Real captured EU HICP YoY releases — exercises the load-bearing EU → `hicp_yoy` country → event_type mapping correctness. |
| `uk_cpi_16releases.json` | `live_db_v1` (`macrodata`, 17 raw rows) | Real captured UK CPI YoY releases. |
| `jp_cpi_12releases.json` | `live_db_v1` (`macrodata`, 17 raw rows) | Real captured Japan CPI YoY releases. |
| `empty_pre_extractor.json` | `synthetic_v1` (0 raw rows) | Pins the error-envelope shape for the pre-extractor-deployment / unknown-country case.  Cannot be captured from the live DB because all 4 canonical supported countries (US/UK/JP/EU) now have realised data — this is a deliberate synthetic edge-case fixture that exercises the empty-window honest-absence path. |

**PR15 compliance:** the four happy-path fixtures satisfy PR15
strictly — they capture real production output against the live
TimescaleDB.  The `empty_pre_extractor.json` fixture is intentionally
synthetic because the live DB cannot reproduce its precondition
(zero realised rows for a supported country, which the v1 event-
extractor coverage no longer permits).

This represents a **stricter compliance level** than the v1 fixtures
of primitives 1 (`get_otr_history`) and 2 (`otr_ofr_spread`), which
shipped entirely synthetic v1 fixtures (inherited debt) — those
will be remediated incrementally as their upstream substrates fill
in.

## Live-DB pre-requisite — ADR 0008 §6 / TD #28b

The event extractor is forward-only and bounded by Bloomberg's
`ECO_RELEASE_DT_LIST` recent-past-plus-forward window (~1.5 years
history + forward scheduled).  At v1 land time, the live DB has
realised CPI / HICP releases:

- US (`cpi_yoy`): 16 realised, Jan 2025 → May 2026
- EU (`hicp_yoy`): 20 realised, Jan 2025 → May 2026
- UK (`cpi_yoy`): 17 realised, Jan 2025 → May 2026
- JP (`cpi_yoy`): 16 realised, Jan 2025 → Apr 2026

These windows cover the post-warmup z-score region (the warmup gate
fires at `release_z_min_periods = 6`) so the fixtures exercise both
the warmup boundary and the steady-state rolling z-score.

## Fixture format

Each `*.json` file is a self-contained fixture:

```json
{
  "fixture_name": "us_cpi_16releases",
  "tool_module": "rates_agent.inflation_swaps.tools.cpi_surprise",
  "tool_function": "calculate_cpi_surprise",
  "capture": {
    "captured_at": "2026-05-24T15:23:01Z",
    "capture_method": "live_db_v1",
    "database_name": "macrodata",
    "frozen_today": "2026-05-24",
    "raw_rows_count": 16,
    "raw_rows_sha256": "abc123..."
  },
  "input": {
    "params": {"country": "US", "lookback_releases": 16},
    "frozen_today": "2026-05-24",
    "raw_rows": [
      {
        "event_id": 5000,
        "event_type": "cpi_yoy",
        "event_category": "economic_release",
        "country": "US",
        "currency": "USD",
        "release_date": "2025-01-15",
        "release_time": null,
        "period": "Dec 2024",
        "actual": 2.9,
        "consensus_median": 2.9,
        "consensus_high": 3.0,
        "consensus_low": 2.8,
        "prior": 2.7,
        "revised_prior": null,
        "surprise_std_dev": 0.05
      }
    ]
  },
  "expected_output": {
    "current_metrics": {...},
    "time_series": [...],
    "time_series_surprise": {...},
    "time_series_zscore": {...},
    "methodology_note": "..."
  }
}
```

## Regenerating fixtures (live-DB capture)

Only after a deliberate methodology change.  When the event
extractor's coverage advances (e.g. TD #28b history backfill lands),
re-running the capture refreshes the live-DB fixtures:

```bash
docker-compose up -d tsdb
export DB_HOST=localhost DB_PORT=5433 DB_USER=quantuser \
       DB_PASSWORD=... DB_NAME=macrodata
python tests/fixtures/cpi_surprise_v1/_capture.py
```

The capture queries `macro_data.event_calendar` for each canonical
slot using the same fetcher the primitive uses, records the raw
rows + SHA-256 hash, then runs the primitive to record the
expected output.  Existing fixtures are overwritten in place; the
parity test will fail loudly on any unintended drift (the SHA
tamper-detection check catches edits without re-running capture).

## Running the parity test

```bash
pytest tests/test_cpi_surprise_parity.py -v
```
