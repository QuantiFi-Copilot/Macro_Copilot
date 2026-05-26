# `breakeven_curve_spread_v1` parity fixtures

Snapshots of the **real production output** of
`calculate_breakeven_curve_spread` captured against the live
TimescaleDB.  PR15 backfill (Phase 3 Step 9).

Companion test: `tests/test_breakeven_curve_spread_parity.py`.

## Compose-primitive seam topology

`calculate_breakeven_curve_spread` composes TWO inner
`calculate_breakeven_inflation_simple` calls (one per endpoint
tenor of the same `(nominal_curve_family, linker_curve_family)`
pair).  Each inner spot call does:

  - 2 `_fetch_curve_family_country_currency` lookups (nominal +
    linker), AND
  - 2 `fetch_single_tenor` calls (nominal + linker)

so per scenario the live tool issues 4 country lookups (2 unique
keys, called twice each) and 4 fetches (4 unique keys — 2 legs ×
2 tenors).

The parity test patches three seams, ALL on the INNER spot
primitive's module (the compose primitive has no DB seams of its
own — it just imports `calculate_breakeven_inflation_simple` and
calls it):

1. `breakeven_inflation_simple.compute.fetch_single_tenor`
2. `breakeven_inflation_simple.compute._fetch_curve_family_country_currency`
3. `breakeven_inflation_simple.compute.date`

See `tests/fixtures/breakeven_inflation_simple_v1/README.md` for
the canonical fixture format.  This folder uses the same shape
with FOUR `raw_rows` entries per scenario instead of two.

## Generating fixtures

```bash
docker-compose up -d tsdb
export DB_HOST=localhost DB_PORT=5433 DB_USER=quantuser \
       DB_PASSWORD=... DB_NAME=macrodata
python tests/fixtures/breakeven_curve_spread_v1/_capture.py
```

| Fixture | Scenario | Rationale |
|---|---|---|
| `ust_usd_tips_5s10s_365d.json` | UST/USD_TIPS 5s10s, 1Y | Canonical US 5s10s breakeven curve read. |
| `uk_gilt_gbp_linker_5s30s_730d.json` | UK_GILT/GBP_LINKER 5s30s, 2Y | Different country, longer-end pair, 2x lookback so the displayed slice is non-trivially larger than the z-score window. |
| `ust_usd_tips_10s30s_365d.json` | UST/USD_TIPS 10s30s, 1Y | Same pair as the canonical case but different curve shape — guards against drift specific to a long-end pair. |

## Running the parity test

```bash
pytest tests/test_breakeven_curve_spread_parity.py -v
```

Regeneration policy mirrors
`tests/fixtures/breakeven_inflation_simple_v1/README.md`.
