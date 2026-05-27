# `carry_basket_v1` parity fixtures

PR15 parity snapshot for `get_fx_carry_basket`. Companion test:
`tests/test_fx_carry_extensions_parity.py`.

## Purpose — PR15

Locks in the strategy-index primitive's full output (snapshot stats
+ cumulative TimeSeries + constituents) for one canonical case.

## Minimal-per-compute-path discipline

One canonical case = `G10 1M top_n=3 long_short_top_n` — desk-
standard G10 zero-cost cross-sectional FX carry strategy. Broader
scope / construction coverage asserted by
`test_fx_carry_basket_sql_validation.py`.

## Fixture format

`g10_1m_top3_long_short.json` carries the per-pair (spot,
forward_points) histories for all G10 deliverable pairs over the
2y lookback. The parity test concatenates them into the wide df
shape `pd.read_sql` would return, freezes `pd.Timestamp.today()` to
`capture.captured_at` so the start_date and rebalance schedule are
reproducible, runs the tool, and asserts byte-identical output.

## Tamper detection

`raw_rows_sha256` covers the captured rows. The parity test
recomputes this hash on load and fails loudly if hand-edited.

## STRATEGY INDEX — NOT EXECUTABLE BACKTEST

This fixture stores the output of a PAPER strategy index with NO
transaction costs / bid-ask / slippage / forward roll. The real-
money equivalent diverges by typically 200-400 bp annualized after
those frictions. See `fx_agent/forwards/tools/carry_basket/config.yaml`
methodology block for the full set of V1 hard-locks.

## Regenerating

```bash
python3 tests/fixtures/_fx_carry_extensions_capture.py
```

## Running the parity test

```bash
python3 tests/test_fx_carry_extensions_parity.py
```
