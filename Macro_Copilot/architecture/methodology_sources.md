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

---

## Future tags (planned)

These are anticipated but not yet used. Add to the list above with a
proper "use when" rationale before the first config.yaml references
them.

- `bloomberg_wirp_pricing` — for meeting-pricing tools once the
  WIRP-data-backed implementation lands.
- `litterman_scheinkman_1991` — for PCA-based curve-decomposition
  tools (number of components, factor labels).
- `iso_day_count_act_360` / `iso_day_count_act_365` — for OIS
  forward-rate day-count conventions, distinguishing curves whose
  underlying floating index uses ACT/360 (USD SOFR, EUR ESTR) from
  those that use ACT/365 (GBP SONIA, JPY OIS, AUD AONIA, CAD CORRA).
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
