# `inflation_linkers_scan_inflation_linkers_extremes_v1` parity fixtures

Snapshots of the **real production output** of
`calculate_scan_inflation_linkers_extremes` captured against the live
TimescaleDB.  The companion test at
`tests/test_scan_inflation_linkers_extremes_parity.py` reloads each
fixture, mocks the DB fetchers (replaying captured `raw_field_rows`
and `raw_reference_rows`), freezes `date.today()` to the recorded
value, runs the tool, and asserts the output matches the recorded
`expected_output` byte-for-byte (modulo 1e-9 float tolerance).

## Purpose

PR15 binds **new primitives from day-one** with a parity-fixture
baseline.  `scan_inflation_linkers_extremes` is the first universe-
scan primitive in the `inflation_indexed_bonds` domain (catalog id
`inflation_linkers__scan_inflation_linkers_extremes`, build_order 24)
— the morning real-yield extremes sweep — so it ships with parity
coverage rather than being grandfathered debt.

The fixture locks in the *current production behaviour* — the exact
output the tool produces on real Bloomberg-shaped linker universe
data — so any silent drift introduced by a refactor (a subtly
altered rolling-z-score path, a regression in the per-stem cleaning,
a change in the ranking tie-break, an off-by-one in the universe
fetcher, a methodology disclosure rename) fails the parity test
loudly.

The captured baseline includes real-data quirks that synthetic
inputs won't reproduce: per-country linker holiday calendars (UK
RPI-linked gilts trade through US holidays and vice versa), days
where one curve family reports independently, NaN field values from
the upstream feed, the underlying universe's tenor-coverage
asymmetry (USD_TIPS exposes 5/10/20/30; GBP_LINKER exposes
1/2/3/5/10/15/20/30/50; etc.).

## Fixture format

Each `*.json` file is a self-contained fixture:

```json
{
  "fixture_name": "full_universe_top5_zfloor",
  "tool_module": "rates_agent.inflation_indexed_bonds.tools.scan_inflation_linkers_extremes",
  "tool_function": "calculate_scan_inflation_linkers_extremes",
  "capture": {
    "captured_at": "YYYY-MM-DDTHH:MM:SSZ",
    "database_name": "macrodata",
    "raw_field_rows_count": <int>,
    "raw_reference_rows_count": <int>,
    "raw_rows_sha256": "<hex>",
    "resolved_curve_families": ["USD_TIPS", "GBP_LINKER",
                                "EUR_FR_LINKER", "CAD_RRB"]
  },
  "input": {
    "params": {curve_families, top_n, min_abs_z_score, as_of_date},
    "frozen_today": "YYYY-MM-DD",
    "raw_field_rows": [{trade_date, curve_family, tenor,
                        contract_code, field_value}, ...],
    "raw_reference_rows": [{curve_family, tenor, contract_code,
                            maturity_date, country, vendor_ticker},
                           ...]
  },
  "expected_output": {scan_summary, results, methodology_disclosure}
}
```

- **`capture`** is provenance metadata.  The parity test ignores it
  for the deep comparison but DOES verify that `raw_rows_sha256`
  matches a fresh recompute over the canonicalised concatenation of
  the two raw-row lists — guards against fixture tampering.
- **`input.params.as_of_date`** is the pinned anchor date the scan
  uses.  The `_capture.py` script writes the pinned date into the
  fixture so re-captures reproduce the same anchor even on a
  different wall-clock day.
- **`input.frozen_today`** is the anchor date the capture ran
  against (tracks `as_of_date` when supplied); the parity test
  patches `date.today()` to this value during replay.
- **`input.raw_field_rows`** is the long-format DataFrame the
  tool's `fetch_scan_universe` fetcher returns for
  `instrument_type='inflation_linker'` + `field_name='YLD_YTM_MID'`.
  The parity test feeds this back into the tool through a mocked
  fetcher.
- **`input.raw_reference_rows`** is the per-(curve_family, tenor,
  contract_code) reference rows the new
  `fetch_scan_universe_reference` helper returns
  (`maturity_date`, `country`, `vendor_ticker`).
- **`expected_output`** is the full tool output captured from the
  real DB run.

## Generating fixtures (live-DB capture)

The `_capture.py` script must be run against a live TimescaleDB
with the inflation_indexed_bonds playbook data loaded (the
`YLD_YTM_MID` field across the linker universe).  Defaults match
the project's `docker-compose.yml`:

```bash
# 1. Make sure TimescaleDB is up and the linker universe is
#    ingested with YLD_YTM_MID.
docker compose up -d tsdb
# (data ingestion via the usual ingestion/ pipeline must have run)

# 2. Set DB env vars if non-default, then capture.
export DB_HOST=localhost DB_PORT=5433 DB_USER=quantuser \
       DB_PASSWORD=... DB_NAME=macrodata
python tests/fixtures/inflation_linkers_scan_inflation_linkers_extremes_v1/_capture.py
```

The default capture set is one fixture
(`full_universe_top5_zfloor`) which pins the full V1 linker
universe scan with `top_n=5` and `min_abs_z_score=0.0` — small
enough that the fixture is reviewable but exercises the ranking,
threshold, reference-attach, and disclosure-attach paths.  Add
more cases to `_CASES` in `_capture.py` if additional
configurations should be parity-pinned (e.g. a USD_TIPS-only
subset, a high-threshold variant).

After capture, **inspect the diffs in git** before committing:

- `capture.captured_at` will change.
- `input.raw_*_rows` will change if the DB has new market data
  since the last capture.
- `expected_output.scan_summary` and `expected_output.results`
  will change to match the new market state.

These changes are expected; they reflect that markets moved.
What you should NOT see is unrelated structural drift in
`expected_output` (e.g. fields appearing or disappearing on rows,
the methodology-disclosure template changing) — that would mean
the tool's schema has shifted.

## When to regenerate

Regenerate ONLY when:

1. **The DB has new market data and you want to refresh the
   baseline.**  This should be a deliberate decision — once a
   fixture is captured, subsequent commits use it as the immovable
   reference.  Refreshing casually defeats the purpose.
2. **You made a deliberate methodology change** (e.g. changed
   `z_score_window_days`, switched sample-std to population-std,
   adopted a paired-metric design per a future ADR) that you want
   the tool to start producing.

In case 2:

1. Make the methodology change in code.
2. Run the capture script to refresh fixtures.
3. Inspect the diff carefully — confirm the change is what you
   intended and nothing else moved.
4. Commit the regenerated fixtures alongside the code change in
   the *same* PR, with the rationale in the commit message.

NEVER regenerate fixtures to "make the test pass" after an
unintended change.  That defeats the entire purpose of having a
parity test.

## Running the parity test

```bash
pytest tests/test_scan_inflation_linkers_extremes_parity.py -v
```

If no fixtures are present (e.g. fresh checkout where the DB
hasn't been captured), the test skips with an explanatory message
rather than failing.
