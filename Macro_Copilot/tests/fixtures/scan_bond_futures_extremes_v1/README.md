# `scan_bond_futures_extremes_v1` parity fixtures

Snapshots of the **real production output** of
`calculate_scan_bond_futures_extremes` captured against the live
TimescaleDB. The companion test at
`tests/test_scan_bond_futures_extremes_parity.py` reloads each
fixture, mocks the DB fetcher (replaying captured `raw_price_rows`,
`raw_volume_rows`, and `raw_oi_rows`), freezes `date.today()` to the
recorded value, runs the tool, and asserts the output matches the
recorded `expected_output` byte-for-byte (modulo 1e-9 float
tolerance).

## Purpose

PR15 binds **new primitives from day-one** with a parity-fixture
baseline. `scan_bond_futures_extremes` is the THIRD primitive in the
new `bond_futures` domain (ADR 0011) — the morning-extremes universe
sweep — so it ships with parity coverage rather than being
grandfathered debt.

The fixture locks in the *current production behaviour* — the exact
output the tool produces on real Bloomberg-shaped universe data — so
any silent drift introduced by a refactor (a subtly altered rolling-
z-score path, a regression in the per-stem intersection alignment, a
change in the four-metric ordering, a different ranking tie-break, an
off-by-one in the universe fetcher) fails the parity test loudly.

The captured baseline includes real-data quirks that synthetic inputs
won't reproduce: per-stem holiday calendars (UST trades through
Bund-only holidays and vice versa), days where one stem's volume or
OI reports independently, NaN field values from the upstream feed,
the rolling-generic underlying contract rotating as the front rolls.

## Fixture format

Each `*.json` file is a self-contained fixture:

```json
{
  "fixture_name": "full_universe_top3_zfloor",
  "tool_module": "rates_agent.bond_futures.tools.scan_bond_futures_extremes",
  "tool_function": "calculate_scan_bond_futures_extremes",
  "capture": {
    "captured_at": "2026-05-23T15:23:01Z",
    "database_name": "macrodata",
    "raw_price_rows_count": 13680,
    "raw_volume_rows_count": 13620,
    "raw_oi_rows_count": 13650,
    "raw_rows_sha256": "abc123...",
    "resolved_curve_families": [
      "UST_FUT", "DE_FUT", "UK_FUT", "JP_FUT", "FR_FUT",
      "IT_FUT", "ES_FUT", "CA_FUT", "AU_FUT"
    ]
  },
  "input": {
    "params": {curve_families, top_n, min_abs_z_score, as_of_date},
    "frozen_today": "YYYY-MM-DD",
    "raw_price_rows": [{trade_date, curve_family, contract_code, tenor, field_value}, ...],
    "raw_volume_rows": [...],
    "raw_oi_rows": [...]
  },
  "expected_output": {scan_summary, results, methodology_disclosure}
}
```

- **`capture`** is provenance metadata. The parity test ignores it
  for the deep comparison but DOES verify that `raw_rows_sha256`
  matches a fresh recompute over the canonicalised concatenation of
  the three raw-row lists — guards against fixture tampering.
- **`input.params.as_of_date`** is the pinned anchor date the scan
  uses (round-2 mandatory-fix #2 — the previous build anchored on
  `date.today()`, which made the captured fixture drift across
  regeneration runs). The `_capture.py` script writes the pinned
  date into the fixture so re-captures reproduce the same anchor
  even on a different wall-clock day.
- **`input.frozen_today`** is the anchor date the capture ran
  against (tracks `as_of_date` when supplied; falls back to the
  wall-clock day otherwise); the parity test patches `date.today()`
  to this value during replay so a hypothetical no-`as_of_date`
  fixture can still resolve compute's default-anchor branch
  deterministically.
- **`input.raw_price_rows`** / **`raw_volume_rows`** / **`raw_oi_rows`**
  are the long-format DataFrames the tool's
  `fetch_rolling_generic_universe_series` fetcher returns for
  `PX_LAST`, `PX_VOLUME`, and `OPEN_INT` respectively. The parity
  test feeds these back into the tool through a mocked fetcher with
  a side-effect that routes by `field_name`.
- **`expected_output`** is the full tool output captured from the
  real DB run.

## Generating fixtures (live-DB capture)

The `_capture.py` script must be run against a live TimescaleDB with
the relevant Bloomberg bond-futures rolling-generic data loaded (all
three fields: PX_LAST, PX_VOLUME, OPEN_INT — across the bond_futures
universe per ADR 0011 V1). Defaults match the project's
`docker-compose.yml`:

```bash
# 1. Make sure TimescaleDB is up and the bond-futures rolling-generic
#    universe is ingested with all three fields.
docker compose up -d tsdb
# (data ingestion via the usual ingestion/ pipeline must have run)

# 2. Set DB env vars if non-default, then capture.
export DB_HOST=localhost DB_PORT=5433 DB_USER=quantuser \
       DB_PASSWORD=... DB_NAME=macrodata
python tests/fixtures/scan_bond_futures_extremes_v1/_capture.py
```

The default capture set is one fixture
(`full_universe_top3_zfloor`) which pins the full V1 universe scan
with `top_n=3` and `min_abs_z_score=0.0` — small enough that the
fixture is reviewable but large enough to exercise every metric
block. Add more cases to `_CASES` in `_capture.py` if multiple
configurations should be parity-pinned (e.g. a UST-only subset, a
high-threshold variant).

After capture, **inspect the diffs in git** before committing:

- `capture.captured_at` will change.
- `input.raw_*_rows` will change if the DB has new market data since
  the last capture.
- `expected_output.scan_summary` and `expected_output.results` will
  change to match the new market state.

These changes are expected; they reflect that markets moved. What you
should NOT see is unrelated structural drift in `expected_output`
(e.g. metric blocks appearing or disappearing in the ordering, fields
appearing or disappearing on rows) — that would mean the tool's
schema has shifted.

## When to regenerate

Regenerate ONLY when:

1. **The DB has new market data and you want to refresh the
   baseline.** This should be a deliberate decision — once a fixture
   is captured, subsequent commits use it as the immovable reference.
   Refreshing casually defeats the purpose.
2. **You made a deliberate methodology change** (e.g. changed
   `z_score_window_days`, switched sample-std to population-std,
   added a fifth metric per a future ADR) that you want the tool to
   start producing.

In case 2:

1. Make the methodology change in code.
2. Run the capture script to refresh fixtures.
3. Inspect the diff carefully — confirm the change is what you
   intended and nothing else moved.
4. Commit the regenerated fixtures alongside the code change in the
   *same* PR, with the rationale in the commit message.

NEVER regenerate fixtures to "make the test pass" after an
unintended change. That defeats the entire purpose of having a
parity test.

## Running the parity test

```bash
pytest tests/test_scan_bond_futures_extremes_parity.py -v
```

If no fixtures are present (e.g. fresh checkout where the DB hasn't
been captured), the test skips with an explanatory message rather
than failing.
