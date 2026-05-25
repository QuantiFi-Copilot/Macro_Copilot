# Parity fixtures — `policy_futures_futures_price_level_v1`

Locked-in production output of
`calculate_policy_futures_price_level` for one representative
strip-slot snapshot, captured against a live TimescaleDB by
`_capture.py`. The companion
`tests/test_policy_futures_futures_price_level_parity.py` replays
each fixture with the DB fetchers mocked and asserts byte-equal
output (modulo a tight 1e-9 absolute float tolerance) against the
recorded result.

Bound by PR15 — primitives get parity coverage from day-one (see
`docs_revamped/02_components/primitive/README.md` L483-L504).

## What's captured

- `full_universe_sfr1.json` — `SOFR_FUT` strip_position=1
  (the front SOFR contract) anchored at `2026-04-08` (the
  policy_futures ingest as-of). RFR regime, inverse-priced.

Why ONE fixture instead of one per curve family: the math is
metadata-driven (the `inverse_pricing` flag determines the implied-
rate conversion; the regime label is disclosure-only). One
representative inverse-priced fixture exercises the full computation
path. The SQL-validation runner
(`tests/test_policy_futures_futures_price_level_sql_validation.py`)
sweeps multiple `(curve_family, strip_position)` cases — that is the
breadth check. This parity fixture is the byte-equal anchor.

## Regeneration policy

Only after a deliberate methodology change (YAML edit, schema
change, fetcher-shape change, conversion-rule change). NEVER
regenerate to make a failing parity test pass — the failure is
load-bearing; either the methodology changed deliberately (in
which case the regeneration is the right diff) or the
implementation drifted accidentally (in which case the fix is in
the implementation, not in the fixture).

To regenerate:

```bash
cd /app
python tests/fixtures/policy_futures_futures_price_level_v1/_capture.py
```

The script requires a live TimescaleDB with the `policy_futures`
playbook ingested. Re-runs are NOT idempotent across capture
days — `captured_at` rotates and the recorded
`expected_output.current_metrics.as_of_date` shifts if the DB has
gained new data — so do NOT regenerate casually.
