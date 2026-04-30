# `curve_spread_v1` parity fixtures

Snapshots of `calculate_curve_spread` output for known synthetic inputs.
The companion test at `tests/test_curve_spread_parity.py` reloads each
fixture, mocks the database fetch, freezes `date.today()`, runs the tool,
and asserts the output matches the recorded `expected_output` byte-for-byte
(modulo float tolerance < 1e-9).

## Why these exist

Commit 0 of the tool-config refactor (see `docs/tool_architecture.md`,
landing later in the pilot).  The pilot will move `Z_SCORE_WINDOW`,
`ffill_limit`, and other conventions from Python constants into a YAML
config file, plus parameterise `shared/analytics/spreads.py` to accept
those values as kwargs.

Without a parity fixture, "the math is unchanged" rests on the existing
test suite, which is mostly CLI smoke-test scripts — not pytest tests
and not assertion-based.  These fixtures lock in the *current*
output bit-for-bit so any silent drift introduced by later commits fails
the parity test loudly.

## Structure

Each `*.json` file is a self-contained fixture:

```
{
  "fixture_name": "...",
  "tool_module": "rates_agent.sovereign_bonds.tools.curve_spread",
  "tool_function": "calculate_curve_spread",
  "input": {
    "params": {curve_family, short_tenor, long_tenor, lookback_days, field_name},
    "frozen_today": "YYYY-MM-DD",
    "raw_rows": [{trade_date, tenor, field_value}, ...]   # what fetch_tenor_pair would return
  },
  "expected_output": {current_metrics: {...}, time_series: [...]}
}
```

`raw_rows` is the long-format DataFrame the tool's DB fetcher produces.
`frozen_today` is the value `date.today()` is patched to during the run,
so `start_date` and `cutoff` are reproducible.

## Inputs are synthetic, not real Bloomberg data

The fixtures use deterministic synthetic data — a mean-reverting random
walk with a fixed RNG seed.  This is intentional:

1. The parity test's job is to detect *math drift* between commits.  It
   does not need to validate against Bloomberg WIRP or real markets.
2. Synthetic data is reproducible without a live DB and without
   committing market data into git.
3. The data is realistic enough to exercise every branch: rolling
   z-score buffer, daily-change calculation, ffill across simulated
   holidays, both legs of the spread populated.

The values inside `expected_output` reflect the synthetic input — they
have no economic meaning.

## When to regenerate

Regenerate ONLY after a deliberate methodology change (e.g. moving a
convention's value, changing the rounding precision, switching from
sample-std to population-std for z-scores).  In those cases:

1. Make the methodology change in code.
2. Run `python tests/fixtures/curve_spread_v1/_capture.py` to refresh
   all fixtures.
3. Inspect the diff — make sure the change is what you intended.
4. Commit the regenerated fixtures alongside the code change in the
   *same* PR, with the rationale in the commit message.

NEVER regenerate the fixtures to "make the test pass" after an
unintended change.  That defeats the entire purpose.

## Running the parity test

```
pytest tests/test_curve_spread_parity.py -v
```

Three test cases run, one per fixture file.  All three must pass for
any commit that touches `calculate_curve_spread` or its primitives.
