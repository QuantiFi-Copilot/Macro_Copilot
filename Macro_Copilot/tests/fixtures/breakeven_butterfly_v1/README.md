# `breakeven_butterfly_v1` parity fixtures

Snapshots of the **real production output** of
`calculate_breakeven_butterfly` captured against the live
TimescaleDB.  PR15 backfill (Phase 3 Step 9).

Companion test: `tests/test_breakeven_butterfly_parity.py`.

## Compose-primitive seam topology

`calculate_breakeven_butterfly` composes THREE inner
`calculate_breakeven_inflation_simple` calls (one per endpoint
tenor of the same `(nominal_curve_family, linker_curve_family)`
pair).  Each inner call does 2 country lookups + 2 fetches, so per
scenario the live tool issues 6 country lookups (2 unique keys,
called 3× each) and 6 fetches (6 unique keys — 2 legs × 3 tenors).

The parity test patches three seams, ALL on the INNER spot
primitive's module:

1. `breakeven_inflation_simple.compute.fetch_single_tenor`
2. `breakeven_inflation_simple.compute._fetch_curve_family_country_currency`
3. `breakeven_inflation_simple.compute.date`

See `tests/fixtures/breakeven_curve_spread_v1/README.md` for the
shared format details.  This folder uses the same shape with SIX
`raw_rows` entries per scenario instead of four.

## Generating fixtures

```bash
docker-compose up -d tsdb
export DB_HOST=localhost DB_PORT=5433 DB_USER=quantuser \
       DB_PASSWORD=... DB_NAME=macrodata
python tests/fixtures/breakeven_butterfly_v1/_capture.py
```

| Fixture | Scenario | Rationale |
|---|---|---|
| `ust_usd_tips_5s10s30s_365d.json` | UST/USD_TIPS 5-10-30Y, 1Y | Canonical US breakeven butterfly read. |
| `uk_gilt_gbp_linker_5s10s30s_365d.json` | UK_GILT/GBP_LINKER 5-10-30Y, 1Y | Different country + index family. |
| `ust_usd_tips_5s10s30s_730d.json` | UST/USD_TIPS 5-10-30Y, 2Y | Same canonical pair with a 2x lookback — exercises rolling-stat coverage across a longer displayed window. |

## Running the parity test

```bash
pytest tests/test_breakeven_butterfly_parity.py -v
```

Regeneration policy mirrors
`tests/fixtures/breakeven_inflation_simple_v1/README.md`.
