# `cross_country_real_yield_spread_simple_v1` parity fixtures

Snapshots of the **real production output** of
`calculate_cross_country_real_yield_spread_simple` captured against
the live TimescaleDB.  PR15 backfill (Phase 3 Step 9).

Companion test:
`tests/test_cross_country_real_yield_spread_simple_parity.py`.

## Compose-primitive seam topology

`calculate_cross_country_real_yield_spread_simple` composes TWO
inner `get_real_yield_level` calls for DIFFERENT linker curves at
the same tenor, and runs TWO identity-guard DB reads in its own
compute module (one per leg).  The parity test patches three seams:

1. `cross_country_real_yield_spread_simple.compute._fetch_curve_family_country_currency`
   (returns the captured tuple per
   `(curve_family, instrument_type='inflation_linker')` — TWO
   unique keys per run).
2. `real_yield_level.compute.fetch_single_tenor` — TWO unique keys
   per run (one per curve_family, same shared tenor).
3. `real_yield_level.compute.date`

See `tests/fixtures/real_yield_curve_spread_v1/README.md` for the
canonical fixture format.

## Generating fixtures

```bash
docker-compose up -d tsdb
export DB_HOST=localhost DB_PORT=5433 DB_USER=quantuser \
       DB_PASSWORD=... DB_NAME=macrodata
python tests/fixtures/cross_country_real_yield_spread_simple_v1/_capture.py
```

| Fixture | Scenario | Rationale |
|---|---|---|
| `usd_tips_gbp_linker_10y_365d.json` | US-UK 10Y, 1Y | Canonical USD-GBP cross-country real-yield read; pins the CPI-U vs RPI index-family mismatch caveat into the wire surface. |
| `usd_tips_eur_fr_linker_10y_365d.json` | US-FR 10Y, 1Y | USD-EUR pair — exercises the EUR linker leg with a different inflation index family (HICP) and different (country, currency) identity from the USD-GBP case. |
| `gbp_linker_eur_fr_linker_10y_730d.json` | UK-FR 10Y, 2Y | Non-USD pair, longer 2Y lookback; exercises the inner-join discipline when the two linker markets have different holiday patterns. |

## Running the parity test

```bash
pytest tests/test_cross_country_real_yield_spread_simple_parity.py -v
```

Regeneration policy and fixture format details mirror
`tests/fixtures/real_yield_curve_spread_v1/README.md` exactly.
