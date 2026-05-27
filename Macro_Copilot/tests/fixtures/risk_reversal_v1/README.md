# `risk_reversal_v1` parity fixtures

PR15 parity snapshot for `get_fx_risk_reversal`. Companion test:
`tests/test_fx_smile_parity.py`.

## Purpose — PR15

Locks in the primitive's output shape + numeric content for known
inputs. Any future code change that alters the snapshot contract
fails the parity test loudly.

## Minimal-per-compute-path discipline

Per Codex correction 2026-05-27, this fixture set is intentionally
minimal: **one canonical case** per tool (not an exhaustive matrix).
The canonical case here is `EURUSD 25R 1M` — the desk-standard
G10 major × 25Δ skew anchor × 1M tenor. Broader cross-pair / tenor
coverage is asserted by the SQL validation test
(`test_fx_risk_reversal_sql_validation.py`).

## Fixture format

`eurusd_25r_1m.json` is a self-contained fixture:

```json
{
  "fixture_name": "eurusd_25r_1m",
  "tool_module": "fx_agent.vol.tools.risk_reversal",
  "tool_function": "get_fx_risk_reversal",
  "capture": {
    "captured_at": "...",
    "capture_method": "live_db_v1",
    "database_name": "macrodata",
    "raw_rows_count": N,
    "raw_rows_sha256": "..."
  },
  "input": {
    "params": {"pair": "EURUSD", "delta_anchor": 25, ...},
    "queries": [
      {"substrate": "fx_vol_smile", "smile_point": "25R", "rows": [...]}
    ]
  },
  "expected_output": {"current_metrics": {...}}
}
```

## Tamper detection

`raw_rows_sha256` covers the captured rows. The parity test recomputes
this hash on load and fails loudly if the JSON has been hand-edited
without re-capturing.

## Regenerating the fixture (live-DB)

Only after a deliberate methodology change. Re-running the capture
overwrites the JSON in place.

```bash
python3 tests/fixtures/_fx_smile_capture.py
```

## Running the parity test

```bash
python3 tests/test_fx_smile_parity.py
```
