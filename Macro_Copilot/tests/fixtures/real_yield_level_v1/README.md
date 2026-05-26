# `real_yield_level_v1` parity fixtures

Snapshots of the **real production output** of `get_real_yield_level`
captured against the live TimescaleDB.  The companion test at
`tests/test_real_yield_level_parity.py` reloads each fixture, mocks the
DB fetch (replaying captured `raw_rows`), freezes `date.today()` to the
recorded value, runs the tool, and asserts the output matches the
recorded `expected_output` byte-for-byte (modulo 1e-9 float tolerance).

## Purpose — PR15 backfill

Phase 3 Step 9 of the primitive-expansion plan: the linker
`real_yield_level` primitive shipped before PR15 (parity-fixture
discipline) was load-bearing.  Without a parity fixture, any refactor
that touches `compute_level_metrics`, `clean_single_series`, the linker
config.yaml, or the boundary-rounding path could silently change the
desk output and pass unit tests.

These fixtures lock in the *current production behaviour* — the exact
output the tool produces on real Bloomberg-shaped linker data — so any
silent drift fails the parity test loudly.

The captured baselines include real-data quirks that synthetic inputs
won't reproduce: NaN field values, market-specific holiday patterns,
occasional duplicate rows, post-2020 negative real yields.  Real
captures protect refactors against drift on those quirks too.

## Fixture format

See `tests/fixtures/curve_spread_v1/README.md` for the canonical
format documentation.  The only structural difference for this
primitive's fixtures:

- `input.raw_rows` carries only `{trade_date, field_value}` per row.
  `fetch_single_tenor` (the linker-side fetcher this primitive uses
  with `instrument_type='inflation_linker'`) returns only those two
  columns — no `tenor` column, unlike `curve_spread`'s
  `fetch_tenor_pair`.
- `expected_output` carries `current_metrics` (snapshot dict) and
  `time_series` (canonical closed-enum `TimeSeries` dict with
  `series_name`, `units`, `description`, `rows`).

## Generating fixtures (live-DB capture)

The `_capture.py` script must be run against a live TimescaleDB with
the inflation-indexed-bonds universe loaded.  Defaults match the
project's `docker-compose.yml`:

```bash
docker-compose up -d tsdb
export DB_HOST=localhost DB_PORT=5433 DB_USER=quantuser \
       DB_PASSWORD=... DB_NAME=macrodata
python tests/fixtures/real_yield_level_v1/_capture.py
```

Three fixtures are written:

| Fixture | Scenario | Rationale |
|---|---|---|
| `usd_tips_10y_365d.json` | USD_TIPS 10Y over 1Y | Canonical desk read: US 10Y TIPS real yield is the most-watched linker pillar globally; pins the math against `US_CPI_URBAN`-referenced data. |
| `gbp_linker_10y_730d.json` | GBP_LINKER 10Y over 2Y | Different currency + index family (`UK_RPI`) and a longer lookback window so the displayed slice is non-trivially larger than the snapshot's z-score window. |
| `eur_fr_linker_5y_365d.json` | EUR_FR_LINKER 5Y over 1Y | Third currency (`EUR`) + index family (`EU_HICP`); 5Y tenor exercises a shorter point on the curve than the 10Y cases. |

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
   subsequent commits use it as the immovable reference.  Refreshing
   casually defeats the purpose.
2. **You made a deliberate methodology change** (changed
   `yield_round_decimals`, switched sample-std to population-std,
   bumped `z_score_window_days`, etc.) that you want the tool to start
   producing.

In case 2:

1. Make the methodology change in code or `config.yaml`.
2. Run the capture script to refresh fixtures.
3. Inspect the diff carefully — confirm the change is what you intended
   and nothing else moved.
4. Commit the regenerated fixtures alongside the code/YAML change in
   the *same* PR, with the rationale in the commit message.

NEVER regenerate fixtures to "make the test pass" after an unintended
change.  That defeats the entire purpose of having a parity test.

## Running the parity test

```bash
pytest tests/test_real_yield_level_parity.py -v
```

If no fixtures are present (e.g. fresh checkout where the DB hasn't
been captured), the test skips with an explanatory message rather than
failing.  All three test cases must pass for any commit that touches
`get_real_yield_level`, `compute_level_metrics`, `clean_single_series`,
or the linker `config.yaml`.
