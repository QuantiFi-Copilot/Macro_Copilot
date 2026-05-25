# `cross_country_breakeven_spread_simple_v1` parity fixtures

Snapshots of the **real production output** of
`calculate_cross_country_breakeven_spread_simple` captured against
the live TimescaleDB.  PR15 backfill (Phase 3 Step 9).

Companion test:
`tests/test_cross_country_breakeven_spread_simple_parity.py`.

## Compose-primitive seam topology

`calculate_cross_country_breakeven_spread_simple` composes TWO
inner `calculate_breakeven_inflation_simple` calls — one per
country leg — each constrained to its own
`(nominal_curve_family, linker_curve_family)` pair at the SAME
shared `tenor`.  Each inner call does 2 country lookups + 2
fetches, so per scenario the live tool issues 4 country lookups
(4 unique keys) and 4 fetches (4 unique keys).

The parity test patches three seams, ALL on the INNER spot
primitive's module:

1. `breakeven_inflation_simple.compute.fetch_single_tenor`
2. `breakeven_inflation_simple.compute._fetch_curve_family_country_currency`
3. `breakeven_inflation_simple.compute.date`

The cross-country invariant lives in the input validator and the
per-leg same-country invariant lives in the inner spot primitive —
neither requires extra patching at the compose layer.

## Generating fixtures

```bash
docker-compose up -d tsdb
export DB_HOST=localhost DB_PORT=5433 DB_USER=quantuser \
       DB_PASSWORD=... DB_NAME=macrodata
python tests/fixtures/cross_country_breakeven_spread_simple_v1/_capture.py
```

| Fixture | Country pair | Rationale |
|---|---|---|
| `us_vs_uk_10y_365d.json` | US (UST/USD_TIPS) vs UK (UK_GILT/GBP_LINKER) at 10Y | Canonical USD-GBP cross-country breakeven; pins the CPI-U vs RPI index-family mismatch caveat. |
| `us_vs_fr_5y_365d.json` | US vs France (FR_OAT/EUR_FR_LINKER) at 5Y | USD-EUR pair — exercises the EUR linker leg with HICP-referenced data. |
| `uk_vs_fr_10y_730d.json` | UK vs France at 10Y, 2Y lookback | Non-USD pair with longer lookback; exercises inner-join discipline when the two country pairs have different holiday patterns. |

## Running the parity test

```bash
pytest tests/test_cross_country_breakeven_spread_simple_parity.py -v
```

Regeneration policy mirrors
`tests/fixtures/breakeven_inflation_simple_v1/README.md`.
