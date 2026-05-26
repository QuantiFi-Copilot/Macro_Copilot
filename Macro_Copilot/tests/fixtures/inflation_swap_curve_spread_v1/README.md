# `inflation_swap_curve_spread_v1` parity fixtures

Snapshots of the **real production output** of
`calculate_inflation_swap_curve_spread` captured against the live
TimescaleDB.  PR15 backfill (Phase 3 Step 9).

Companion test: `tests/test_inflation_swap_curve_spread_parity.py`.

## Compose-primitive seam topology

`calculate_inflation_swap_curve_spread` composes TWO inner
`calculate_inflation_swap_rate_level` calls (one per endpoint
tenor of the same ZCIS `curve_family`).  All DB seams live INSIDE
the inner level primitive's compute module:

1. `inflation_swap_rate_level.compute.fetch_zcis_single_pillar`
2. `inflation_swap_rate_level.compute.date`

The compose primitive itself has no DB seams — it imports
`calculate_inflation_swap_rate_level` and calls it twice.  Its
same-curve reference-metadata equality guard
(`_assert_same_curve_reference_metadata`) runs against the inner
primitive's `current_metrics`, which is data-derived from the
captured rows — no DB read of its own.

## Fixture format

See `tests/fixtures/inflation_swap_rate_level_v1/README.md` for
the canonical 8-column ZCIS raw-row shape.  This folder uses the
same shape with TWO `raw_rows` entries per scenario (one per
endpoint tenor), keyed by
`"<curve_family>__<tenor>__<field_name>"` (no `instrument_type`
slot since the inner level primitive binds
`instrument_type='inflation_swap'` AND
`pricing_type='zero_coupon_breakeven'` in code, not as fetcher
arguments).

The `capture.raw_rows_sha256` hashes ONLY the `raw_rows` dict (no
separate `country_currency` dict for ZCIS compose primitives —
the reference metadata is carried in the 8 columns of each row).

## Generating fixtures

```bash
docker-compose up -d tsdb
export DB_HOST=localhost DB_PORT=5433 DB_USER=quantuser \
       DB_PASSWORD=... DB_NAME=macrodata
python tests/fixtures/inflation_swap_curve_spread_v1/_capture.py
```

| Fixture | Scenario | Reference convention | Rationale |
|---|---|---|---|
| `usd_zcis_5s10s_365d.json` | USD_ZCIS 5s10s, 1Y | `US_CPI_URBAN` / `3M` / `Daily` | Canonical US ZCIS curve-spread read. |
| `eur_zcis_5s30s_730d.json` | EUR_ZCIS 5s30s, 2Y | `EU_HICP` / `3M` / `Monthly` | Longer-end EUR pair, 2x lookback so the displayed slice is non-trivially larger than the z-score window. |
| `gbp_zcis_5s10s_365d.json` | GBP_ZCIS 5s10s, 1Y | `UK_RPI` / `2M` / `Monthly` | Third index family with the distinct `2M` index lag. |

## Running the parity test

```bash
pytest tests/test_inflation_swap_curve_spread_parity.py -v
```

Regeneration policy mirrors
`tests/fixtures/inflation_swap_rate_level_v1/README.md`.
