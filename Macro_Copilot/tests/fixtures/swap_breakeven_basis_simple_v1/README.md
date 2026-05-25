# `swap_breakeven_basis_simple_v1` parity fixtures

Snapshots of the **real production output** of
`calculate_swap_breakeven_basis_simple` captured against the live
TimescaleDB.  PR15 backfill (Phase 3 Step 9) — the closing
primitive of the 15-primitive inflation backfill batch.

Companion test:
`tests/test_swap_breakeven_basis_simple_parity.py`.

## Cross-domain compose seam topology

`calculate_swap_breakeven_basis_simple` is the only stage-3
primitive whose compose path crosses domain boundaries:

- ZCIS leg: `calculate_inflation_swap_rate_level`
  (uses this primitive's bundled ToolConfig)
- Breakeven leg: `calculate_breakeven_inflation_simple`
  (uses ITS OWN bundled ToolConfig — `breakeven_config = load_tool_config(BREAKEVEN_INFLATION_SIMPLE_CONFIG_PATH)`)

The parity test patches FIVE seams across TWO modules:

| Seam | Module |
|---|---|
| `fetch_zcis_single_pillar` | `inflation_swap_rate_level.compute` |
| `date` | `inflation_swap_rate_level.compute` |
| `fetch_single_tenor` | `breakeven_inflation_simple.compute` |
| `_fetch_curve_family_country_currency` | `breakeven_inflation_simple.compute` |
| `date` | `breakeven_inflation_simple.compute` |

Two distinct `date` seams are patched (one per inner primitive)
because the compose primitive itself does not import `date` — the
fetch-start logic lives independently inside each inner primitive.

## Fixture format

Three provenance streams per scenario:

- `input.raw_rows_zcis` — 1 entry keyed by
  `"<zcis_curve_family>__<tenor>__<field_name>"` whose value is the
  8-column ZCIS row stream.
- `input.raw_rows_breakeven` — 2 entries keyed by
  `"<curve_family>__<tenor>__<field_name>__<instrument_type>"`
  (linker + nominal legs).
- `input.country_currency` — 2 entries keyed by
  `"<curve_family>__<instrument_type>"` (the identity-guard rows
  the breakeven primitive consults before fetching).

`capture.raw_rows_sha256` hashes the combined payload
(`ZCIS|...|BE|...|CC|...`) so any hand edit to any provenance
stream trips the tamper guard.

## Generating fixtures

```bash
docker-compose up -d tsdb
export DB_HOST=localhost DB_PORT=5433 DB_USER=quantuser \
       DB_PASSWORD=... DB_NAME=macrodata
python tests/fixtures/swap_breakeven_basis_simple_v1/_capture.py
```

| Fixture | ZCIS leg − Nominal/Linker leg | Rationale |
|---|---|---|
| `usd_zcis_ust_usd_tips_10y_365d.json` | USD_ZCIS 10Y − UST/USD_TIPS 10Y | Canonical US 10Y swap-breakeven basis; pins the cleanest same-country basis read. |
| `eur_zcis_fr_oat_eur_fr_linker_5y_365d.json` | EUR_ZCIS 5Y − FR_OAT/EUR_FR_LINKER 5Y | EUR-France pair at 5Y; exercises a different inflation-index family (HICP) on both legs. |
| `gbp_zcis_uk_gilt_gbp_linker_10y_730d.json` | GBP_ZCIS 10Y − UK_GILT/GBP_LINKER 10Y | UK pair with 2Y lookback; GBP_ZCIS has the only 2M index lag in the ingested universe. |

## Running the parity test

```bash
pytest tests/test_swap_breakeven_basis_simple_parity.py -v
```
