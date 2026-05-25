# Frontend Registries

> The central registries (`KNOWN_BACKEND_TOOLS`, `RUNNABLE_PRIMITIVE_TOOLS`, `WORKFLOW_INCOMPATIBLE_TOOLS`, `UNSUPPORTED_KNOWN_TOOLS`, `KNOWN_WORKFLOWS`, `PAUSED_WORKFLOWS`, `MODELS`, `WIDGET_TYPES`, `TOOL_TO_VIEW`, node-renderer registry, dashboard registry) — their derivation rules, invariants, and contract.

**Version:** v1
**Last reviewed:** 2026-05-25
**Status:** load-bearing. Changes to a registry's derivation rule require an ADR.
**Operationalises principles:** [FP4](../../00_thesis/03_frontend_thesis.md) (derived registries), [FP5](../../00_thesis/03_frontend_thesis.md) (pure-spec assembly), [FM7](../frontend_module/README.md), [FM12](../frontend_module/README.md), P10 (single source of truth).
**See also:** [`../frontend_module/README.md`](../frontend_module/README.md), [`../frontend_module/tiers.md`](../frontend_module/tiers.md), [`../../05_decisions/0014-frontend-module-architecture.md`](../../05_decisions/0014-frontend-module-architecture.md).

---

## What this folder is

This is the contract that says: **no central registry is hand-authored.** Every registry is a *pure function* over `ALL_PRIMITIVE_MODULES` (and `ALL_WORKFLOW_MODULES` where applicable). The derivation rule for each registry is fixed; the registry's contents follow mechanically from module specs.

The architectural decision is recorded in [`../../05_decisions/0014-frontend-module-architecture.md`](../../05_decisions/0014-frontend-module-architecture.md). This doc is the operational reference: each registry's derivation rule, the invariants it must satisfy, the consumers it serves.

## The closed registry list

| Registry | Lives in | Type | Derived from |
|---|---|---|---|
| `KNOWN_BACKEND_TOOLS` | `src/lib/toolNames.ts` | `ReadonlySet<string>` | `ALL_PRIMITIVE_MODULES.map(m => m.toolName)` (every module's tool name) |
| `RUNNABLE_PRIMITIVE_TOOLS` | `src/lib/toolNames.ts` | `ReadonlySet<string>` | modules where `tiers ∋ generic_runnable` |
| `WORKFLOW_INCOMPATIBLE_TOOLS` | `src/lib/toolNames.ts` | `ReadonlySet<string>` | modules where `tiers ∋ workflow_incompatible` |
| `UNSUPPORTED_KNOWN_TOOLS` | `src/lib/toolNames.ts` | `ReadonlySet<string>` | modules where `tiers ∋ paused` |
| `UNSUPPORTED_KNOWN_REASONS` | `src/lib/toolNames.ts` | `Record<string, UnsupportedKnownReason>` | `{ [m.toolName]: m.unsupportedReason }` for every module with a non-null reason |
| `KNOWN_TOOL_ALIASES` | `src/lib/toolNames.ts` | `Record<string, string>` | from per-module `aliases` field (rare; manifest shorthand vs canonical) |
| `KNOWN_WORKFLOWS` | `src/lib/toolNames.ts` | `ReadonlySet<string>` | workflow modules whose runtime-status tier is neither `paused` nor `deferred` (default-active derivation) |
| `PAUSED_WORKFLOWS` | `src/lib/toolNames.ts` | `ReadonlySet<string>` | workflow modules where `tiers ∋ paused` |
| `MODELS` | `src/lib/modelRegistry.ts` | `ModelMetadata[]` | modules where `richModel === true`; the entry shape is the module's `displayName`, `category`, `oneLineSummary`, `defaultParams`, `paramHints`, `interpretationCards` |
| `TOOL_TO_VIEW` | `src/components/build/primitive/contextDecoder.ts` | `Record<string, PrimitiveViewKind>` | modules where `typedView !== null`; entry is `{ [m.toolName]: m.typedView }` |
| `WIDGET_TYPES` | `src/components/monitor/registry.ts` | `Record<string, WidgetTypeMeta>` | modules where `tiers ∋ monitor_surface`; entry shape from `module.monitorMeta` |
| Node-renderer registry (per-tool entries) | `src/components/build/lib/nodeRendererRegistry.ts` (runtime) | mutable per-tool dict | modules where `tiers ∋ custom_preview_widget`; registered at loader-init from `module.surfaces.preview` |
| Dashboard registry (per-workflow) | `src/components/build/results/dashboards/registry.ts` | runtime dict | workflow modules where `surfaces.results` is defined |

The list is closed. A new registry is added only via ADR.

## Derivation contract

Every registry above is computed by a pure function:

```ts
// Example: RUNNABLE_PRIMITIVE_TOOLS
import { ALL_PRIMITIVE_MODULES } from '@/modules';

export const RUNNABLE_PRIMITIVE_TOOLS: ReadonlySet<string> = new Set(
  ALL_PRIMITIVE_MODULES
    .filter((m) => m.tiers.includes('generic_runnable'))
    .map((m) => m.toolName),
);
```

The derivation is:
- **Pure.** Same input → same output. No mutation. No side effects.
- **Deterministic.** Module ordering in `ALL_PRIMITIVE_MODULES` is stable (alphabetical by toolName per FM12); the registry's iteration order is therefore stable.
- **Total.** Every module that should contribute does. No filtering by additional criteria beyond the tier claim.

## Invariants (mechanically tested)

For each registry, a test asserts:

1. **Every entry corresponds to exactly one module.** A registry entry that doesn't trace back to a module is a derivation-rule bug.
2. **Every module that should contribute does.** A module with `tiers ∋ generic_runnable` MUST appear in `RUNNABLE_PRIMITIVE_TOOLS`.
3. **The registry's count matches the derivation count.** The test snapshot is updated only when modules are added/removed.
4. **No hand-authored entries remain after Stage N.** Grepping for `new Set<string>([` literal arrays in `src/lib/` returns ZERO matches.

These invariants live in `src/lib/__tests__/registry_derivation.spec.ts` and `src/lib/__tests__/no_hand_authored_registries.spec.ts`.

## Cross-side parity (FP6)

A separate check asserts:

```
backend _PRIMITIVE_SPECS.keys()
  ∪ backend WORKFLOW_INCOMPATIBLE_TOOLS.keys()
  ∪ frontend deferred-reservations
==
frontend ALL_PRIMITIVE_MODULES.map(toolName)
```

Implementation: `tools/check_module_parity.py`. Reads the backend's Python source for the registry literals, reads the frontend's compiled module loader (or its source via a regex), asserts set equality.

A backend primitive without a matching frontend module fails CI. A frontend module without a backend tool fails CI. The script runs in the standard test phase.

## Migration boundary

During the migration ([`../../06_roadmap/frontend_migration.md`](../../06_roadmap/frontend_migration.md)):

- **Stage 1** — registries are hand-authored to close the 18-primitive coverage gap; no module folders exist yet.
- **Stage 2** — the first module (`calculate_cpi_surprise_tool`) ships. Its tool name is removed from the hand-authored registry entries; the derivation now includes it.
- **Stage 3+** — each migration PR removes hand-authored entries for its primitive and adds the module folder. The hand-authored count shrinks monotonically.
- **Stage N** — every primitive is a module. Hand-authored entries are deleted. The invariant "no hand-authored entries" begins to bind.

Until Stage N, the test `no_hand_authored_registries.spec.ts` is parameterised with a whitelist (tools still in transition). The whitelist shrinks per migration PR; reaching zero marks Stage N complete.

## Adding a new registry

A new registry is justified when:
- A new derivation rule has emerged (e.g. "modules that claim X tier and Y property").
- An existing registry cannot be adapted (extending it would force unrelated consumers to handle the new shape).

The procedure:
1. ADR documenting the new registry: its name, type, derivation rule, consumers, and why no existing registry suffices.
2. Add a row to this doc's table.
3. Implement the derivation function in `src/lib/` (or the appropriate location).
4. Add the parity test.
5. Wire the consumers.

## Removing a registry

A registry is removed when:
- Its consumers have migrated to a different lookup.
- It was a workaround for a registry-derivation gap now solved upstream.

The procedure is the reverse: ADR, remove the derivation, remove consumers (or migrate them), remove the row from this table.

## Open questions

1. **Library category derivation.** Today `CATEGORY_LABELS` (in `src/types/library.ts`) is hand-authored. Should it derive from manifest data the backend exposes via `/api/v1/library/manifest`? Today: hand-authored — the labels are display strings, not data. Revisit when label drift becomes a recurring failure.
2. **Workflow tier vocabulary.** Workflows share the primitive tier set; the default-active derivation ("any workflow module without `paused | deferred`") avoids adding an `active` tier that would offer no extra information. Revisit when N ≥ 5 workflow modules and the derivation becomes ambiguous in practice.

## Version log

| Version | Date | Change | ADR |
|---|---|---|---|
| v1 | 2026-05-25 | Initial registries doc. 13-registry closed list, derivation contract, invariants, migration-stage boundary, parity check. | [`../../05_decisions/0014-frontend-module-architecture.md`](../../05_decisions/0014-frontend-module-architecture.md) |
