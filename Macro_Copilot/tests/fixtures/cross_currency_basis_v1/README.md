# `cross_currency_basis_v1` parity fixture

PR15 parity snapshot for `get_fx_cross_currency_basis`. Companion test:
`tests/test_fx_cross_currency_basis_parity.py`.

## Purpose — PR15

Locks in the cross-currency basis primitive's output shape + numeric
content for the canonical EURUSD 1M case.

## Minimal-per-compute-path discipline

One canonical case = `EURUSD 1M` with Bloomberg/BCRX sign convention
(negative basis = USD scarcity). Broader cross-pair/tenor coverage
asserted by `test_fx_cross_currency_basis_sql_validation.py`.

## Fixture format

`eurusd_1m.json` carries TWO substrates:
- `fx_joined_spot_forward` — joined (spot, forward_points) rows
- `ois_cross_market_pair` — (EUR_ESTR_OIS, USD_SOFR_OIS) at 1M tenor

The parity test monkey-patches both `pd.read_sql` (FX leg) and
`shared.analytics.rates_fetch.fetch_cross_market_pair` (OIS leg) plus
freezes `pd.Timestamp.today()` to the captured date for reproducibility.

## Tamper detection

`raw_rows_sha256` over all 768 captured rows. Parity test recomputes
on load and fails loudly if hand-edited.

## Regenerating

```bash
python3 tests/fixtures/_fx_cross_currency_basis_capture.py
```

## Running the parity test

```bash
python3 tests/test_fx_cross_currency_basis_parity.py
```
