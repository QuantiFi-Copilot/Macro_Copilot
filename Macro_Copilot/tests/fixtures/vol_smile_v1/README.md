# `vol_smile_v1` parity fixtures

PR15 parity snapshot for `get_fx_vol_smile`. Companion test:
`tests/test_fx_smile_parity.py`.

## Purpose — PR15

Locks in the primitive's output shape + numeric content for known
inputs. Any future code change that alters the snapshot contract
fails the parity test loudly.

## Minimal-per-compute-path discipline

Per Codex correction 2026-05-27, this fixture set is intentionally
minimal: **one canonical case** per tool. Canonical case =
`EURUSD 1M` 5-point smile aggregate (ATM + 25R + 25B + 10R + 10B).
Broader cross-pair / tenor coverage is asserted by the SQL validation
test (`test_fx_vol_smile_sql_validation.py`).

## Fixture format

`eurusd_1m_5pt_smile.json` carries 5 query blocks (one per smile
point) since the vol_smile compute path joins ATM (fx_vol substrate)
with the 4 smile points (fx_vol_smile substrate) in one tool call.

## Regenerating

```bash
python3 tests/fixtures/_fx_smile_capture.py
```

## Running the parity test

```bash
python3 tests/test_fx_smile_parity.py
```
