# `scan_policy_futures_extremes_v1` parity fixtures

Snapshots of the **real production output** of
`calculate_scan_policy_futures_extremes` captured against the live
TimescaleDB. The companion test at
`tests/test_scan_policy_futures_extremes_parity.py` reloads each
fixture, mocks the DB fetchers (replaying captured `raw_price_rows`,
`raw_volume_rows`, `raw_oi_rows`, and `reference_rows`), freezes
`date.today()` to the recorded value, runs the tool, and asserts
the output matches the recorded `expected_output` byte-for-byte
(modulo 1e-9 float tolerance).

## Purpose

PR15 binds **new primitives from day-one** with a parity-fixture
baseline. `scan_policy_futures_extremes` is the eighth and final
primitive in the new `policy_futures` domain (ADR 0013) — the
universe-wide STIR-strip morning extremes scan — so it ships with
parity coverage rather than being grandfathered debt.

The fixture locks in the *current production behaviour* — the exact
output the tool produces on real Bloomberg-shaped universe data —
so any silent drift introduced by a refactor (a subtly altered
rolling-z-score path, a regression in the per-stem intersection
alignment, a change in the four-metric ordering, a different
ranking tie-break, an off-by-one in the universe fetcher, a
broken inverse-pricing conversion) fails the parity test loudly.

The captured baseline includes real-data quirks that synthetic
inputs won't reproduce: per-curve_family holiday calendars (SOFR
trades through Euribor-only holidays and vice versa), days where
one strip's volume or OI reports independently, NaN field values
from the upstream feed, the quarterly roll that rotates each
strip slot's underlying contract.

## Fixture format

Each `*.json` file is a self-contained fixture:

```json
{
  "fixture_name": "full_universe_top3_zfloor",
  "tool_module": "rates_agent.policy_futures.tools.scan_policy_futures_extremes",
  "tool_function": "calculate_scan_policy_futures_extremes",
  "capture": {
    "captured_at": "2026-05-23T19:50:00Z",
    "database_name": "macrodata",
    "raw_price_rows_count": <int>,
    "raw_volume_rows_count": <int>,
    "raw_oi_rows_count": <int>,
    "reference_rows_count": 24,
    "raw_rows_sha256": "abc123...",
    "resolved_curve_families": [
      "SOFR_FUT", "EUR_SHORT_RATE_FUT", "SONIA_FUT"
    ]
  },
  "input": {
    "params": {curve_families, top_n, min_abs_z_score, metrics, as_of_date},
    "frozen_today": "YYYY-MM-DD",
    "raw_price_rows": [{trade_date, curve_family, strip_position, contract_code, field_value}, ...],
    "raw_volume_rows": [...],
    "raw_oi_rows": [...],
    "reference_rows": [{curve_family, contract_code, strip_position, inverse_pricing, ...}, ...]
  },
  "expected_output": {scan_summary, results, methodology_disclosure}
}
```

- **`capture`** is provenance metadata. The parity test ignores it
  for the deep comparison but DOES verify that `raw_rows_sha256`
  matches a fresh recompute over the canonicalised concatenation
  of the four raw-row lists — guards against fixture tampering.
- **`input.params.as_of_date`** is the pinned anchor date the scan
  uses (matches the bond_futures scanner's deterministic-fixture
  precedent — `date.today()` anchoring drifts across regeneration
  runs). The `_capture.py` script writes the pinned date into the
  fixture so re-captures reproduce the same anchor even on a
  different wall-clock day. The current pin is
  `2026-04-08` — the last ingested `trade_date` on the live DB
  snapshot for `instrument_type='policy_future'` PX_LAST when
  this fixture was authored.
- **`input.frozen_today`** is the anchor date the capture ran
  against (tracks `as_of_date` when supplied); the parity test
  patches `date.today()` to this value during replay so a
  hypothetical no-`as_of_date` fixture can still resolve compute's
  default-anchor branch deterministically.
- **`input.raw_price_rows`** / **`raw_volume_rows`** / **`raw_oi_rows`**
  are the long-format DataFrames the tool's
  `fetch_scan_universe_strip_position` fetcher returns for
  `PX_LAST`, `PX_VOLUME`, and `OPEN_INT` respectively. The parity
  test feeds these back into the tool through a mocked fetcher
  with a side-effect that routes by `field_name`.
- **`input.reference_rows`** is the per-stem reference frame the
  tool's `fetch_scan_universe_policy_future_reference` fetcher
  returns — one row per `(curve_family, strip_position)` with
  `inverse_pricing`, master `contract_code`, SCD2-bounded
  `underlying_contract_code`, `expiry_date`, `security_name`,
  `tick_size`, `tick_value`, `contract_size`.
- **`expected_output`** is the full tool output captured from the
  real DB run.

## Generating fixtures (live-DB capture)

The `_capture.py` script must be run against a live TimescaleDB
with the relevant Bloomberg policy-futures strip-position data
loaded (all three fields: PX_LAST, PX_VOLUME, OPEN_INT — across
the policy_futures universe per ADR 0013 V1 + the 24 strip-
position rolling contracts on `instrument_master`). Defaults
match the project's `docker-compose.yml`:

```bash
# 1. Make sure TimescaleDB is up and the policy-futures universe
#    is ingested with all three fields.
docker compose up -d tsdb
# (data ingestion via the usual ingestion/ pipeline must have run)

# 2. Set DB env vars if non-default, then capture.
export DB_HOST=localhost DB_PORT=5433 DB_USER=quantuser \
       DB_PASSWORD=... DB_NAME=macrodata
python tests/fixtures/scan_policy_futures_extremes_v1/_capture.py
```

The default capture set is one fixture
(`full_universe_top3_zfloor`) which pins the full V1 universe scan
with `top_n=3` and `min_abs_z_score=0.0` — small enough that the
fixture is reviewable but large enough to exercise every metric
block AND both regimes (RFR — SOFR/SONIA; IBOR —
EUR_SHORT_RATE_FUT). Add more cases to `_CASES` in `_capture.py`
if multiple configurations should be parity-pinned (e.g. an
RFR-only subset, a high-threshold variant).

After capture, **inspect the diffs in git** before committing:

- `capture.captured_at` will change.
- `input.raw_*_rows` will change if the DB has new market data
  since the last capture.
- `input.reference_rows` will change if the SCD2 history has
  rolled (new front contract, new expiry).
- `expected_output.scan_summary` and `expected_output.results`
  will change to match the new market state.

These changes are expected; they reflect that markets moved and/or
the front contract has rolled. What you should NOT see is
unrelated structural drift in `expected_output` (e.g. metric
blocks appearing or disappearing in the ordering, fields appearing
or disappearing on rows, regime labels flipping between RFR and
IBOR) — that would mean the tool's schema has shifted.

## When to regenerate

Regenerate ONLY when:

1. **The DB has new market data and you want to refresh the
   baseline.** This should be a deliberate decision — once a
   fixture is captured, subsequent commits use it as the immovable
   reference. Refreshing casually defeats the purpose.
2. **You made a deliberate methodology change** (e.g. changed
   `z_score_window_days`, switched sample-std to population-std,
   added a fifth metric per a future ADR) that you want the tool
   to start producing.

In case 2:

1. Make the methodology change in code.
2. Run the capture script to refresh fixtures.
3. Inspect the diff carefully — confirm the change is what you
   intended and nothing else moved.
4. Commit the regenerated fixtures alongside the code change in
   the *same* PR, with the rationale in the commit message.

NEVER regenerate fixtures to "make the test pass" after an
unintended change. That defeats the entire purpose of having a
parity test.

## Running the parity test

```bash
pytest tests/test_scan_policy_futures_extremes_parity.py -v
```

If no fixtures are present (e.g. fresh checkout where the DB
hasn't been captured), the test skips with an explanatory message
rather than failing.
