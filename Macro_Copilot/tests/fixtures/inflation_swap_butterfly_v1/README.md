# `inflation_swap_butterfly_v1` parity fixtures

Snapshots of the **real production output** of
`calculate_inflation_swap_butterfly` captured against the live
TimescaleDB.  PR15 backfill (Phase 3 Step 9).

Companion test: `tests/test_inflation_swap_butterfly_parity.py`.

## Compose-primitive seam topology

Three inner `calculate_inflation_swap_rate_level` calls.  Patches
the same two seams stage-3's `inflation_swap_curve_spread` patches:

1. `inflation_swap_rate_level.compute.fetch_zcis_single_pillar`
2. `inflation_swap_rate_level.compute.date`

See `tests/fixtures/inflation_swap_curve_spread_v1/README.md` for
the canonical format; this folder uses the same shape with THREE
`raw_rows` entries per scenario instead of two.

## Generating fixtures

```bash
docker-compose up -d tsdb
export DB_HOST=localhost DB_PORT=5433 DB_USER=quantuser \
       DB_PASSWORD=... DB_NAME=macrodata
python tests/fixtures/inflation_swap_butterfly_v1/_capture.py
```

| Fixture | Scenario | Rationale |
|---|---|---|
| `usd_zcis_5s10s30s_365d.json` | USD_ZCIS 5/10/30, 1Y | Canonical US ZCIS butterfly read. |
| `eur_zcis_2s5s10s_365d.json` | EUR_ZCIS 2/5/10, 1Y | Different belly point + EUR/HICP/Monthly convention. |
| `gbp_zcis_5s10s30s_365d.json` | GBP_ZCIS 5/10/30, 1Y | Third currency + UK_RPI / 2M / Monthly convention. |

## Running the parity test

```bash
pytest tests/test_inflation_swap_butterfly_parity.py -v
```
