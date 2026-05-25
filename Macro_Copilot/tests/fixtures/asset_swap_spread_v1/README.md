# `asset_swap_spread_v1` parity fixtures

Snapshots that lock in the **regression behavior** of
`get_asset_swap_spread` (the per-bond INGESTED Bloomberg ASW primitive
shipping under `rates_agent/ois/tools/asset_swap_spread/`).

The companion test at `tests/test_asset_swap_spread_parity.py` reloads
each fixture, mocks the engine to replay captured `raw_rows`, freezes
`date.today()` to the recorded value, runs the tool, and asserts the
output matches the recorded `expected_output` byte-for-byte (modulo
`1e-9` float tolerance — though for an INGEST primitive the raw ASW
values should match bit-exact since no rounding is applied).

## Purpose — PR15

This primitive ships with PR15 parity-fixture infrastructure from day
one (the binding rule for *new* primitives in the contract at
`docs_revamped/02_components/primitive/README.md`).  The fixtures
shipped here lock in the primitive's output shape + content for known
inputs, so any future code change that breaks the snapshot or the
canonical TimeSeries fails the parity test loudly.

## v1 fixture provenance — synthetic, deterministic

The shipped v1 fixtures carry `capture.capture_method = "synthetic_v1"`.
The `raw_rows` are not from a captured live-DB run; they are
deterministic test scenarios constructed to exercise the INGEST
contract:

- `de_bund_2y_explicit_date.json` — explicit `as_of_date` mode with a
  bond identity row (Germany 2Y bund, `/isin/DE000BU22130`).  Locks
  in the bit-exact preservation of the raw ASW value with full
  3-decimal precision (`-19.471`) per the live-DB shape Codex
  identified.
- `us_10y_latest_mode.json` — latest mode (as_of_date=None) with
  identity for US 10Y (`/isin/US91282CQQ77`).  Pins the resolution
  rule "snapshot is the most recent non-NULL observation" + the
  identity-echo block.

**Why synthetic, not live-DB?**  The primitive ships in a fresh PR
against the `build` branch where the SQL-validation runner serves as
the live-DB anchor (Layer B).  The parity fixtures here are Layer A's
offline-replay regression check — their job is to detect
configuration drift, schema breakage, or unintended math changes; the
SQL-validation runner is the wire-level INGEST parity check that
asserts 1e-9 match against the actual Bloomberg-ingested value.

When the operator runs `_capture.py` against a populated DB after
merge, captured-from-DB fixtures replace these v1 fixtures (with
`capture_method = "live_db_v1"`) in a follow-up PR.

## Fixture format

```json
{
  "fixture_name": "...",
  "tool_module": "rates_agent.ois.tools.asset_swap_spread",
  "tool_function": "get_asset_swap_spread",
  "capture": {
    "captured_at": "ISO-8601Z",
    "capture_method": "synthetic_v1",
    "frozen_today": "YYYY-MM-DD",
    "raw_rows_count": N,
    "raw_rows_sha256": "..."
  },
  "input": {
    "params": {"vendor_ticker": ..., "as_of_date": ..., "lookback_days": ...},
    "frozen_today": "YYYY-MM-DD",
    "identity_row": {country, currency, cusip, isin, maturity_date, instrument_type},
    "raw_rows": [{"trade_date": ..., "field_value": ...}, ...]
  },
  "expected_output": {current_metrics, time_series, methodology_note}
}
```

- `capture.raw_rows_sha256` is a SHA-256 of the canonical-JSON
  serialisation of `raw_rows` (sorted keys, no whitespace).  The
  parity test recomputes the hash and refuses to run if it has been
  tampered with — guards against silent fixture edits.

## Running the parity test

```bash
pytest tests/test_asset_swap_spread_parity.py -v
```

When no fixtures are present the test is skipped with an explanatory
message.

## Regenerating

Only after a deliberate methodology change.  The regeneration script
is `tests/fixtures/asset_swap_spread_v1/_regenerate.py` (synthetic
v1) and `tests/fixtures/asset_swap_spread_v1/_capture.py` (live-DB
once available).
