# Closed-Family Discipline

> The platform's type-algebra integrity rests on closed families: a fixed enum / tuple / discriminated-union whose membership the substrate can reason about. Extension is ADR-gated. There is a critical distinction between **open catalogues within closed families** (the everyday case) and **closed-family extension itself** (rare and heavy).

**Version:** v1
**Last reviewed:** 2026-05-18

## 1. Universal Rule

A **closed family** is a set the substrate enumerates and validates against:

```python
# Example: shared/workflow/registry.py
ARTIFACT_TYPE_NAMES: tuple[str, ...] = (
    "Series", "SeriesSet", "EventSet", "Panel", "WindowedPanel", "TradeSet",
)
```

Three rules govern every closed family:

1. **One source of truth.** The enum / tuple / `Literal` lives at exactly one address; every consumer reads from there.
2. **Validators reject non-members loudly.** A value outside the family raises (per [`error_handling.md`](error_handling.md)), never falls back to a default.
3. **Adding a member is ADR-gated.** An entry in the closed family touches every consumer (validator, executor, persistence, UI, tests); admission requires a documented decision and a multi-site landing in one PR (or an explicit prerequisite chain).

The opposite is an **open catalogue**: a set whose members the substrate accepts freely, but each member still follows a fixed contract. The catalogue is open; the contract is not.

## 2. Open catalogue vs closed-family extension

This distinction is what makes the platform extensible without breaking. Every component layer has both:

| Layer | Closed family (members) | Open catalogue (instances) |
|---|---|---|
| Artifacts | `ARTIFACT_TYPE_NAMES` (6 types) | n/a — artifacts are types, not instances |
| Operators | `shared/operators/` is the catalogue, but it is registered via `OPERATOR_REGISTRY` (a closed dict — adding an operator updates it) | Adding an operator name to `OPERATOR_REGISTRY` is a substrate addition, not a closed-family extension; the operator contract (OPR1–OPR16) is fixed |
| Primitives | `Convention` enum + `Bucket` taxonomy (closed) | Tool catalogue per agent (open) — primitives proliferate within a fixed bucket / convention contract |
| Workflow archetypes | `WORKFLOW_ARCHETYPES` (5 archetypes) | Templates per archetype (open) — multiple sibling templates per archetype are the post-v1 norm |
| Workflow templates | n/a — templates are catalogue entries | Template registry (open) |
| Slot types | `Literal["str", "int", "float", "bool", "dict", "list"]` (6 types) | Per-template slot schemas (open) |
| Slot constraints | `SlotConstraint` union (today only `RelativeOrderConstraint`) | Per-template constraint instances (open) |
| Structural metadata enums | `TimeSeriesUnits`, `MissingnessPolicy` (each closed) | Per-artifact values from those enums (open) |
| Methodology source tags | the canonical tag registry in [`methodology_disclosure.md`](methodology_disclosure.md) | Per-tool `defaults[].source` references (open) |
| Lineage step kinds | `Literal["primitive", "operator", "adapter", "fetch"]` (4 kinds, plus `"clean"`) | Per-call step instances (open) |
| Hash recipe | `_compute_step_hash` recipe — kind/name/version/params/input_hashes (closed; see [`hash_determinism.md`](hash_determinism.md)) | Per-step hashes (open) |

The everyday case is open-catalogue extension: a new template, a new primitive, a new operator. The rare case is closed-family extension: a new archetype, a new artifact type, a new slot type, a new metadata enum value. The two have qualitatively different procedures.

## 3. Why This Exists

- **[P8](../00_thesis/01_non_negotiables.md) (closed-family discipline).** Type-algebra integrity at every layer. The substrate cannot reason about composability if any consumer can invent a new artifact type / archetype / slot type at will.
- **Bounded review surface.** A closed family lets a reviewer audit *all* members at one address. Knowing that `ARTIFACT_TYPE_NAMES` has exactly six entries is what makes the validator's job tractable.
- **Cross-layer consistency.** When `Series` appears in `ARTIFACT_TYPE_NAMES`, the discriminator union, the `artifact_type_name()` type-map, the artifact-store codec, and the executor's `TerminalArtifact` union, they all read from one place. Drift between them would break either persistence or dispatch silently.

## 4. Component Manifestations

| Component | Closed-family principle(s) | Extension procedure |
|---|---|---|
| Primitive | [PR8](../02_components/primitive/README.md) (closed `Convention` surface) | Open catalogue inside the convention; closed family is the convention itself |
| Operator | [OPR3](../02_components/operator/README.md) (residence under `shared/operators/`), [OPR4](../02_components/operator/README.md) (promotion rule = sibling-template equivalent) | Operator additions update `OPERATOR_REGISTRY`; the operator contract is fixed |
| Artifact | [ART2](../02_components/artifact/README.md) (closed `ARTIFACT_TYPE_NAMES`), [ART4](../02_components/artifact/README.md) (ADR-gated extension), [ART12](../02_components/artifact/README.md) (closed metadata enums) | Eight-site landing per [artifact runbook](../02_components/artifact/runbook.md) |
| Workflow template | [WT2](../02_components/workflow_template/README.md) (closed `WORKFLOW_ARCHETYPES`), [WT4](../02_components/workflow_template/README.md) (ADR-gated archetype extension), [WT5](../02_components/workflow_template/README.md) (sibling templates within archetype), [WT9](../02_components/workflow_template/README.md) (closed slot-type taxonomy) | Path B in the [workflow_template runbook](../02_components/workflow_template/runbook.md) |
| Lineage | hash recipe is itself closed-family (see [`hash_determinism.md`](hash_determinism.md)) | Recipe changes are the heaviest closed-family extension in the platform |

## 5. Anti-Patterns

- **A parallel enum.** Defining `class PanelUnits(Enum)` next to `TimeSeriesUnits` because "Panel needs slightly different units". Extend the existing enum centrally.
- **A new artifact-shaped class outside `shared/artifacts/`.** Bypassing `ARTIFACT_TYPE_NAMES`; validator rejects at execution time, but the silent compile-time pass is a footgun.
- **A new archetype admitted "for one analysis".** The five existing archetypes cover the breadth of v1 analyses; a sixth needs the WT4 procedure.
- **A slot type added by string interpolation.** `type: "datetime"` in a `template.yaml` is loader-rejected; closed `Literal[...]` is enforced.
- **Branching at runtime on an open enumeration.** `if op == "regime_conditioned_relationship": ...` inside a template is two templates badly compressed (see [WT5](../02_components/workflow_template/README.md)).
- **A "framework template" meant to be extended.** Templates are leaf catalogue entries; abstraction-by-extension is an anti-pattern at the workflow layer.
- **Half-landed closed-family extension.** An entry in `ARTIFACT_TYPE_NAMES` without the matching Pydantic class, without the discriminator update, without the codec, without a consumer. The substrate is silently broken until the rest lands.

## 6. Reviewer Checks

- [ ] If the PR adds a member to a closed family, does it update *every* site that reads the family? (Per the matching runbook's "all N sites land together" rule.)
- [ ] If the PR adds an instance to an open catalogue (a primitive, an operator, a template), does it follow the component's contract (PR/OPR/WT principles)?
- [ ] If the PR introduces a new enum / Literal / tuple, is it deliberately closed-family? If yes, document the admission procedure in the relevant component contract; if no, justify why an open list is the right shape.
- [ ] No parallel enums (search for newly added enums; check whether an existing closed family covers the use case).
- [ ] An ADR exists for any change to `ARTIFACT_TYPE_NAMES`, `WORKFLOW_ARCHETYPES`, `SlotDeclaration.type`, `SlotConstraint` union, `TimeSeriesUnits`, `MissingnessPolicy`, or the `_compute_step_hash` recipe.

## 7. Links

- [P8 (closed-family discipline)](../00_thesis/01_non_negotiables.md), [P10 (single source of truth)](../00_thesis/01_non_negotiables.md)
- [`hash_determinism.md`](hash_determinism.md) — the lineage hash recipe is the heaviest closed family
- [`methodology_disclosure.md`](methodology_disclosure.md) — source tags are a documented closed registry
- Component manifestations: [PR8](../02_components/primitive/README.md), [OPR3](../02_components/operator/README.md), [ART2](../02_components/artifact/README.md), [ART4](../02_components/artifact/README.md), [ART12](../02_components/artifact/README.md), [WT2](../02_components/workflow_template/README.md), [WT4](../02_components/workflow_template/README.md), [WT5](../02_components/workflow_template/README.md), [WT9](../02_components/workflow_template/README.md)
