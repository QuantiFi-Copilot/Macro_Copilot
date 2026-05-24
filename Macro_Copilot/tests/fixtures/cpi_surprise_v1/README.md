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

## v1 fixture provenance — SYNTHETIC, not live-DB (debt — not the intended end-state)

The shipped v1 fixtures carry `capture.capture_method = "synthetic_v1"`.
The `raw_rows` are NOT from a live-DB capture; they are deterministic
synthetic event-calendar rows designed to exercise:

- the happy-path `cpi_yoy` series with full z-score warmup
  (`us_cpi_24releases`);
- the `hicp_yoy` resolution path for EU — load-bearing correctness
  check that the country → event_type YAML mapping reaches the
  fetcher correctly (`eu_hicp_18releases`);
- the survey=false honest-absence shape per ADR 0008 §2 — rows with
  `consensus_median=None` emit `surprise_pct=None` rather than a
  fabricated zero (`uk_cpi_with_missing_consensus`);
- the empty-result honest-absence error envelope (TD #28b pre-
  extractor-deployment shape) (`empty_pre_extractor`).

The `expected_output` was produced by running the primitive itself
against those rows under a frozen wall-clock date (2026-05-22).  This
is **honest disclosure (P5)** — these fixtures lock in regression
behaviour but are **NOT captured production output**.

### Codex-review-style PR15 stance

Per the strict reading of PR15 ("captures real production output"),
synthetic fixtures are non-conformant.  This primitive follows the
inherited precedent from `get_otr_history`-v1 (merged 2026-05-24)
and `otr_ofr_spread`-v1 (merged 2026-05-24 — PR #186), which shipped
synthetic-v1 fixtures with explicit P5 disclosure and a documented
live-DB-capture path.  The blocker is identical: the upstream
substrate (event extractor / cash-bond ingestion) is rolling out
and the canonical regression slots do not yet have enough live data
to populate a meaningful 24-release surprise z-score warmup.

The path to live-DB replacement is unblocked the moment
`event_calendar` has realised `cpi_yoy` / `hicp_yoy` rows for the
canonical regression slots — `_capture.py` is ready to run, parity
test is provenance-agnostic.

### Why synthetic and not live-DB?

Two upstream-coverage blockers force the synthetic shape at v1 land:

1. The event extractor (ADR 0008's `--mode event-calendar`) is
   forward-only and bounded by Bloomberg's `ECO_RELEASE_DT_LIST`
   recent-past-plus-forward window (~1.5 years history + forward
   scheduled per ADR 0008 §6 / TD #28b).
2. The `economic_releases.yml` playbook is itself rolling out, so
   per-country CPI / HICP rows may not yet cover the full
   `release_z_window=24` realised releases for every supported
   country.

A captured fixture against the partly-populated DB would either be
empty for some countries or carry incomplete z-score warmup —
neither shape locks in useful regression behaviour.

## Live-DB pre-requisite — ADR 0008 §6 / TD #28b

The event extractor is forward-only.  The capture script
(`_capture.py`) handles partial coverage honestly: if a slot has zero
realised releases in the live DB for the lookback, the capture
writes an error-envelope fixture (matching the primitive's
pre-extractor-deployment shape).  The `empty_pre_extractor.json`
fixture pins this honest-absence path explicitly.

## Fixture format

Each `*.json` file is a self-contained fixture:

```json
{
  "fixture_name": "us_cpi_24releases",
  "tool_module": "rates_agent.inflation_swaps.tools.cpi_surprise",
  "tool_function": "calculate_cpi_surprise",
  "capture": {
    "captured_at": "2026-05-22T13:26:38Z",
    "capture_method": "synthetic_v1",
    "frozen_today": "2026-05-22",
    "raw_rows_count": 48,
    "raw_rows_sha256": "abc123..."
  },
  "input": {
    "params": {"country": "US", "lookback_releases": 24},
    "frozen_today": "2026-05-22",
    "raw_rows": [
      {
        "event_id": 5000,
        "event_type": "cpi_yoy",
        "event_category": "economic_release",
        "country": "US",
        "currency": "USD",
        "release_date": "2024-06-15",
        "release_time": null,
        "period": "May 2024",
        "actual": 3.2,
        "consensus_median": 3.1,
        "consensus_high": 3.2,
        "consensus_low": 3.0,
        "prior": 3.15,
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

## Generating fixtures (live-DB capture)

```bash
docker-compose up -d tsdb
export DB_HOST=localhost DB_PORT=5433 DB_USER=quantuser \
       DB_PASSWORD=... DB_NAME=macrodata
python tests/fixtures/cpi_surprise_v1/_capture.py
```

The capture queries `macro_data.event_calendar` for each canonical
slot using the same fetcher the primitive uses, records the raw
rows, then runs the primitive to record the expected output.

## Running the parity test

```bash
pytest tests/test_cpi_surprise_parity.py -v
```
