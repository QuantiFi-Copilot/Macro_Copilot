# `real_yield_curve_spread_v1` parity fixtures

Snapshots of the **real production output** of
`calculate_real_yield_curve_spread` captured against the live
TimescaleDB.  The companion test at
`tests/test_real_yield_curve_spread_parity.py` reloads each
fixture, mocks the inner DB seams, runs the compose primitive, and
asserts the output matches the recorded `expected_output`
byte-for-byte (modulo 1e-9 float tolerance).

## Purpose — PR15 backfill

Phase 3 Step 9 of the primitive-expansion plan.  This is a
**compose primitive** that internally calls `get_real_yield_level`
twice (one per endpoint tenor) and runs one identity-guard DB read
in its own `_fetch_curve_family_country_currency` helper.  Without a
parity fixture, any refactor to the inner level primitive, the
compose's identity-guard helper, the bundled `config.yaml`, or any
shared analytics primitive (`rolling_zscore`, `period_changes`,
`trailing_high_low_percentile`) could silently change desk output
and pass unit tests.

## Fixture format

See `tests/fixtures/curve_spread_v1/README.md` for the canonical
shape.  Compose-primitive specifics:

- `input.country_currency` records the single identity-guard row
  the compose primitive's `_fetch_curve_family_country_currency`
  returns for `(curve_family, instrument_type='inflation_linker')`.
- `input.raw_rows` records the long-format
  `[trade_date, field_value]` rows the INNER level primitive's
  `fetch_single_tenor` returns for EACH of the two endpoint tenors
  (`short_tenor`, `long_tenor`).
- `capture.raw_rows_sha256` hashes BOTH provenance streams together
  (combined `country_currency` + `raw_rows`).
- The capture script computes the production fetch window
  explicitly: `today - (lookback_days + outer_buffer +
  inner_buffer)` where `outer_buffer` matches the compose
  primitive's `max(z_window, trailing_window) * buffer_multiplier`
  and `inner_buffer` matches the level primitive's `z_window *
  buffer_multiplier`.  Mirrors the production fetch window exactly
  so the captured rows match what the live tool reads.

## Generating fixtures (live-DB capture)

```bash
docker-compose up -d tsdb
export DB_HOST=localhost DB_PORT=5433 DB_USER=quantuser \
       DB_PASSWORD=... DB_NAME=macrodata
python tests/fixtures/real_yield_curve_spread_v1/_capture.py
```

Three fixtures are written:

| Fixture | Scenario | Rationale |
|---|---|---|
| `usd_tips_5s10s_365d.json` | USD_TIPS 5s10s over 1Y | Canonical US linker curve-spread read; pins the math against `US/USD` linker rows. |
| `gbp_linker_2s10s_730d.json` | GBP_LINKER 2s10s over 2Y | Different currency + index family (`UK/GBP`, RPI-referenced), longer lookback so the displayed slice is non-trivially larger than the z-score window. |
| `usd_tips_10s30s_365d.json` | USD_TIPS 10s30s over 1Y | Longer-end USD pair exercising the same curve at a different shape — guards against drift that affects 30Y data differently from 5Y/10Y. |

## When to regenerate

Regenerate ONLY when:

1. **The DB has new market data and you want to refresh the
   baseline.**  Refreshing casually defeats the purpose.
2. **You made a deliberate methodology change** (changed
   `yield_round_decimals`, switched sample-std to population-std,
   bumped `z_score_window_days`, etc.) that you want the tool to
   start producing.

In case 2: make the change → run capture → inspect the diff
carefully → commit fixtures alongside the code/YAML change in the
*same* PR.

NEVER regenerate fixtures to "make the test pass" after an
unintended change.  That defeats the entire purpose of having a
parity test.

## Running the parity test

```bash
pytest tests/test_real_yield_curve_spread_parity.py -v
```

If no fixtures are present, the test skips with an explanatory
message.  All three test cases must pass for any commit that
touches `calculate_real_yield_curve_spread`,
`get_real_yield_level`, `compute_level_metrics`, the shared
analytics spreads primitives, or either bundled `config.yaml`.
