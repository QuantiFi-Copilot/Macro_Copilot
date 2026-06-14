# Methodology Source Tags

Every `Convention.source` string in a tool's `config.yaml` should
reference one of the documented tags below. The tag is a short
identifier; the rationale field on the convention itself carries the
specific justification for that tool. Together they form the
auditable record of why a default value was chosen.

This document is the registry. **Adding a new tag requires a one-line
entry here**, alongside an explanation of what it means and when to
use it.

> Enforcement note (commit 6 of the tool-config pilot): `source` is
> currently validated as a **non-empty string** only. The strict
> enum tightening (where `source` MUST be one of the keys below or
> validation fails) is a planned follow-up once the registry has
> stabilised across multiple migrated tools. Until then, the registry
> is enforced socially — by code review and CI lint — not by code.

---

## Registered tags

### `industry_standard_1y_window`

**Use when**: a numeric default reflects the industry-standard 1-year
rolling window for percentile / z-score / mean-reversion stats.
**Examples**: `z_score_window_days = 252` (trading-day count for one
year). Matches Bloomberg's default rolling window and desk convention
across UST, Bund, Gilt, JGB curves.

### `industry_standard_sample_std`

**Use when**: a default reflects the industry convention of using the
sample standard deviation (Bessel-corrected, `ddof=1`) for finite-
sample rolling z-scores. Matches pandas' default and the convention
the codebase has used since inception. **Examples**: `z_score_ddof =
1`.

### `bloomberg_field_convention`

**Use when**: a string default names a Bloomberg field mnemonic that
is the canonical observation for that asset class. **Examples**:
`default_field_name = "YLD_YTM_MID"` (mid yield-to-maturity is the
canonical sovereign-yield observation), `default_field_name =
"PX_LAST"` (mid par swap rate for OIS).

### `derived_from_window`

**Use when**: a numeric default is mechanically derived from another
convention rather than being a freestanding choice. The rationale
field should make the derivation explicit. **Examples**:
`z_score_buffer_multiplier = 1.5` — calendar-day buffer added to the
fetch window so the z-score is fully populated from the first
displayed trading day; 1.5× the trading-day window covers weekends
and holidays. If the source z-score window changes, this multiplier's
appropriateness should be re-evaluated.

### `team_judgment_pending_review`

**Use when**: a default reflects the team's current best guess but
has not yet been validated against an external reference (Bloomberg
WIRP, a research paper, advisory-firm input, etc.). This tag is
**debt** — the goal is to drive the count down over time as defaults
get validated and re-tagged with their actual source. **Examples**:
threshold-style defaults like `parallel_threshold_bps = 1.0` or
`ffill_limit_days = 5` that were chosen by code-review consensus
during the pilot, not by external validation.

> CI/Engineering note: track the count of `team_judgment_pending_review`
> tags across all `config.yaml` files. Driving it down is a
> deliberate effort, not aspirational. A future commit will add a
> CI-visible counter to the consistency lint.

### `trading_day_convention`

**Use when**: a numeric default expresses the standard trading-day
mapping for a calendar period — i.e. how many post-ffill rows of a
daily series correspond to "1 day", "1 week", or "1 month" of
calendar time on a typical exchange calendar. The value is fixed by
calendar arithmetic plus the desk convention of stepping over the
ffill row, NOT by an industry-standard window (`industry_standard_1y_window`)
nor by a derivation from another convention in the same YAML
(`derived_from_window`). **Examples**: `daily_change_offset_rows = 2`
(iloc[-1] vs iloc[-2] = 1 trading day back, after ffill),
`weekly_change_offset_rows = 6` (1 calendar week ≈ 5 trading days),
`monthly_change_offset_rows = 22` (1 calendar month ≈ 22 trading
days). Used identically across sovereign `yield_levels`, OIS
`rate_level`, OIS `cross_market_spread`, OIS `forward_rate`, and the
linker `real_yield_level` tool.

### `legacy_default_pre_pilot`

**Use when**: a numeric default reflects the literal value the
codebase used before the tool-config pilot made it explicit, AND the
default was carried forward unchanged into the YAML to preserve
runtime behaviour. The tag exists to make the "this is the old hard-
coded number, surfaced into YAML so it can be edited but not
silently changed" pedigree auditable; it is distinct from
`team_judgment_pending_review` (which signals an external-validation
debt) because the value's provenance is the codebase's own pre-pilot
state, not an open question. **Examples**:
`yield_round_decimals = 4` / `z_score_round_decimals = 4` /
`high_low_round_decimals = 4` (all match the legacy hardcoded default
in `shared.analytics.levels.compute_level_metrics`); used identically
across sovereign `yield_levels`, OIS `rate_level`, OIS `forward_rate`,
and the linker `real_yield_level` tool.

---

> Registry-drift remediation (PR12 / P5): the tags below were already
> referenced by shipped `config.yaml` files but had no registry entry.
> Each has been given a one-line "use when" here; a handful of ad-hoc
> one-off tags were re-tagged onto these or onto the existing tags
> above instead of being registered separately (noted inline).

### `display_convention`

**Use when**: a numeric default is a **display-only rounding precision**
(`*_round_decimals`) applied to surfaced metrics and never fed back into
the computation. The convention NAME is deliberately unique per tool so
the cross-config consistency lint (which keys on name) does not force
unrelated tools to share a rounding value. **Examples**:
`fair_value_round_decimals = 2`, `forward_round_decimals = 4`,
`vol_round_decimals = 4`. (Absorbs the former `shared_analytics_default`
tag used for `trailing_range_round_decimals` in the scan tools.)

### `operator_v1_default`

**Use when**: a string default selects the **V1 default branch of a
multi-valued operator/method enum** — the behaviour shipped first while
alternative branches are listed in the rationale (often raising
`NotImplementedError` until a later version). The provenance is "the
chosen default among documented alternatives," not an external
reference. **Examples**: `default_missing_data_policy =
"forward_fill_only"`, `calendar_policy = "business_days"`,
`default_convention = "nominal_breakeven"`, `default_method =
"overnight_index_proxy"` (the most-defensible GC-repo proxy).

### `numerical_stability_lock`

**Use when**: a default exists purely to pin **numerical determinism /
bit-stability** — a solver string, a condition-number or variance-share
threshold, a sign-anchor rule, a near-zero guard, a unit-norm tolerance,
or a trading-day-resolution direction — rather than to express a finance
choice. The value is governed by float64 behaviour and reproducibility,
not market convention. **Examples**: `regression_solver =
"numpy_lstsq_default"`, `condition_number_warning_threshold = 1e10`,
`degenerate_variance_share_threshold = 1e-12`, `sign_anchor =
"lock_pc_long_tenor_positive"`, `min_abs_beta_for_half_life = 1e-6`.

### `industry_standard_yield_changes`

**Use when**: a default specifies that rates statistics are computed on
**yield level changes (first differences)** — the standard rates
convention — rather than percent returns, which are ill-defined near
zero yields. **Examples**: `return_method = "diff"`,
`realized_vol_return_method = "diff"`.

### `industry_standard_daily_changes`

**Use when**: a default sets the **daily** change cadence as the
canonical input frequency for curve-PCA / change-attribution work
(preserves sample size, matches desk RV workflows); coarser frequencies
remain available as per-request inputs. **Examples**:
`default_change_frequency = "daily"`.

### `industry_standard_with_intercept`

**Use when**: a boolean default fixes **with-intercept OLS** as the
regression form (the standard choice); the no-intercept variant is a
structurally different model (a sibling tool), not a config knob.
**Examples**: `add_constant = true`.

### `industry_standard_release_window`

**Use when**: a default sets the trailing **economic-release count** for
a surprise-index z-score (≈ 2 years of monthly releases), matching the
Citi / Bloomberg Economic Surprise convention. Distinct from the
252-trading-day `industry_standard_1y_window`: the unit is releases, not
trading days. **Examples**: `release_z_window = 24`.

### `industry_standard_5y_window`

**Use when**: a default sets a **5-year (≈ 1825 calendar-day) estimation
lookback for a PCA curve-decomposition fit** — the typical desk window
for sovereign-curve PCA work, distinct from the 252-day z-score /
percentile window (`industry_standard_1y_window`). **Examples**:
`default_pca_lookback_days = 1825`.

### `industry_standard_butterfly`

**Use when**: a default encodes the **standard 2× (2:1:1) butterfly
belly weight** — long 2 units of belly, short the wings — against which
the wing weights are solved. **Examples**: `belly_weight = 2.0`.

### `industry_standard_two_sided_95`

**Use when**: a default sets the **two-sided 95% confidence level**, the
standard quant reporting level, for an interval the tool surfaces.
**Examples**: `confidence_level = 0.95`.

### `litterman_scheinkman_1991`

**Use when**: a default reflects the **Litterman & Scheinkman (1991)
three-factor curve decomposition** — the level/slope/curvature factor
count (or factor labels) for PCA-based curve tools. Three components
typically explain >99% of sovereign-curve variance. **Examples**:
`default_n_components = 3`, `n_components = 3`. (Promoted from the
planned list; replaces the ad-hoc `industry_standard_level_slope_curvature`
and `industry_standard_3factor` tags that several PCA tools were using.)

### `iso_day_count_act_360`

**Use when**: a string default selects the **ACT/360 day-count basis** —
the USD money-market / sovereign-repo convention, and the basis of USD
SOFR / EUR ESTR floating legs. **Examples**: `default_day_count_basis =
"act_360"`. (Promoted from the planned list; replaces the one-off
`industry_standard_sovereign_repo_usd_money_market` tag. The `act_365`
sibling — Gilt repo / SONIA / JGB OIS — remains planned below until a
config references it.)

### `desk_convention_default_horizon`

**Use when**: a string default names the **desk's most-quoted default
horizon** for a per-query-overridable curve / carry tool — the canonical
holding or forward period, freely overridable per request. **Examples**:
`default_horizon = "3M"` (carry/roll holding period),
`default_forward_horizon = "1Y"` (forward-curve window). (Consolidates
the former one-off `desk_convention_carry_roll` and
`desk_convention_forward_curve` tags.)

### `desk_convention_vol_regimes`

**Use when**: a default sets a **volatility-regime cut-point or label**
for an unsupervised rates / curve vol-regime tool — the percentile
boundaries that partition conditional / realised vol into
calm/normal/elevated, and the descriptive names attached to the
resulting states. The labels describe observed structure, never a
signal. **Examples**: `vol_calm_percentile_max = 33.0`,
`vol_elevated_percentile_min = 67.0`, `regime_label_elevated =
"elevated"`, `regime_naming_feature = "realized_vol"`.

### `desk_convention_policy_regimes`

**Use when**: a default sets the **strip-slot positions or regime
labels** for the OIS policy-path regime tool — which strip slots define
the front/belly/back of the priced path, the feature regimes are named
by, and the easing/neutral/tightening labels. Descriptive, never a
signal. **Examples**: `strip_front_position = 1`, `strip_belly_position
= 4`, `policy_naming_feature = "strip_slope"`, `policy_label_tightening
= "tightening_priced"`.

### `tool_design_intent`

**Use when**: a string default is a **structural wiring constant fixed
by the tool's design** — e.g. which substrate `instrument_type` universe
a scanner queries — rather than a methodology choice. Changing it would
make it a different tool, not re-parameterise this one. **Examples**:
`ois_scan_instrument_type = "ois_swap"`, `sovereign_scan_instrument_type
= "sovereign_benchmark"`.

### `adr_0007_otr_canonicalisation`

**Use when**: a default pins an **on-the-run / tenor canonicalisation
shape mandated by ADR 0007** (uppercase ISO-country / integer-Y-tenor
slots, per ADR 0007 §4 + 0005 §3); reading against any other casing
returns no rows and the SCD2 history looks empty. **Examples**:
`tenor_canonicalisation = "uppercase_country_integer_y_tenor"`.

### `adr_0008_event_playbook_contract`

**Use when**: a default pins a value to the **economic-event playbook
contract defined in ADR 0008** (`rates_agent/playbooks/economic_releases.yml`)
— the canonical surprise identity, the ingested `event_type` slugs, and
country canonicalisation for the CPI / NFP surprise tools. **Examples**:
`surprise_formula = "actual_minus_consensus_median"`,
`cpi_event_type_for_eu = "hicp_yoy"`, `country = "US"`.

### `adr_0009_wirp_time_series`

**Use when**: a default pins a **WIRP field name or fetch horizon
defined by ADR 0009** (`rates_agent/playbooks/wirp.yml`) — the
metric→`field_name` mapping in `market_data_daily`, the forward / past
calendar horizons, and the supported central-bank set, for the
meeting-pricing tool. Supersedes the planned `bloomberg_wirp_pricing`
tag. **Examples**: `implied_rate_field = "WIRP_IMPLIED_RATE"`,
`forward_horizon_days = 365`, `supported_central_banks =
"FOMC,ECB,BOE,BOJ"`.

---

## Future tags (planned)

These are anticipated but not yet used. Add to the list above with a
proper "use when" rationale before the first config.yaml references
them.

- `bloomberg_wirp_pricing` — originally reserved for WIRP
  meeting-pricing tools once the WIRP-data-backed implementation
  landed. The shipped `wirp_meeting_pricing` tool instead tags its
  WIRP fields/horizons under the ADR-citation tag
  `adr_0009_wirp_time_series` (registered above). This tag is retained
  only for a possible future non-ADR WIRP-probability use; it is not
  referenced by any current config.
- `iso_day_count_act_365` — for OIS / financing day-count on curves
  whose underlying floating index uses ACT/365 (GBP SONIA, JPY OIS,
  AUD AONIA, CAD CORRA). Its ACT/360 sibling is now registered above
  (`iso_day_count_act_360`); promote this one the same way when the
  first ACT/365 config lands.
- `regulatory_pricing_convention` — for any future tool whose
  default reflects a regulator-prescribed methodology (e.g. CCP
  margining, regulatory CVA).

---

## Anti-patterns

A few source tags that should NOT exist:

- **`default`** / **`standard`** / **`convention`** — too vague.
  Whatever the default is, name what makes it standard.
- **`bloomberg`** — too vague unless the convention is specifically
  a Bloomberg field code (use `bloomberg_field_convention`) or a
  WIRP probability output (use `bloomberg_wirp_pricing`).
- **`from_<file_or_paper_name>`** without a date or version — sources
  evolve; cite the version (`litterman_scheinkman_1991`, not
  `litterman_scheinkman`).
- **`tbd`** / **`fixme`** / **`change_me`** — if you don't know yet,
  use `team_judgment_pending_review` so it shows up in the
  remediation count.

---

## Process for adding a new tag

1. Open a PR that adds the tag's entry above (in the registered tags
   section, alphabetically would be ideal but isn't enforced — by
   purpose-grouping is fine).
2. Reference the tag in the relevant `config.yaml`'s `source` field
   in the same PR.
3. The reviewer's job is to confirm the rationale on the convention
   matches the meaning of the tag, and that the tag is named
   precisely enough to be usable elsewhere.
