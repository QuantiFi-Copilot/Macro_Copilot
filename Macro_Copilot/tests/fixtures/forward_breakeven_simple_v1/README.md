# `forward_breakeven_simple_v1` parity fixtures

Snapshots of the **real production output** of
`calculate_forward_breakeven_simple` captured against the live
TimescaleDB.  PR15 backfill (Phase 3 Step 9).

Companion test: `tests/test_forward_breakeven_simple_parity.py`.

## Compose-primitive seam topology

`calculate_forward_breakeven_simple` composes TWO inner
`calculate_breakeven_inflation_simple` calls (one at `start_tenor`,
one at `end_tenor` of the same `(nominal_curve_family,
linker_curve_family)` pair) and combines them via the year-weighted
linear forward formula

    forward_breakeven_bps =
        (BE_end_bps * T_end - BE_start_bps * T_start)
        / (T_end - T_start)

The seam topology mirrors `breakeven_curve_spread` exactly — all
seams on the INNER spot primitive's module:

1. `breakeven_inflation_simple.compute.fetch_single_tenor`
2. `breakeven_inflation_simple.compute._fetch_curve_family_country_currency`
3. `breakeven_inflation_simple.compute.date`

See `tests/fixtures/breakeven_curve_spread_v1/README.md` for the
shared format details.

## Generating fixtures

```bash
docker-compose up -d tsdb
export DB_HOST=localhost DB_PORT=5433 DB_USER=quantuser \
       DB_PASSWORD=... DB_NAME=macrodata
python tests/fixtures/forward_breakeven_simple_v1/_capture.py
```

| Fixture | Forward window | Rationale |
|---|---|---|
| `ust_usd_tips_5y10y_365d.json` | US 5Y5Y forward | Canonical US 5Y forward starting in 5Y — the most-quoted forward breakeven on the US curve. |
| `ust_usd_tips_10y30y_365d.json` | US 20Y forward starting in 10Y | Longer-end variant; exercises the year-weighted formula's sensitivity at extended `dt_years`. |
| `uk_gilt_gbp_linker_5y10y_365d.json` | UK 5Y5Y forward | Different country + index family. |

## Running the parity test

```bash
pytest tests/test_forward_breakeven_simple_parity.py -v
```

Regeneration policy mirrors
`tests/fixtures/breakeven_inflation_simple_v1/README.md`.
