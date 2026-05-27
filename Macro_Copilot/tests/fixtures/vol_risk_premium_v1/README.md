# `vol_risk_premium_v1` parity fixtures

PR15 parity snapshot for `get_fx_vol_risk_premium`. Companion test:
`tests/test_fx_vol_carry_parity.py`.

## Purpose — PR15

Locks in the primitive's output shape + numeric content for known
inputs. Any future code change that alters the snapshot contract
fails the parity test loudly.

## Minimal-per-compute-path discipline

Per Phase E2 convention ("minimales"), this fixture set is
intentionally minimal: **one canonical case**. Canonical case =
`EURUSD 1M tenor_matched` — desk-standard pair × tenor × default
methodology knob. Broader cross-pair / cross-basis coverage is
asserted by the SQL validation test
(`test_fx_vol_risk_premium_sql_validation.py`).

## Fixture format

`eurusd_1m_tenor_matched.json` is a self-contained fixture with two
substrates captured:

```json
{
  "input": {
    "params": {"pair": "EURUSD", "tenor": "1M", ...},
    "queries": [
      {"substrate": "fx_vol",  "tenor": "1M", "rows": [...]},
      {"substrate": "fx_spot",                "rows": [...]}
    ]
  },
  "expected_output": {...}
}
```

The parity test freezes `date.today()` to `capture.captured_at` so
the in-compute cutoff slicing is reproducible regardless of when
the test runs.

## Tamper detection

`raw_rows_sha256` covers the captured rows. The parity test
recomputes this hash on load and fails loudly if the JSON has been
hand-edited without re-capturing.

## Regenerating

```bash
python3 tests/fixtures/_fx_vol_carry_capture.py
```

## Running the parity test

```bash
python3 tests/test_fx_vol_carry_parity.py
```
