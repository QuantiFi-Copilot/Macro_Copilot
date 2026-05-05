# Workflow-Template Gauntlet (manual eval)

Drives the manual end-to-end testing of the LLM workflow-template
routing + execution layer shipped in PR 9 (PR title: "LLM template
surface + Track A eval").

This is a **temporary, root-of-repo** scratchpad — once the routing
quality clears the Phase 0 thresholds for both V1 archetypes
(event_study, regime_conditioned_relationship), this file should
either be moved into `tests/` as a frozen eval input or deleted.

## How to run

Two stages, in order.

### Stage 1 — routing only (no execution)

Goal: spot-check that the LLM picks the right `template_id` and binds
the right slot values BEFORE executing anything.

```bash
# Activate your venv with langchain-anthropic + ANTHROPIC_API_KEY set.
python -m rates_agent.workflows.cli route "<prompt>"
```

The CLI prints a JSON `WorkflowRouteDecision`:

```json
{
  "action": "route",
  "template_id": "event_study",
  "slot_values": { ... },
  "rationale": "...",
  "clarification_question": null,
  "adjustments": []
}
```

Score each prompt against the **expected entry below**:

- ✅ **template selection** = the chosen `template_id` matches the
  expected one
- ✅ **parameter binding** = every key in the prompt's "expected slot
  subset" is present in `slot_values` AND its value matches (numeric
  tolerance 1e-6; strings exact; dicts as a SUBSET deep-comparison)

A `clarification_question` instead of a route counts against
template-selection for that prompt.

### Stage 2 — route + execute

Goal: prove every successfully-routed prompt actually executes.

```bash
python -m rates_agent.workflows.cli run "<prompt>"
```

By default this uses synthetic DB fetchers (Q1 / Q2 canonical
binding fixtures), so no live TimescaleDB is needed.  The CLI
prints both the routing decision AND the execution envelope:

```json
{ "ok": true, "template_id": "event_study", "terminal_artifact": {...},
  "workflow_lineage_summary": "..." }
```

- ✅ **execution success** = `"ok": true` for every prompt that the
  router returned `action: "route"` for

### Phase 0 acceptance thresholds

Score after running every prompt below:

| Metric | Threshold |
|---|---|
| Template selection | ≥ 85% |
| Parameter binding  | ≥ 80% |
| Execution success on bound templates | 100% |

If thresholds miss, iterate the system prompt in
`orchestrator/workflow_prompts.py` or each template's `description` /
`archetype_signature` cues in its `template.yaml`.  Re-run the
gauntlet until thresholds hold, THEN merge.

---

## Catalogue snapshot (V1)

Two registered templates.  Reference for the "expected" columns
below.

### `event_study`

Slots:

| Slot | Type | Required | Notes |
|---|---|---|---|
| `signal_tool_name`     | str   | yes | e.g. `calculate_swap_spread_tool`, `calculate_curve_spread_tool`, `calculate_cross_market_spread_tool`, `calculate_ois_curve_spread_tool` |
| `signal_params`        | dict  | yes | Primitive's *Input dict |
| `signal_output_field`  | str   | yes | `time_series_change_zscore` (single-day widening) or `time_series_zscore` (level-stretched) |
| `target_tool_name`     | str   | yes | e.g. `get_yield_levels_tool`, `get_ois_rate_level_tool`, `calculate_swap_spread_tool` |
| `target_params`        | dict  | yes | Primitive's *Input dict |
| `target_output_field`  | str   | yes | e.g. `time_series` for yield_levels |
| `threshold`            | float | yes | `|z|` boundary for events (e.g. 1.5) |
| `post_window`          | int   | no  | Default 5 |

### `regime_conditioned_relationship`

Slots:

| Slot | Type | Required | Notes |
|---|---|---|---|
| `lhs_tool_name`               | str   | yes | Dependent series primitive (e.g. `get_yield_levels_tool`) |
| `lhs_params`                  | dict  | yes |  |
| `lhs_output_field`            | str   | yes | `time_series` |
| `rhs_tool_name`               | str   | yes | Regressor series primitive (e.g. `get_ois_rate_level_tool`) |
| `rhs_params`                  | dict  | yes |  |
| `rhs_output_field`            | str   | yes |  |
| `regime_signal_tool_name`     | str   | yes | e.g. `calculate_ois_curve_spread_tool` |
| `regime_signal_params`        | dict  | yes |  |
| `regime_signal_output_field`  | str   | yes | e.g. `time_series_spread` (BPS) |
| `regression_window`           | int   | yes | e.g. 60 |
| `high_threshold`              | float | yes | Daily-move threshold (BPS).  MUST be ≥ low_threshold (substrate-enforced) |
| `low_threshold`               | float | yes | Daily-move threshold (BPS).  Negative for "flattening" regime |
| `regression_min_periods`      | int   | no  | Default 30 |

---

## Prompts

Each block: prompt → expected `template_id` + key expected slot
values.  The "subset" notation means every listed key MUST be
present + match in the LLM's `slot_values`; the LLM may bind extra
keys (e.g. `lookback_days` inside `signal_params`) without that
counting against the score.

Convention for nested dict comparisons: `signal_params: {curve_family: UST, tenor: "10Y"}`
means the LLM's `signal_params` dict must contain BOTH those keys
with matching values; other inner-dict keys (`lookback_days`, etc.)
are ignored for scoring.

---

### A. `event_study` prompts (single-day-widening / change-zscore semantics)

> Canonical proof Q1 = "single-day widening" event study.  The LLM
> must pick `signal_output_field: "time_series_change_zscore"`
> (NOT `"time_series_zscore"`) for these prompts because the
> question is about a CHANGE event, not a stretched-level event.

**1.** Over the last 5 years, when the 2Y OIS-Treasury spread widens by more than 1.5σ in a single day, what's the average 5-day forward move in the 10Y UST yield, and how does it compare to the unconditional 5-day move?

→ `event_study`
- `signal_tool_name = "calculate_swap_spread_tool"`
- `signal_params ⊇ {"sovereign_curve_family": "UST", "ois_curve_family": "USD_SOFR_OIS", "tenor": "2Y"}`
- `signal_output_field = "time_series_change_zscore"`
- `target_tool_name = "get_yield_levels_tool"`
- `target_params ⊇ {"curve_family": "UST", "tenor": "10Y"}`
- `target_output_field = "time_series"`
- `threshold = 1.5`
- `post_window = 5`

**2.** When the 5Y UST-SOFR swap spread widens by more than 2σ in a single day, what's the average 10-day forward move in the 5Y UST yield?

→ `event_study`
- `signal_tool_name = "calculate_swap_spread_tool"`
- `signal_params ⊇ {"sovereign_curve_family": "UST", "ois_curve_family": "USD_SOFR_OIS", "tenor": "5Y"}`
- `signal_output_field = "time_series_change_zscore"`
- `target_tool_name = "get_yield_levels_tool"`
- `target_params ⊇ {"curve_family": "UST", "tenor": "5Y"}`
- `threshold = 2.0`
- `post_window = 10`

**3.** Show me the average 3-day forward move in 10Y Bunds when the 10Y BUND-ESTR swap spread widens by more than 1.5σ on the day.

→ `event_study`
- `signal_tool_name = "calculate_swap_spread_tool"`
- `signal_params ⊇ {"sovereign_curve_family": "DE_BUND", "ois_curve_family": "EUR_ESTR_OIS", "tenor": "10Y"}`
- `signal_output_field = "time_series_change_zscore"`
- `target_tool_name = "get_yield_levels_tool"`
- `target_params ⊇ {"curve_family": "DE_BUND", "tenor": "10Y"}`
- `threshold = 1.5`
- `post_window = 3`

**4.** Event study: 5-day move in 30Y UST after days when 30Y UST-SOFR swap spread widened more than 1σ.

→ `event_study`
- `signal_tool_name = "calculate_swap_spread_tool"`
- `signal_params ⊇ {"sovereign_curve_family": "UST", "ois_curve_family": "USD_SOFR_OIS", "tenor": "30Y"}`
- `signal_output_field = "time_series_change_zscore"`
- `target_tool_name = "get_yield_levels_tool"`
- `target_params ⊇ {"curve_family": "UST", "tenor": "30Y"}`
- `threshold = 1.0`
- `post_window = 5`

**5.** What's the abnormal 5-day forward move in 10Y Gilts after days when the 10Y GILT-SONIA swap spread widened by more than 2σ in a day?

→ `event_study`
- `signal_tool_name = "calculate_swap_spread_tool"`
- `signal_params ⊇ {"sovereign_curve_family": "UK_GILT", "ois_curve_family": "GBP_SONIA_OIS", "tenor": "10Y"}`
- `signal_output_field = "time_series_change_zscore"`
- `target_tool_name = "get_yield_levels_tool"`
- `target_params ⊇ {"curve_family": "UK_GILT", "tenor": "10Y"}`
- `threshold = 2.0`
- `post_window = 5`

---

### B. `event_study` prompts (level-stretched / level-zscore semantics)

> When the user asks about days the spread IS stretched (level z-score
> high) — NOT widening — the LLM must pick
> `signal_output_field: "time_series_zscore"`.  This is the rarer
> case but lives in the same template surface.

**6.** When the 2Y UST-SOFR swap spread is more than 1.5σ stretched (level z-score), what's the average 5-day forward move in 10Y UST?

→ `event_study`
- `signal_tool_name = "calculate_swap_spread_tool"`
- `signal_params ⊇ {"sovereign_curve_family": "UST", "ois_curve_family": "USD_SOFR_OIS", "tenor": "2Y"}`
- `signal_output_field = "time_series_zscore"`
- `target_tool_name = "get_yield_levels_tool"`
- `target_params ⊇ {"curve_family": "UST", "tenor": "10Y"}`
- `threshold = 1.5`
- `post_window = 5`

**7.** When the UST 2s10s curve is more than 2σ stretched on its level, what's the average 10-day forward move in 30Y UST?

→ `event_study`
- `signal_tool_name = "calculate_curve_spread_tool"`
- `signal_params ⊇ {"curve_family": "UST", "short_tenor": "2Y", "long_tenor": "10Y"}`
- `signal_output_field = "time_series_zscore"`
- `target_tool_name = "get_yield_levels_tool"`
- `target_params ⊇ {"curve_family": "UST", "tenor": "30Y"}`
- `threshold = 2.0`
- `post_window = 10`

**8.** Event study around days when SOFR 2s10s level is more than 1σ stretched: average 5-day move in 10Y UST.

→ `event_study`
- `signal_tool_name = "calculate_ois_curve_spread_tool"`
- `signal_params ⊇ {"curve_family": "USD_SOFR_OIS", "short_tenor": "2Y", "long_tenor": "10Y"}`
- `signal_output_field = "time_series_zscore"`
- `target_tool_name = "get_yield_levels_tool"`
- `target_params ⊇ {"curve_family": "UST", "tenor": "10Y"}`
- `threshold = 1.0`
- `post_window = 5`

**9.** When the BTP-Bund 10Y spread is more than 2σ stretched on its level, what's the average 5-day forward move in 10Y BTPs?

→ `event_study`
- `signal_tool_name = "calculate_cross_market_spread_tool"`
- `signal_params ⊇ {"curve_family_1": "IT_BTP", "curve_family_2": "DE_BUND", "tenor": "10Y"}`
- `signal_output_field = "time_series_zscore"`
- `target_tool_name = "get_yield_levels_tool"`
- `target_params ⊇ {"curve_family": "IT_BTP", "tenor": "10Y"}`
- `threshold = 2.0`
- `post_window = 5`

---

### C. `event_study` prompts — varied target / threshold / window

**10.** Average 5-day forward move in 5Y UST when 2Y UST-SOFR swap spread widens >1σ in a single day.

→ `event_study`
- `signal_tool_name = "calculate_swap_spread_tool"`
- `signal_output_field = "time_series_change_zscore"`
- `signal_params ⊇ {"sovereign_curve_family": "UST", "ois_curve_family": "USD_SOFR_OIS", "tenor": "2Y"}`
- `target_tool_name = "get_yield_levels_tool"`
- `target_params ⊇ {"curve_family": "UST", "tenor": "5Y"}`
- `threshold = 1.0`
- `post_window = 5`

**11.** What's the abnormal 21-day forward move in 10Y UST after a 1.5σ swap-spread widening event in 2Y UST-SOFR?

→ `event_study`
- `signal_tool_name = "calculate_swap_spread_tool"`
- `signal_output_field = "time_series_change_zscore"`
- `signal_params ⊇ {"sovereign_curve_family": "UST", "ois_curve_family": "USD_SOFR_OIS", "tenor": "2Y"}`
- `target_tool_name = "get_yield_levels_tool"`
- `target_params ⊇ {"curve_family": "UST", "tenor": "10Y"}`
- `threshold = 1.5`
- `post_window = 21`

**12.** Conditional vs unconditional 7-day move in 10Y BUND after days when 2Y BUND-ESTR swap spread widens by more than 1.5 standard deviations in one day.

→ `event_study`
- `signal_tool_name = "calculate_swap_spread_tool"`
- `signal_output_field = "time_series_change_zscore"`
- `signal_params ⊇ {"sovereign_curve_family": "DE_BUND", "ois_curve_family": "EUR_ESTR_OIS", "tenor": "2Y"}`
- `target_tool_name = "get_yield_levels_tool"`
- `target_params ⊇ {"curve_family": "DE_BUND", "tenor": "10Y"}`
- `threshold = 1.5`
- `post_window = 7`

**13.** Forward 5-day move in 10Y JGB after days when JGB-JPY-OIS swap spread widens >2σ.

→ `event_study`
- `signal_tool_name = "calculate_swap_spread_tool"`
- `signal_output_field = "time_series_change_zscore"`
- `signal_params ⊇ {"sovereign_curve_family": "JGB", "ois_curve_family": "JPY_OIS", "tenor": "10Y"}`
- `target_tool_name = "get_yield_levels_tool"`
- `target_params ⊇ {"curve_family": "JGB", "tenor": "10Y"}`
- `threshold = 2.0`
- `post_window = 5`

**14.** Show me the abnormal forward 5-day move in 30Y UST when the 10Y UST-SOFR swap spread widens by more than 1σ in a single day.

→ `event_study`
- `signal_tool_name = "calculate_swap_spread_tool"`
- `signal_output_field = "time_series_change_zscore"`
- `signal_params ⊇ {"sovereign_curve_family": "UST", "ois_curve_family": "USD_SOFR_OIS", "tenor": "10Y"}`
- `target_tool_name = "get_yield_levels_tool"`
- `target_params ⊇ {"curve_family": "UST", "tenor": "30Y"}`
- `threshold = 1.0`
- `post_window = 5`

**15.** Event study: 5-day forward move in 5Y BUND after days when DE_BUND 2s10s curve_spread is more than 1.5σ stretched on its LEVEL.

→ `event_study`
- `signal_tool_name = "calculate_curve_spread_tool"`
- `signal_output_field = "time_series_zscore"`
- `signal_params ⊇ {"curve_family": "DE_BUND", "short_tenor": "2Y", "long_tenor": "10Y"}`
- `target_tool_name = "get_yield_levels_tool"`
- `target_params ⊇ {"curve_family": "DE_BUND", "tenor": "5Y"}`
- `threshold = 1.5`
- `post_window = 5`

---

### D. `regime_conditioned_relationship` prompts (canonical curve-regime → yield β)

> Canonical proof Q2 = rolling β of UST 10Y change vs 2Y SOFR OIS
> change in steepening vs flattening 2s10s regimes.  The thresholds
> are MOVE-based (the curve_spread's daily diff in BPS), NOT level-
> based.

**16.** Estimate the rolling beta of the 10Y UST yield change to the 2Y OIS rate change, and report how that beta differs in steepening vs flattening regimes of the 2s10s curve over the last 3 years.

→ `regime_conditioned_relationship`
- `lhs_tool_name = "get_yield_levels_tool"`
- `lhs_params ⊇ {"curve_family": "UST", "tenor": "10Y"}`
- `lhs_output_field = "time_series"`
- `rhs_tool_name = "get_ois_rate_level_tool"`
- `rhs_params ⊇ {"curve_family": "USD_SOFR_OIS", "tenor": "2Y"}`
- `rhs_output_field = "time_series"`
- `regime_signal_tool_name = "calculate_ois_curve_spread_tool"`
- `regime_signal_params ⊇ {"curve_family": "USD_SOFR_OIS", "short_tenor": "2Y", "long_tenor": "10Y"}`
- `regime_signal_output_field = "time_series_spread"`
- `high_threshold > 0` and `low_threshold < 0` (canonical: 3.0 / -3.0)

**17.** How does the rolling beta of UST 10Y yield changes on UST 5Y yield changes differ in steepening vs flattening 5s10s regimes?

→ `regime_conditioned_relationship`
- `lhs_tool_name = "get_yield_levels_tool"`
- `lhs_params ⊇ {"curve_family": "UST", "tenor": "10Y"}`
- `rhs_tool_name = "get_yield_levels_tool"`
- `rhs_params ⊇ {"curve_family": "UST", "tenor": "5Y"}`
- `regime_signal_tool_name = "calculate_curve_spread_tool"`
- `regime_signal_params ⊇ {"curve_family": "UST", "short_tenor": "5Y", "long_tenor": "10Y"}`
- `regime_signal_output_field = "time_series_spread"`
- `high_threshold > 0` and `low_threshold < 0`

**18.** Compare the rolling beta of 10Y BUND yield changes to ESTR 2Y rate changes across steepening vs flattening DE_BUND 2s10s regimes.

→ `regime_conditioned_relationship`
- `lhs_tool_name = "get_yield_levels_tool"`
- `lhs_params ⊇ {"curve_family": "DE_BUND", "tenor": "10Y"}`
- `rhs_tool_name = "get_ois_rate_level_tool"`
- `rhs_params ⊇ {"curve_family": "EUR_ESTR_OIS", "tenor": "2Y"}`
- `regime_signal_tool_name = "calculate_curve_spread_tool"`
- `regime_signal_params ⊇ {"curve_family": "DE_BUND", "short_tenor": "2Y", "long_tenor": "10Y"}`
- `high_threshold > 0` and `low_threshold < 0`

**19.** Run a regime-conditioned regression of 10Y UST changes on 5Y SOFR OIS changes, splitting by steepening vs flattening days of SOFR 2s10s.

→ `regime_conditioned_relationship`
- `lhs_tool_name = "get_yield_levels_tool"`
- `lhs_params ⊇ {"curve_family": "UST", "tenor": "10Y"}`
- `rhs_tool_name = "get_ois_rate_level_tool"`
- `rhs_params ⊇ {"curve_family": "USD_SOFR_OIS", "tenor": "5Y"}`
- `regime_signal_tool_name = "calculate_ois_curve_spread_tool"`
- `regime_signal_params ⊇ {"curve_family": "USD_SOFR_OIS", "short_tenor": "2Y", "long_tenor": "10Y"}`
- `high_threshold > 0` and `low_threshold < 0`

**20.** Rolling 60-day beta of 30Y UST yield change to 2Y SOFR rate change in steepening vs flattening UST 2s10s regimes.

→ `regime_conditioned_relationship`
- `lhs_tool_name = "get_yield_levels_tool"`
- `lhs_params ⊇ {"curve_family": "UST", "tenor": "30Y"}`
- `rhs_tool_name = "get_ois_rate_level_tool"`
- `rhs_params ⊇ {"curve_family": "USD_SOFR_OIS", "tenor": "2Y"}`
- `regime_signal_tool_name = "calculate_curve_spread_tool"`
- `regime_signal_params ⊇ {"curve_family": "UST", "short_tenor": "2Y", "long_tenor": "10Y"}`
- `regression_window = 60`
- `high_threshold > 0` and `low_threshold < 0`

**21.** What's the difference in 90-day rolling beta of 10Y BTP changes to 10Y BUND changes between steep and flat 5s10s BUND regimes?

→ `regime_conditioned_relationship`
- `lhs_tool_name = "get_yield_levels_tool"`
- `lhs_params ⊇ {"curve_family": "IT_BTP", "tenor": "10Y"}`
- `rhs_tool_name = "get_yield_levels_tool"`
- `rhs_params ⊇ {"curve_family": "DE_BUND", "tenor": "10Y"}`
- `regime_signal_tool_name = "calculate_curve_spread_tool"`
- `regime_signal_params ⊇ {"curve_family": "DE_BUND", "short_tenor": "5Y", "long_tenor": "10Y"}`
- `regression_window = 90`
- `high_threshold > 0` and `low_threshold < 0`

**22.** Show me how the rolling beta of 10Y Gilt changes to 2Y SONIA changes shifts between steepening and flattening GBP 2s10s regimes.

→ `regime_conditioned_relationship`
- `lhs_tool_name = "get_yield_levels_tool"`
- `lhs_params ⊇ {"curve_family": "UK_GILT", "tenor": "10Y"}`
- `rhs_tool_name = "get_ois_rate_level_tool"`
- `rhs_params ⊇ {"curve_family": "GBP_SONIA_OIS", "tenor": "2Y"}`
- `regime_signal_tool_name = "calculate_ois_curve_spread_tool"`
- `regime_signal_params ⊇ {"curve_family": "GBP_SONIA_OIS", "short_tenor": "2Y", "long_tenor": "10Y"}`
- `high_threshold > 0` and `low_threshold < 0`

---

### E. `regime_conditioned_relationship` prompts — varied window / threshold

**23.** Compare the rolling 30-day beta of 10Y UST changes to 2Y SOFR changes between days when 2s10s steepens by ≥5bp vs flattens by ≤-5bp.

→ `regime_conditioned_relationship`
- `lhs_tool_name = "get_yield_levels_tool"`, `lhs_params ⊇ {"curve_family": "UST", "tenor": "10Y"}`
- `rhs_tool_name = "get_ois_rate_level_tool"`, `rhs_params ⊇ {"curve_family": "USD_SOFR_OIS", "tenor": "2Y"}`
- `regime_signal_tool_name = "calculate_ois_curve_spread_tool"`
- `regression_window = 30`
- `high_threshold = 5.0`
- `low_threshold = -5.0`

**24.** Rolling 120-day beta of 5Y UST to 2Y SOFR, conditioned on UST 2s10s daily move ≥+10bp vs ≤-10bp.

→ `regime_conditioned_relationship`
- `regression_window = 120`
- `high_threshold = 10.0`, `low_threshold = -10.0`

**25.** Estimate the regime-conditioned beta of 10Y BUND change on 1Y ESTR change with a 60-day rolling window across steepening (>+3bp) vs flattening (<-3bp) DE_BUND 2s10s.

→ `regime_conditioned_relationship`
- `regression_window = 60`
- `high_threshold = 3.0`, `low_threshold = -3.0`

**26.** Beta of 10Y UST changes to 5Y UST changes in regimes where UST 5s10s moves up ≥2bp vs down ≤-2bp; 90-day rolling window.

→ `regime_conditioned_relationship`
- `regime_signal_params ⊇ {"curve_family": "UST", "short_tenor": "5Y", "long_tenor": "10Y"}`
- `regression_window = 90`
- `high_threshold = 2.0`, `low_threshold = -2.0`

**27.** What's the difference in rolling beta of 10Y BTP changes to 10Y BUND changes across BTP-Bund spread tightening vs widening regimes?

> Note: this prompt's regime classifier is a CROSS-MARKET spread,
> not a curve-spread.  Substitute `calculate_cross_market_spread_tool`.

→ `regime_conditioned_relationship`
- `regime_signal_tool_name = "calculate_cross_market_spread_tool"`
- `regime_signal_params ⊇ {"curve_family_1": "IT_BTP", "curve_family_2": "DE_BUND", "tenor": "10Y"}`
- `regime_signal_output_field = "time_series_spread"`
- `high_threshold > 0` and `low_threshold < 0`

**28.** Rolling 60-day beta of 10Y JGB changes to 2Y JPY OIS changes, comparing steepening vs flattening JGB 2s10s regimes.

→ `regime_conditioned_relationship`
- `lhs_params ⊇ {"curve_family": "JGB", "tenor": "10Y"}`
- `rhs_params ⊇ {"curve_family": "JPY_OIS", "tenor": "2Y"}`
- `regime_signal_params ⊇ {"curve_family": "JGB", "short_tenor": "2Y", "long_tenor": "10Y"}`
- `regression_window = 60`

---

### F. `clarify` / `out_of_scope` prompts (the LLM should NOT route)

> The router must decline these.  Score: any non-CLARIFY-or-OUT_OF_SCOPE
> response is wrong.

**29.** Where's SOFR 2Y trading?

→ `out_of_scope` — primitive-only question, not a workflow shape.

**30.** What's the BTP-Bund 10Y spread today?

→ `out_of_scope` — primitive-only question.

**31.** Hi, how are you?

→ `out_of_scope` — not a rates question at all.

**32.** Show me an event study.

→ `clarify` — the user wants event_study but no slot can be inferred
(no signal, no target, no threshold).  The follow-up question should
ask for at least the signal + target + threshold.

**33.** Run a regime-conditioned beta analysis.

→ `clarify` — wants regime_conditioned_relationship but lhs / rhs /
regime_signal / thresholds are all unspecified.

---

### G. Edge cases (`clarify` / substrate constraint enforcement)

> These exercise the substrate's slot-binding + slot_constraints
> guards.  The router's normaliser MUST demote a malformed ROUTE to
> CLARIFY rather than emitting a workflow that would fail at bind.

**34.** Run regime_conditioned_relationship with high_threshold=-5 and low_threshold=+5 on UST 10Y vs 2Y SOFR.

→ `clarify` (NOT `route`) — the substrate enforces
`high_threshold >= low_threshold` as a slot_constraint; the router's
normaliser catches the bind failure and demotes to CLARIFY.

**35.** Event study with threshold "high" on swap_spread.

→ `clarify` — `threshold` is a float slot; "high" is not a valid
binding.  The normaliser's bind dry-run catches the type mismatch
and demotes to CLARIFY.

---

## Scoring template

After running every prompt, fill in:

| Bucket | Total | Correct template_id | Correct params | Executed OK |
|---|---|---|---|---|
| event_study (1–15) | 15 | __ | __ | __ |
| regime (16–28)     | 13 | __ | __ | __ |
| out_of_scope / clarify (29–35) | 7 | __ (correct decline) | n/a | n/a |
| **TOTAL** | 35 | __ / __ | __ / __ | __ / __ |

Acceptance: `correct_template_id / total ≥ 0.85`,
`correct_params / routed_total ≥ 0.80`,
`executed_ok / routed_total = 1.00`.
