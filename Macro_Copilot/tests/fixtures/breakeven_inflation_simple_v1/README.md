# `breakeven_inflation_simple_v1` parity fixtures

Snapshots of the **real production output** of
`calculate_breakeven_inflation_simple` captured against the live
TimescaleDB.  The companion test at
`tests/test_breakeven_inflation_simple_parity.py` reloads each
fixture, mocks the DB seams (`fetch_single_tenor`,
`_fetch_curve_family_country_currency`, `date`), runs the tool, and
asserts the output matches the recorded `expected_output`
byte-for-byte (modulo 1e-9 float tolerance).

## Purpose — PR15 backfill

Phase 3 Step 9 of the primitive-expansion plan: the
`breakeven_inflation_simple` primitive shipped before PR15 (parity-
fixture discipline) was load-bearing.  Without a parity fixture, any
refactor that touches `compute_spread_bps`, `pivot_and_align_tenors`,
`_enforce_same_country_invariant`, the bundled `config.yaml`, or the
two `fetch_single_tenor` call sites could silently change desk
output and pass unit tests.

This primitive is the **base** that four compose primitives in this
stage build on (`breakeven_curve_spread`, `breakeven_butterfly`,
`forward_breakeven_simple`, `cross_country_breakeven_spread_simple`)
— locking parity here also locks the contract those compose
primitives inherit from their inner spot calls.

## Fixture format

See `tests/fixtures/curve_spread_v1/README.md` for the canonical
format.  Two structural differences:

- `input.country_currency` is a dict keyed by
  `"<curve_family>__<instrument_type>"` recording the
  `(country, currency)` row that
  `_fetch_curve_family_country_currency` would return for each
  curve_family/instrument_type pair the same-country invariant guard
  consults (nominal + linker, two entries per scenario).  The parity
  test's mock side_effect routes lookups by these keys.
- `input.raw_rows` is a dict keyed by
  `"<curve_family>__<tenor>__<field_name>__<instrument_type>"` whose
  values are lists of `{trade_date, field_value}` rows — one entry
  per fetcher call (linker + nominal, two entries per scenario).
- `capture.raw_rows_sha256` covers BOTH the `country_currency` dict
  AND the `raw_rows` dict so any hand edit to either provenance
  stream trips the tamper guard.

## Generating fixtures (live-DB capture)

```bash
docker-compose up -d tsdb
export DB_HOST=localhost DB_PORT=5433 DB_USER=quantuser \
       DB_PASSWORD=... DB_NAME=macrodata
python tests/fixtures/breakeven_inflation_simple_v1/_capture.py
```

Three fixtures are written:

| Fixture | Scenario | (country, currency) | Rationale |
|---|---|---|---|
| `ust_usd_tips_10y_365d.json` | UST + USD_TIPS 10Y over 1Y | (US, USD) | Canonical US 10Y breakeven — most-watched bond-implied inflation compensation read globally. |
| `uk_gilt_gbp_linker_10y_365d.json` | UK_GILT + GBP_LINKER 10Y over 1Y | (UK, GBP) | Different country + RPI-referenced linker; exercises the same-country guard with a non-USD pair. |
| `fr_oat_eur_fr_linker_5y_365d.json` | FR_OAT + EUR_FR_LINKER 5Y over 1Y | (France, EUR) | EUR-zone pair at 5Y — verifies the (country, currency) guard accepts a same-country EUR pair (and would refuse DE_BUND + EUR_FR_LINKER, which is the load-bearing EUR-zone case the guard exists to catch). |

## When to regenerate

Regenerate ONLY when:

1. **The DB has new market data and you want to refresh the
   baseline.**  Refreshing casually defeats the purpose.
2. **You made a deliberate methodology change** (changed
   `bps_round_decimals`, switched sample-std to population-std,
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
pytest tests/test_breakeven_inflation_simple_parity.py -v
```

If no fixtures are present (e.g. fresh checkout where the DB hasn't
been captured), the test skips with an explanatory message.  All
three test cases must pass for any commit that touches
`calculate_breakeven_inflation_simple`, the shared analytics
primitives it composes (`compute_spread_bps`,
`pivot_and_align_tenors`, `rolling_zscore`, `period_changes`,
`trailing_high_low_percentile`), or the bundled `config.yaml`.
