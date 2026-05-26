# `real_yield_butterfly_v1` parity fixtures

Snapshots of the **real production output** of
`calculate_real_yield_butterfly` captured against the live
TimescaleDB.  PR15 backfill (Phase 3 Step 9).

Companion test: `tests/test_real_yield_butterfly_parity.py`.

## Compose-primitive seam topology

`calculate_real_yield_butterfly` composes THREE inner
`get_real_yield_level` calls (short / belly / long) and runs one
identity-guard DB read in its own compute module.  The parity test
patches three seams:

1. `real_yield_butterfly.compute._fetch_curve_family_country_currency`
2. `real_yield_level.compute.fetch_single_tenor`
3. `real_yield_level.compute.date`

See `tests/fixtures/real_yield_curve_spread_v1/README.md` for the
canonical fixture format — this folder uses the same shape with
three `raw_rows` entries per scenario instead of two.

## Generating fixtures

```bash
docker-compose up -d tsdb
export DB_HOST=localhost DB_PORT=5433 DB_USER=quantuser \
       DB_PASSWORD=... DB_NAME=macrodata
python tests/fixtures/real_yield_butterfly_v1/_capture.py
```

| Fixture | Scenario | Rationale |
|---|---|---|
| `usd_tips_5s10s30s_365d.json` | USD_TIPS 5/10/30Y butterfly, 1Y | Canonical US linker curvature read. |
| `gbp_linker_5s10s30s_365d.json` | GBP_LINKER 5/10/30Y butterfly, 1Y | Different currency + index family (UK/GBP RPI). |
| `usd_tips_5s20s30s_365d.json` | USD_TIPS 5/20/30Y butterfly, 1Y | Same curve as the canonical case but a different belly point — guards against drift specific to a curvature shape. |

## Running the parity test

```bash
pytest tests/test_real_yield_butterfly_parity.py -v
```

Regeneration policy and fixture format details mirror
`tests/fixtures/real_yield_curve_spread_v1/README.md` exactly.
