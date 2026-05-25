# `inflation_swap_rate_level_v1` parity fixtures

Snapshots of the **real production output** of
`calculate_inflation_swap_rate_level` captured against the live
TimescaleDB.  The companion test at
`tests/test_inflation_swap_rate_level_parity.py` reloads each fixture,
mocks the DB fetch (replaying captured `raw_rows`), freezes
`date.today()` to the recorded value, runs the tool, and asserts the
output matches the recorded `expected_output` byte-for-byte (modulo
1e-9 float tolerance).

## Purpose — PR15 backfill

Phase 3 Step 9 of the primitive-expansion plan: the
`inflation_swap_rate_level` primitive shipped before PR15
(parity-fixture discipline) was load-bearing.  Without a parity
fixture, any refactor that touches `compute_level_metrics`,
`clean_single_series`, the local `fetch_zcis_single_pillar` SQL,
`_resolve_reference_metadata`, the ZCIS config.yaml, or the
boundary-rounding path could silently change desk output and pass unit
tests.

These fixtures lock in the *current production behaviour* — the exact
output the tool produces on real Bloomberg-shaped ZCIS data — so any
silent drift fails the parity test loudly.

The captured baselines include real-data quirks that synthetic inputs
won't reproduce: NaN field values, per-currency ZCIS holiday patterns,
negative EUR ZCIS rates across parts of the post-2020 history, and the
three distinct `(inflation_index_family, index_lag, interpolation)`
tuples each curve family carries on the wire.

## Fixture format

See `tests/fixtures/curve_spread_v1/README.md` for the canonical format
documentation.  Two structural differences for this primitive's
fixtures:

- `input.raw_rows` carries eight columns per row, not three:
  `{trade_date, field_value, vendor_ticker, pricing_type,
  inflation_index_family, index_lag, interpolation,
  underlying_index}`.  All eight columns are required by
  `_resolve_reference_metadata` (vendor_ticker enforces the
  single-ticker guarantee; the three convention strings drive the
  wire-surface fields on `current_metrics`; `underlying_index` is
  surfaced for traceability).  Dropping any column would make the
  replay diverge from the live capture.
- `expected_output.current_metrics` carries the full
  `InflationSwapRateLevelCurrentMetrics` payload including the
  reference-metadata fields and `methodology_label` threaded from the
  YAML's `methodology.what_it_does`.

## Generating fixtures (live-DB capture)

The `_capture.py` script must be run against a live TimescaleDB with
the inflation_swaps universe loaded.  Defaults match the project's
`docker-compose.yml`:

```bash
docker-compose up -d tsdb
export DB_HOST=localhost DB_PORT=5433 DB_USER=quantuser \
       DB_PASSWORD=... DB_NAME=macrodata
python tests/fixtures/inflation_swap_rate_level_v1/_capture.py
```

Three fixtures are written:

| Fixture | Scenario | Reference convention | Rationale |
|---|---|---|---|
| `usd_zcis_5y_365d.json` | USD_ZCIS 5Y over 1Y | `US_CPI_URBAN` / `3M` / `Daily` | Canonical US 5Y CPI-U ZCIS — the most-quoted USD inflation-swap pillar. |
| `eur_zcis_10y_730d.json` | EUR_ZCIS 10Y over 2Y | `EU_HICP` / `3M` / `Monthly` | Different index family + interpolation, longer tenor, longer lookback so the displayed slice is non-trivially larger than the z-score window. |
| `gbp_zcis_5y_365d.json` | GBP_ZCIS 5Y over 1Y | `UK_RPI` / `2M` / `Monthly` | Third index family with a distinct `2M` index lag — the only family with a 2-month lag in the ingested universe. |

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
pytest tests/test_inflation_swap_rate_level_parity.py -v
```

If no fixtures are present (e.g. fresh checkout where the DB hasn't
been captured), the test skips with an explanatory message rather than
failing.  All three test cases must pass for any commit that touches
`calculate_inflation_swap_rate_level`, `compute_level_metrics`,
`clean_single_series`, `_resolve_reference_metadata`, or the ZCIS
`config.yaml`.
