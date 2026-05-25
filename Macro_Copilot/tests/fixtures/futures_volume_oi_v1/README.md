# `futures_volume_oi_v1` parity fixtures

Snapshots of the **real production output** of
`calculate_futures_volume_oi` captured against the live TimescaleDB.
The companion test at `tests/test_futures_volume_oi_parity.py` reloads
each fixture, mocks the DB fetchers (replaying captured `raw_volume_rows`
+ `raw_oi_rows` + `reference`), freezes `date.today()` to the recorded
value, runs the tool, and asserts the output matches the recorded
`expected_output` byte-for-byte (modulo 1e-9 float tolerance).

## Purpose

PR15 binds **new primitives from day-one** with a parity-fixture
baseline. `futures_volume_oi` is the second primitive in the new
`bond_futures` domain (ADR 0013), so it ships with parity coverage
rather than being grandfathered debt.

The fixture locks in the *current production behaviour* — the exact
output the tool produces on real Bloomberg-shaped data — so any silent
drift introduced by a refactor (a different rounding path, a subtly
altered z-score formula, an off-by-one in the rolling-generic fetch
or the buffer window, a regression in the volume-OI intersection
alignment) fails the parity test loudly.

The captured baseline includes real-data quirks that synthetic inputs
won't reproduce: NaN field values, market-specific holiday patterns,
days where volume or OI report independently (one missing), occasional
duplicate rows. Real captures protect the refactor against drift on
those quirks too.

## Fixture format

Each `*.json` file is a self-contained fixture:

```json
{
  "fixture_name": "ty1_ust_fut_10y_365d",
  "tool_module": "rates_agent.bond_futures.tools.futures_volume_oi",
  "tool_function": "calculate_futures_volume_oi",
  "capture": {
    "captured_at": "2026-05-23T15:23:01Z",
    "database_name": "macrodata",
    "as_of_date": "2026-04-08",
    "raw_volume_rows_count": 740,
    "raw_oi_rows_count": 740,
    "raw_rows_sha256": "abc123..."
  },
  "input": {
    "params": {curve_family, contract_code, lookback_days},
    "frozen_today": "YYYY-MM-DD",
    "raw_volume_rows": [{trade_date, field_value}, ...],
    "raw_oi_rows": [{trade_date, field_value}, ...],
    "reference": {contract_code, curve_family, tenor, expiry_date,
                  security_name, quote_units, contract_size}
  },
  "expected_output": {current_metrics, time_series, methodology_disclosure}
}
```

- **`capture`** is provenance metadata. The parity test ignores it for
  the deep comparison but DOES verify that `raw_rows_sha256` matches a
  fresh recompute over the canonicalised concatenation of
  `raw_volume_rows` + `raw_oi_rows` — guards against fixture tampering.
- **`input.frozen_today`** is the wall-clock date when the capture ran;
  the parity test patches `date.today()` to this value during replay.
- **`input.raw_volume_rows`** + **`input.raw_oi_rows`** are the long-
  format DataFrames the tool's `fetch_rolling_generic_series` fetcher
  returns for `PX_VOLUME` and `OPEN_INT` respectively. The parity test
  feeds these back into the tool through a mocked fetcher with a side-
  effect that routes by `field_name`.
- **`input.reference`** is the dict the tool's
  `fetch_rolling_generic_reference` fetcher returns — also mocked in
  the parity test.
- **`expected_output`** is the full tool output captured from the real
  DB run.

## Generating fixtures (live-DB capture)

The `_capture.py` script must be run against a live TimescaleDB with
the relevant Bloomberg bond-futures rolling-generic data loaded.
Defaults match the project's `docker-compose.yml`:

```bash
# 1. Make sure TimescaleDB is up and the bond-futures rolling-generic
#    universe is ingested (TY1, RX1, ... — with both PX_VOLUME and
#    OPEN_INT).
docker compose up -d tsdb
# (data ingestion via the usual ingestion/ pipeline must have run)

# 2. Set DB env vars if non-default, then capture.
export DB_HOST=localhost DB_PORT=5433 DB_USER=quantuser \
       DB_PASSWORD=... DB_NAME=macrodata
python tests/fixtures/futures_volume_oi_v1/_capture.py
```

The default capture set is one fixture (`ty1_ust_fut_10y_365d`), which
pins the 10Y UST bellwether. Add more cases to `_CASES` in
`_capture.py` if multiple contracts should be parity-pinned (e.g. RX1
for Bund 10Y, JB1 for JGB 10Y).

After capture, **inspect the diffs in git** before committing:

- `capture.captured_at` and `capture.as_of_date` will change.
- `input.raw_volume_rows` / `input.raw_oi_rows` will change if the DB
  has new market data since the last capture.
- `expected_output.current_metrics` and `expected_output.time_series`
  will change to match the new market state.

These changes are expected; they reflect that markets moved. What you
should NOT see is unrelated structural drift in `expected_output`
(e.g. fields appearing or disappearing) — that would mean the tool's
schema has shifted.

## When to regenerate

Regenerate ONLY when:

1. **The DB has new market data and you want to refresh the baseline.**
   This should be a deliberate decision — once a fixture is captured,
   subsequent commits use it as the immovable reference. Refreshing
   casually defeats the purpose.
2. **You made a deliberate methodology change** (e.g. changed
   `oi_z_score_window_days`, switched sample-std to population-std)
   that you want the tool to start producing.

In case 2:

1. Make the methodology change in code.
2. Run the capture script to refresh fixtures.
3. Inspect the diff carefully — confirm the change is what you
   intended and nothing else moved.
4. Commit the regenerated fixtures alongside the code change in the
   *same* PR, with the rationale in the commit message.

NEVER regenerate fixtures to "make the test pass" after an unintended
change. That defeats the entire purpose of having a parity test.

## Running the parity test

```bash
pytest tests/test_futures_volume_oi_parity.py -v
```

If no fixtures are present (e.g. fresh checkout where the DB hasn't
been captured), the test skips with an explanatory message rather than
failing.
