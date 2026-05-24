# Methodology Disclosure

> Methodology must be visible at every layer where a user, reviewer, or replay engine would need to understand why an output exists. The disclosure surface differs by component; the discipline does not.

**Version:** v1
**Last reviewed:** 2026-05-18

## 1. Universal Rule

Every methodology choice the platform makes — a window size, a fill policy, a numerical-stability epsilon, a regime threshold, a financing assumption — is visible in **exactly one** of these places, and never hidden in code:

| Surface | What it carries | Owned by |
|---|---|---|
| Tool `config.yaml` `defaults` block | Primitive-layer methodology defaults | Primitive ([PR7](../02_components/primitive/README.md)) |
| Operator `config.yaml` `defaults` block | Operator-layer methodology defaults | Operator ([OPR7](../02_components/operator/README.md)) |
| Artifact structural metadata | Units, frequency, missingness policy — every metadata field that affects downstream operator compatibility | Artifact ([ART8](../02_components/artifact/README.md)) |
| Lineage step `params` | Every per-call choice (caller-supplied + bound from caller-tunable slot) | Lineage ([ART10](../02_components/artifact/README.md), [`hash_determinism.md`](hash_determinism.md)) |
| Workflow template `slot_schema` | Caller-tunable methodology surface | Template ([WT8](../02_components/workflow_template/README.md)) |
| Workflow template YAML-locked node `params` | Template-author-locked methodology | Template ([WT8](../02_components/workflow_template/README.md)) |
| `TemplateCard` | LLM-readable summary of all the above | Template ([WT13](../02_components/workflow_template/README.md)) |
| Methodology card (rendered) | User-readable summary of a primitive's / template's methodology choices | UI rendering of `TemplateCard` + tool `methodology.what_it_does` |

A methodology constant in `compute.py` or `operator.py` that should be a knob is a violation regardless of which layer it's in. The design-locked-constant allowance ([OPR7](../02_components/operator/README.md)) covers structural constants that genuinely cannot vary (`SUMMARY_SENTINEL_DATE`, `_OFFSET_ANCHOR`); methodology constants do not qualify.

## 2. Source-tag registry

Every `defaults` entry in any `config.yaml` carries a `source` tag identifying *why* this default was chosen. The tag must be **registered** — one of the canonical values observed across the live catalogue. Today's canonical tags, observed live in `<agent>/<domain>/tools/*/config.yaml` and `shared/operators/*/config.yaml`:

| Tag | Use |
|---|---|
| `industry_standard_<concept>` | A widely-accepted convention (e.g., `industry_standard_252_business_days`, `industry_standard_mid_yield`, `industry_standard_sample_std`, `industry_standard_with_intercept`, `industry_standard_two_sided_95`, `industry_standard_level_slope_curvature`, `industry_standard_daily_changes`, `industry_standard_sovereign_repo_usd_money_market`, `industry_standard_1y_window`, `industry_standard_5y_window`) |
| `bloomberg_field_convention` | The default mirrors a specific Bloomberg field's convention (name the field, not "bloomberg") |
| `trading_day_convention` | Calendar / business-day / settlement convention |
| `numerical_stability_lock` | A value chosen for numerical stability (epsilons, regularization), not domain meaning |
| `most_defensible_proxy_for_<concept>` | A documented proxy when the ideal datum is unavailable (e.g., `most_defensible_proxy_for_GC_repo`) |
| `derived_from_window` | The default is computationally derived from another `defaults` field |
| `operator_v1_default` | The substrate's v1 default for an operator-level choice, pending review |
| `methodology_judgement_pending_review` / `team_judgment_pending_review` | An explicit "this is a judgment call we have not yet ratified" marker |
| `legacy_default_pre_pilot` | A pre-pilot default kept for compatibility; flagged for review |
| `<primitive_name>_primitive_v1` | A primitive-specific default the substrate honours (e.g., `rolling_regression_primitive_v1`) |
| `adr_<N>_<concept>` | The convention's authoritative source is a specific ADR; tag names the ADR number and the concept (e.g., `adr_0007_otr_canonicalisation` — the (country, tenor) canonicalisation rule established by ADR 0007's resolver design). Use when a convention exists *because* a specific ADR fixed it. |

New tags require:
1. An entry in this table (one line).
2. The tag actually used in at least one live `config.yaml`.
3. Mention in the PR description.

Today the registry is documented here; a future ADR may move enforcement into [`shared/config/lint.py`](../../shared/config/lint.py) (today the lint validates schema shape but does not yet enforce a tag enum).

## 3. Why This Exists

- **[P5](../00_thesis/01_non_negotiables.md) (honest disclosure).** A user reading a terminal artifact must be able to walk back to every methodology choice that produced it. Hidden constants break replay-grade reproducibility.
- **[P10](../00_thesis/01_non_negotiables.md) (single source of truth).** A methodology default that exists in two places (a YAML and a code constant) drifts. The YAML wins; the code reads from YAML.
- **Cross-component drift detection.** When `ffill_limit_days` exists in two primitives' YAML with different values, the substrate's lint flags it. When the value lives in code, lint cannot see it.

## 4. Component Manifestations

| Component | Disclosure surface | Hidden-constant boundary |
|---|---|---|
| Primitive | `config.yaml` `conventions` / `defaults` ([PR7](../02_components/primitive/README.md)) | A constant in `compute.py` is allowed only when it's mathematical truth (e.g., `2 * pi`) or trivially structural (e.g., a fixed column-rename map) |
| Operator | `config.yaml` `defaults` + design-locked constants in `operator.py` ([OPR7](../02_components/operator/README.md)) | Design-locked constants need rationale + migration pointer in the docstring; documented examples: `SUMMARY_SENTINEL_DATE`, `_OFFSET_ANCHOR`, `_FIXED_DDOF` |
| Artifact | Structural metadata fields ([ART8](../02_components/artifact/README.md)) | Validators raise on missing metadata at construction |
| Workflow template | `slot_schema` (caller-tunable) + YAML-locked `params` (author-locked) ([WT8](../02_components/workflow_template/README.md)) | A constant in `params` that should be a slot is the canonical anti-pattern |
| LLM-facing card | `TemplateCard` ([WT13](../02_components/workflow_template/README.md)) | Card is derived; templates cannot suppress fields |

## 5. Anti-Patterns

- **A `WINDOW_DAYS = 252` constant in `compute.py`** when 252 is a methodology choice. Move to `config.yaml` `defaults`; pass through `*Input`.
- **A `source: default`** / `source: standard` / `source: convention` / `source: tbd` / empty tag. Vague tags are auto-reject.
- **A `source: bloomberg`** without naming the specific Bloomberg field convention. "bloomberg" is the vendor; the tag names the convention (e.g., `bloomberg_field_convention`).
- **A methodology choice that lives only in the lineage step's `params` but not in the producer's `config.yaml`** — the choice is replay-recorded but the producer's surface lies about its defaults.
- **A template `params` value like `window_days: 252`** when callers should be able to override. Make it a slot ([WT8](../02_components/workflow_template/README.md)).
- **An operator constant `_TWO_SIDED_95_Z = 1.96`** with no rationale / migration comment. Either move to `config.yaml` `defaults` (`industry_standard_two_sided_95`) or document the design-lock under OPR7.
- **Two `config.yaml` files defining the same convention name with different values** without rationale. Either align (preferred) or use a distinct name + document the difference. The lint will flag drift; running it before commit catches this.

## 6. Reviewer Checks

- [ ] Every new methodology choice surfaces in `config.yaml` `defaults` (primitive / operator) or `slot_schema` (template) — not as a code constant.
- [ ] Every `defaults` entry has a `source` from the canonical tag list (section 2).
- [ ] A `source` of `methodology_judgement_pending_review` is accompanied by a one-line note about when review is expected.
- [ ] No `compute.py` / `operator.py` constant is a hidden methodology choice. Mathematical truths and design-locked constants (per OPR7) are exempt — they carry rationale in a docstring.
- [ ] `python -m shared.config.lint` passes (cross-component convention-drift check).
- [ ] If the PR adds a new tag, it's added to section 2 of this file with a one-line use description.

## 7. Links

- [P5 (honest disclosure)](../00_thesis/01_non_negotiables.md), [P10 (single source of truth)](../00_thesis/01_non_negotiables.md)
- [`hash_determinism.md`](hash_determinism.md) — lineage `params` is where per-call methodology lives in the hash recipe
- Component manifestations: [PR7](../02_components/primitive/README.md), [PR12](../02_components/primitive/README.md) (registered sources), [OPR7](../02_components/operator/README.md), [OPR12](../02_components/operator/README.md), [ART8](../02_components/artifact/README.md), [WT8](../02_components/workflow_template/README.md), [WT13](../02_components/workflow_template/README.md)
