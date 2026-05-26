# Parity fixtures — `policy_futures_futures_butterfly_simple_v1`

Locked-in production output of
`calculate_futures_butterfly_simple` for one representative
same-curve simple-butterfly snapshot, captured against a live
TimescaleDB by `_capture.py`. The companion
`tests/test_policy_futures_futures_butterfly_simple_parity.py`
replays each fixture with the DB fetchers mocked and asserts
byte-equal output (modulo a tight 1e-9 absolute float tolerance)
against the recorded result.

Bound by PR15 — primitives get parity coverage from day-one.

## What's captured

- `full_universe_sfr1_sfr2_sfr4.json` — `SOFR_FUT` simple
  butterfly between wing_short=1 (SFR1), body=2 (SFR2), and
  wing_long=4 (SFR4) anchored at `2026-04-08` (the policy_futures
  ingest as-of). RFR regime, all three legs inverse-priced.

Why ONE fixture instead of one per curve family: the math is
metadata-driven (the per-leg `inverse_pricing` flag determines the
implied-rate conversion; the regime label is disclosure-only) AND
the butterfly formula is the same fixed 50-50 weighting across all
families. One representative inverse-priced fixture exercises the
full computation path. The SQL-validation runner
(`tests/test_policy_futures_futures_butterfly_simple_sql_validation.py`)
sweeps multiple `(curve_family, wing_short, body, wing_long)` cases
including both adjacent (1, 2, 4) and wider (1, 4, 8) triples on
RFR and IBOR regimes — that is the breadth check. This parity
fixture is the byte-equal anchor.

## Regeneration policy

Only after a deliberate methodology change (YAML edit, schema
change, fetcher-shape change, weighting-rule change). NEVER
regenerate to make a failing parity test pass.

To regenerate:

```bash
cd /app
python tests/fixtures/policy_futures_futures_butterfly_simple_v1/_capture.py
```

The script requires a live TimescaleDB with the `policy_futures`
playbook ingested.
