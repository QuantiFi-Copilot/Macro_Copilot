# `nfp_surprise_v1` parity fixtures

Snapshots that lock in the **regression behavior** of
`calculate_nfp_surprise`.  The companion test at
`tests/test_nfp_surprise_parity.py` reloads each fixture, mocks the
event-calendar fetcher to replay captured `raw_rows`, freezes
`date.today()` to the recorded value, runs the tool, and asserts the
output matches the recorded `expected_output` within a `1e-9`
absolute float tolerance.

## Purpose — PR15

This primitive ships with PR15 parity-fixture infrastructure from
day one (the binding rule for *new* primitives per
`docs_revamped/02_components/primitive/README.md`).

## v1 fixture provenance — LIVE-DB CAPTURE + one synthetic empty-case fixture

The shipped v1 fixtures are a **mix** of live-DB captures and one
synthetic edge-case fixture.  Each `*.json` file declares its
provenance under `capture.capture_method`:

| Fixture | Provenance | Purpose |
|---|---|---|
| `us_nfp_16releases.json` | `live_db_v1` (`macrodata`, 16 raw rows) | Real captured US NFP releases (Jan 2025 → May 2026 — within ECO_RELEASE_DT_LIST window per TD #28b).  16 realised → 16-row display + post-warmup z-score on the trailing 10 rows. |
| `empty_pre_extractor.json` | `synthetic_v1` (0 raw rows) | Pins the error-envelope shape for the pre-extractor-deployment / honest-absence path.  Cannot be captured from the live DB because the US NFP slot now has realised data — this is a deliberate synthetic edge-case fixture. |

**PR15 compliance:** the happy-path fixture satisfies PR15 strictly
— it captures real production output against the live TimescaleDB.
The `empty_pre_extractor.json` fixture is intentionally synthetic
because the live DB cannot reproduce its precondition (zero realised
rows for US NFP).

This matches the strict-PR15-compliance approach cpi_surprise
adopted after the Codex review of PR #187, applied here from day
one.

## Live-DB pre-requisite — ADR 0008 §6 / TD #28b

The event extractor is forward-only and bounded by Bloomberg's
`ECO_RELEASE_DT_LIST` recent-past-plus-forward window (~1.5 years
history + forward scheduled).  At v1 land time, the live DB has
16 realised US NFP releases (Jan 2025 → May 2026) — enough to
exercise both the warmup boundary (`release_z_min_periods=6` fires
z-score after 6 realised observations) and the steady-state
rolling z-score.

## Fixture format

Each `*.json` file is a self-contained fixture:

```json
{
  "fixture_name": "us_nfp_16releases",
  "tool_module": "rates_agent.sovereign_bonds.tools.nfp_surprise",
  "tool_function": "calculate_nfp_surprise",
  "capture": {
    "captured_at": "2026-05-24T...",
    "capture_method": "live_db_v1",
    "database_name": "macrodata",
    "frozen_today": "2026-05-24",
    "raw_rows_count": 16,
    "raw_rows_sha256": "abc123..."
  },
  "input": {
    "params": {"lookback_releases": 16},
    "frozen_today": "2026-05-24",
    "raw_rows": [
      {
        "event_id": ...,
        "event_type": "nfp",
        "event_category": "economic_release",
        "country": "US",
        "currency": "USD",
        "release_date": "2025-01-10",
        "release_time": null,
        "period": "...",
        "actual": 250.0,
        "consensus_median": 165.0,
        "consensus_high": 195.0,
        "consensus_low": 135.0,
        "prior": 212.0,
        "revised_prior": null,
        "surprise_std_dev": 25.0
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
re-running the capture refreshes the live-DB fixture:

```bash
docker-compose up -d tsdb
export DB_HOST=localhost DB_PORT=5433 DB_USER=quantuser \
       DB_PASSWORD=... DB_NAME=macrodata
python tests/fixtures/nfp_surprise_v1/_capture.py
```

## Running the parity test

```bash
pytest tests/test_nfp_surprise_parity.py -v
```
