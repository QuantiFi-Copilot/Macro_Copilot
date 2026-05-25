# `cross_market_inflation_swap_spread_v1` parity fixtures

Snapshots of the **real production output** of
`calculate_cross_market_inflation_swap_spread` captured against
the live TimescaleDB.  PR15 backfill (Phase 3 Step 9).

Companion test:
`tests/test_cross_market_inflation_swap_spread_parity.py`.

## Compose-primitive seam topology

`calculate_cross_market_inflation_swap_spread` composes TWO inner
`calculate_inflation_swap_rate_level` calls for DIFFERENT ZCIS
`curve_family` values at the SAME shared tenor.  Patches the same
two seams as the sibling inflation_swap compose primitives:

1. `inflation_swap_rate_level.compute.fetch_zcis_single_pillar`
2. `inflation_swap_rate_level.compute.date`

There is no same-curve invariant here (legs are EXPECTED to differ
on `inflation_index_family` / `index_lag` / `interpolation`); the
per-leg reference metadata flows through the inner primitive's
8-column rows and lands on `current_metrics.leg_a_*` /
`current_metrics.leg_b_*` automatically.

## Generating fixtures

```bash
docker-compose up -d tsdb
export DB_HOST=localhost DB_PORT=5433 DB_USER=quantuser \
       DB_PASSWORD=... DB_NAME=macrodata
python tests/fixtures/cross_market_inflation_swap_spread_v1/_capture.py
```

| Fixture | Leg A vs Leg B | Rationale |
|---|---|---|
| `usd_zcis_eur_zcis_5y_365d.json` | USD_ZCIS vs EUR_ZCIS at 5Y | Canonical CPI-U vs HICP differential at the 5Y pillar — pins the index-family-mismatch caveat. |
| `usd_zcis_gbp_zcis_10y_365d.json` | USD_ZCIS vs GBP_ZCIS at 10Y | CPI-U vs RPI at 10Y; exercises a different tenor + index-lag combination (3M vs 2M). |
| `eur_zcis_gbp_zcis_5y_730d.json` | EUR_ZCIS vs GBP_ZCIS at 5Y, 2Y lookback | HICP vs RPI with the longer lookback; both legs interpolate Monthly which is the load-bearing pair for the no-same-curve-invariant path. |

## Running the parity test

```bash
pytest tests/test_cross_market_inflation_swap_spread_parity.py -v
```
