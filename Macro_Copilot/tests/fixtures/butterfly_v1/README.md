# `butterfly_v1` parity fixtures

PR15 parity snapshot for `get_fx_butterfly`. Companion test:
`tests/test_fx_smile_parity.py`.

## Purpose — PR15

Locks in the primitive's output shape + numeric content for known
inputs. Any future code change that alters the snapshot contract
fails the parity test loudly.

## Minimal-per-compute-path discipline

Per Codex correction 2026-05-27, this fixture set is intentionally
minimal: **one canonical case** per tool. Canonical case =
`EURUSD 25B 1M` — desk-standard G10 major × 25Δ wing anchor × 1M
tenor. Broader cross-pair / tenor coverage is asserted by the SQL
validation test (`test_fx_butterfly_sql_validation.py`).

## Fixture format

See `tests/fixtures/risk_reversal_v1/README.md` — same shape, with
`smile_point="25B"` and `expected_output.current_metrics.current_butterfly_vol_pts`.

## Regenerating

```bash
python3 tests/fixtures/_fx_smile_capture.py
```

## Running the parity test

```bash
python3 tests/test_fx_smile_parity.py
```
