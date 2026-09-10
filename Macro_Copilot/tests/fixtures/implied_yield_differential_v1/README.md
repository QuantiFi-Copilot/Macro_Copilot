# `implied_yield_differential_v1` parity fixtures

PR15 parity snapshot for `get_fx_implied_yield_differential`.
Companion test: `tests/test_fx_carry_extensions_parity.py`.

## Purpose — PR15

Locks in the primitive's output shape + numeric content for known
inputs. Any future code change that alters the snapshot contract
fails the parity test loudly.

## Minimal-per-compute-path discipline

One canonical case = `EURUSD 1M` — desk-standard G10 USD-quote pair
× 1M tenor. Broader cross-pair / tenor / sign-convention coverage is
asserted by `test_fx_implied_yield_differential_sql_validation.py`.

## Fixture format

`eurusd_1m.json` carries the joined (spot, forward_points) history
from the inner-join SQL query. The parity test monkey-patches
`pd.read_sql` to replay the captured rows, freezes `pd.Timestamp.today()`
to `capture.captured_at` so the start_date filter is reproducible,
runs the tool, and asserts byte-identical output.

## Tamper detection

`raw_rows_sha256` covers the captured rows. The parity test
recomputes this hash on load and fails loudly if hand-edited.

## Regenerating

```bash
python3 tests/fixtures/_fx_carry_extensions_capture.py
```

## Running the parity test

```bash
python3 tests/test_fx_carry_extensions_parity.py
```
