# Parity fixtures — `policy_futures_volume_open_interest_snapshot_v1`

Locked-in production output of
`calculate_volume_open_interest_snapshot` for one representative
strip-slot snapshot, captured against a live TimescaleDB by
`_capture.py`. The companion
`tests/test_policy_futures_volume_open_interest_snapshot_parity.py`
replays each fixture with the DB fetchers mocked and asserts byte-
equal output (modulo a tight 1e-9 absolute float tolerance) against
the recorded result.

Bound by PR15 — primitives get parity coverage from day-one (see
`docs_revamped/02_components/primitive/README.md` L483-L504).

## What's captured

- `full_universe_sfr1.json` — `SOFR_FUT` strip_position=1
  (the front SOFR contract) anchored at `2026-04-08` (the
  policy_futures ingest as-of).

Why ONE fixture instead of one per curve family: the math is the
same across families (volume + OI + ΔOI + OI z-score + percentile +
volume rolling mean/max all read off the contract-count series and
care about no per-family methodology choice). One representative
fixture exercises the full computation path. The SQL-validation
runner
(`tests/test_policy_futures_volume_open_interest_snapshot_sql_validation.py`)
sweeps multiple `(curve_family, strip_position)` cases — that is
the breadth check. This parity fixture is the byte-equal anchor.

## Regeneration policy

Only after a deliberate methodology change (YAML edit, schema
change, fetcher-shape change). NEVER regenerate to make a failing
parity test pass — the failure is load-bearing; either the
methodology changed deliberately (in which case the regeneration is
the right diff) or the implementation drifted accidentally (in
which case the fix is in the implementation, not in the fixture).

To regenerate:

```bash
cd /app
python tests/fixtures/policy_futures_volume_open_interest_snapshot_v1/_capture.py
```

The script requires a live TimescaleDB with the `policy_futures`
playbook ingested. Re-runs are NOT idempotent across capture days —
`captured_at` rotates and the recorded
`expected_output.current_metrics.as_of_date` shifts if the DB has
gained new data — so do NOT regenerate casually.

## Change log

- **2026-05-23** — round-1 reviewer raised P5 finding that the
  methodology disclosure did not name the underlying short-rate
  regime (RFR vs IBOR), so a desk reader relaying SFR1 vs ER1 could
  not tell that a Euribor position is structurally different from a
  SOFR position. Round-2 prepended an
  `Underlying short-rate regime for {curve_family}: {regime}` sentence
  to the disclosure template
  (`compute._METHODOLOGY_DISCLOSURE_TEMPLATE`). Only
  `expected_output.methodology_disclosure` was re-recorded here; no
  other fixture field changed and `capture.raw_rows_sha256`
  (`54ae93341d46a2429f65e092b41fdf808dd2fa5a75a7e9cffcf9192d3b810653`)
  is unchanged — proving the raw volume / OI / reference inputs were
  not touched.
