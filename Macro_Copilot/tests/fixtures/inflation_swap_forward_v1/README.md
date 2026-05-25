# `inflation_swap_forward_v1` parity fixtures

Snapshots of the **real production output** of
`calculate_inflation_swap_forward` captured against the live
TimescaleDB.  PR15 backfill (Phase 3 Step 9).

Companion test: `tests/test_inflation_swap_forward_parity.py`.

## Compose-primitive seam topology

`calculate_inflation_swap_forward` composes TWO inner
`calculate_inflation_swap_rate_level` calls (one at `start_tenor`,
one at `end_tenor` of the same ZCIS `curve_family`) and combines
them via the dual-compounding geometric forward formula

    f = ((1 + r_end)^T_end / (1 + r_start)^T_start)
            ^ (1 / (T_end - T_start)) - 1

Patches the same two seams as
`inflation_swap_curve_spread`:

1. `inflation_swap_rate_level.compute.fetch_zcis_single_pillar`
2. `inflation_swap_rate_level.compute.date`

## Generating fixtures

```bash
docker-compose up -d tsdb
export DB_HOST=localhost DB_PORT=5433 DB_USER=quantuser \
       DB_PASSWORD=... DB_NAME=macrodata
python tests/fixtures/inflation_swap_forward_v1/_capture.py
```

| Fixture | Forward window | Rationale |
|---|---|---|
| `usd_zcis_5y10y_365d.json` | USD 5Y5Y forward | Canonical US 5Y forward starting in 5Y — the most-quoted ZCIS forward. |
| `eur_zcis_5y10y_365d.json` | EUR 5Y5Y forward | Different inflation index family (HICP) + Monthly interpolation. |
| `gbp_zcis_5y10y_365d.json` | GBP 5Y5Y forward | UK RPI / 2M / Monthly convention. |

## Running the parity test

```bash
pytest tests/test_inflation_swap_forward_parity.py -v
```
