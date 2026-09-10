# `vol_calendar_spread_v1` parity fixtures

PR15 parity snapshot for `get_fx_vol_calendar_spread`. Companion
test: `tests/test_fx_vol_carry_parity.py`.

## Purpose — PR15

Locks in the primitive's output shape + numeric content for known
inputs. Any future code change that alters the snapshot contract
fails the parity test loudly.

## Minimal-per-compute-path discipline

One canonical case = `EURUSD 1M/3M long_minus_short` — desk-standard
pair × belly calendar × default sign convention. Broader coverage
is asserted by `test_fx_vol_calendar_spread_sql_validation.py`.

## Fixture format

`eurusd_1m_3m_long_minus_short.json` carries 2 query blocks (one
per leg). The parity test replays each by matching the `tenor` SQL
param to the captured `tenor` per leg.

## Regenerating

```bash
python3 tests/fixtures/_fx_vol_carry_capture.py
```

## Running the parity test

```bash
python3 tests/test_fx_vol_carry_parity.py
```
