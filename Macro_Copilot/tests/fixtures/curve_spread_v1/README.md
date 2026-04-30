# `curve_spread_v1` parity fixtures

Snapshots of the **real production output** of `calculate_curve_spread`
captured against the live TimescaleDB.  The companion test at
`tests/test_curve_spread_parity.py` reloads each fixture, mocks the DB
fetch (replaying captured `raw_rows`), freezes `date.today()` to the
recorded value, runs the tool, and asserts the output matches the
recorded `expected_output` byte-for-byte (modulo 1e-9 float tolerance).

## Purpose

Commit 0 of the tool-config refactor pilot.  The pilot will move
`Z_SCORE_WINDOW`, `ffill_limit`, and other conventions out of Python
constants and into a per-tool YAML config, plus parameterise
`shared/analytics/spreads.py` to accept those values as kwargs.

These fixtures lock in the *current production behaviour* — i.e. the
exact output the tool produces on real Bloomberg-shaped data — so any
silent drift introduced by the refactor (a different rounding path, a
subtly altered z-score formula, an off-by-one in the buffer window)
fails the parity test loudly.

The captured baseline includes real-data quirks that synthetic inputs
won't reproduce: NaN field values, market-specific holiday patterns,
occasional duplicate rows, idiosyncratic gaps.  Real captures protect
the refactor against drift on those quirks too.

## Fixture format

Each `*.json` file is a self-contained fixture:

```json
{
  "fixture_name": "ust_2s10s_365d",
  "tool_module": "rates_agent.sovereign_bonds.tools.curve_spread",
  "tool_function": "calculate_curve_spread",
  "capture": {
    "captured_at": "2026-04-30T15:23:01Z",
    "database_name": "macrodata",
    "as_of_date": "2026-04-29",
    "raw_rows_count": 740,
    "raw_rows_sha256": "abc123..."
  },
  "input": {
    "params": {curve_family, short_tenor, long_tenor, lookback_days, field_name},
    "frozen_today": "YYYY-MM-DD",
    "raw_rows": [{trade_date, tenor, field_value}, ...]
  },
  "expected_output": {current_metrics: {...}, time_series: [...]}
}
```

- **`capture`** is provenance metadata.  The parity test ignores it for
  the deep comparison but DOES verify that `raw_rows_sha256` matches a
  fresh recompute — guards against fixture tampering.
- **`input.frozen_today`** is the wall-clock date when the capture ran;
  the parity test patches `date.today()` to this value during replay.
- **`input.raw_rows`** is the long-format DataFrame the tool's SQL
  fetcher returned (`fetch_tenor_pair`).  The parity test feeds these
  back into the tool through a mocked fetcher.
- **`expected_output`** is the full tool output captured from the real
  DB run.

## Generating fixtures (live-DB capture)

The `_capture.py` script must be run against a live TimescaleDB with
the relevant Bloomberg sovereign yield data loaded.  Defaults match
the project's `docker-compose.yml`:

```bash
# 1. Make sure TimescaleDB is up and the sovereign benchmark data is loaded.
docker-compose up -d tsdb
# (data ingestion via the usual ingestion/ pipeline must have run)

# 2. Set DB env vars if non-default, then capture.
export DB_HOST=localhost DB_PORT=5433 DB_USER=quantuser \
       DB_PASSWORD=... DB_NAME=macrodata
python tests/fixtures/curve_spread_v1/_capture.py
```

Three fixtures are written: `ust_2s10s_365d.json`,
`bund_5s30s_90d.json`, `btp_2s10s_730d.json`.  Each pulls real history
from the DB across the lookback window plus a 252-day z-score buffer.

After capture, **inspect the diffs in git** before committing:

- `capture.captured_at` and `capture.as_of_date` will change.
- `input.raw_rows` will change if the DB has new market data since the
  last capture.
- `expected_output.current_metrics` and `expected_output.time_series`
  will change to match the new market state.

These changes are expected; they reflect that markets moved.  What you
should NOT see is unrelated structural drift in `expected_output`
(e.g. fields appearing or disappearing) — that would mean the tool's
schema has shifted.

## When to regenerate

Regenerate ONLY when:

1. **The DB has new market data and you want to refresh the baseline.**
   This should be a deliberate decision — once a fixture is captured,
   subsequent commits in the pilot use it as the immovable reference.
   Refreshing mid-pilot defeats the purpose.
2. **You made a deliberate methodology change** (changed the rounding
   precision, switched sample-std to population-std, etc.) that you
   want the tool to start producing.

In case 2:

1. Make the methodology change in code.
2. Run the capture script to refresh fixtures.
3. Inspect the diff carefully — confirm the change is what you intended
   and nothing else moved.
4. Commit the regenerated fixtures alongside the code change in the
   *same* PR, with the rationale in the commit message.

NEVER regenerate fixtures to "make the test pass" after an unintended
change.  That defeats the entire purpose of having a parity test.

## Why the existing test suite isn't enough

The pre-existing `tests/test_*_direct.py` files are CLI smoke-test
scripts (each has an `if __name__ == "__main__"` block, none have
`def test_*` or `@pytest`).  They are not picked up by pytest and
they perform no assertion-based regression checks.  These parity
fixtures + `tests/test_curve_spread_parity.py` are the project's first
real assertion-based regression test.

## Running the parity test

```bash
pytest tests/test_curve_spread_parity.py -v
```

If no fixtures are present (e.g. fresh checkout where the DB hasn't
been captured), the test skips with an explanatory message rather than
failing.  All three test cases must pass for any commit that touches
`calculate_curve_spread` or its primitives.
