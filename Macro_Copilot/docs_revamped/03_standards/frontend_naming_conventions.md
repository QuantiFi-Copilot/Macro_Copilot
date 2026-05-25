# Frontend Naming Conventions

> The canonical naming conventions for the frontend codebase. Counterpart to backend's [`naming_conventions.md`](naming_conventions.md). Strict for module folder names; conventional for everything else.

**Version:** v1
**Last reviewed:** 2026-05-25
**Status:** load-bearing standard for module-folder names; conventional for other artefacts.
**Operationalises principles:** [FP1](../00_thesis/03_frontend_thesis.md), [FM1](../02_components/frontend_module/README.md), P3 (consistency by contract), P10 (single source of truth).
**See also:** [`naming_conventions.md`](naming_conventions.md) — backend analog; [`frontend_file_layout.md`](frontend_file_layout.md) — the layout rules.

---

## Strict rules

| Artefact | Convention | Example | Authority |
|---|---|---|---|
| Module folder name | `snake_case` matching backend `tool_name` EXACTLY | `calculate_cpi_surprise_tool/` | FM1 |
| Workflow module folder name | `snake_case` matching backend `template_id` EXACTLY | `event_study/` | FM1 |
| Module spec file | `module.ts` | — | FM7 |
| Surface files | Per FM8 fixed names | `BuildSurface.tsx`, `MonitorWidget.tsx`, etc. | FM8 |
| Module test file | `module.spec.ts` | — | FM11 |
| THESIS file | `THESIS.md` (uppercase) | — | FM10 |
| Closed-family enum string values | `snake_case`, matching backend identifier conventions | `'generic_runnable'`, `'workflow_incompatible'` | P8 |

Strict rules are mechanically enforced by tests / lints. Violations are CI failures.

## Conventional rules

| Artefact | Convention | Example |
|---|---|---|
| React component | `PascalCase` | `MonitorWidget`, `LibraryPage` |
| Component file | `PascalCase.tsx` | `MonitorWidget.tsx` |
| Hook | `useCamelCase` | `useRatesData`, `useWorkspaceDetail` |
| Hook file | `useCamelCase.ts` | `useRatesData.ts` |
| Service | `camelCase` | `ratesApi`, `workflowsApi` |
| Service file | `camelCaseApi.ts` (or `name.ts` for non-API services like `copilot.ts`) | `ratesApi.ts` |
| Type file | `kebab-case.ts` for new files; existing `camelCase.ts` files retained | `library.ts`, `artifacts.ts`, `common.ts` |
| Type / interface | `PascalCase` | `PrimitiveModuleSpec`, `WidgetTypeMeta` |
| Type alias for closed family | `PascalCase` | `SurfaceTier`, `DagNodeKind` |
| String literal in closed family | `snake_case` | `'generic_runnable'`, `'primitive'` |
| Constant | `SCREAMING_SNAKE_CASE` | `LAYOUT_VERSION`, `DEFAULT_LOOKBACK_PRESETS` |
| Variable / function | `camelCase` | `getPrimitiveModule`, `normalizeToolName` |
| CSS / Tailwind class names | Tailwind utility | `text-fg-primary`, `rounded-md` |
| Test directory | `__tests__/` (with underscores) | — |
| Test file | `<thing>.spec.ts` or `<Thing>.spec.tsx` | `module.spec.ts`, `AutoRenderer.spec.tsx` |
| Storybook file (if used) | `<Thing>.stories.tsx` | `MonitorWidget.stories.tsx` |

Conventional rules are enforced by code review + ESLint where possible. Drift is fixable in the next PR; not a CI blocker.

## Identifier sourcing — backend ↔ frontend

The strictest mirror: backend identifiers used on the frontend are SAME string verbatim. No translation.

| Backend identifier | Frontend usage |
|---|---|
| `tool_name` (e.g. `calculate_cpi_surprise_tool`) | Module folder name, `MODULE.toolName`, every reference |
| `template_id` (e.g. `event_study`) | Workflow module folder name, `MODULE.templateId`, every reference |
| Curve family (e.g. `UST`, `DE_BUND`, `USD_SOFR_OIS`) | `MODULE.defaultParams`, control dropdown values, every reference |
| Tenor (e.g. `10Y`, `5Y`) | Same |
| Sub-agent slug (e.g. `sovereign_bonds`, `inflation_swaps`) | `SUB_AGENT_LABELS` keys, manifest filter values |
| Category slug (e.g. `snapshots`, `curve_shape`) | `CATEGORY_LABELS` keys, manifest filter values |
| Artifact type (e.g. `Series`, `Panel`) | TS type literals, `WIDGET_TYPES` keys for per-type widgets |
| Workflow archetype (e.g. `event_study`, `regime_conditioned_relationship`) | `KNOWN_WORKFLOWS` derivation source |

A frontend "friendly" rename of a backend identifier is forbidden. Friendly display strings live in label maps (`CATEGORY_LABELS`, `SUB_AGENT_LABELS`); the underlying identifier remains the backend's string.

## Display labels — module-level

A module's `MODULE.displayName` is the human-facing string shown in:
- Library card title.
- Build header.
- DAG node chip.

`displayName` is `Title Case`, often the title-cased version of the backend's `tool.name` but with adjustments for readability:

| Backend tool_name | Module displayName |
|---|---|
| `calculate_curve_spread_tool` | `Curve Spread` |
| `calculate_cpi_surprise_tool` | `CPI Surprise` |
| `calculate_pca_yield_curve_tool` | `PCA · Yield Curve` |
| `get_yield_levels_tool` | `Yield Snapshot` |
| `scan_extremes_tool` | `Z-Score Scanner` |

The translation is module-author choice (FM5); the THESIS may document a deliberate divergence from a naive title-case.

## Wire-format field names

Backend wire-format field names are NEVER renamed on the frontend. A backend field `daily_change_bps` is read as `daily_change_bps` on the TypeScript type. The PR14 (backend) wire-format-honesty principle binds the frontend: changing a field name is a backend schema change, not a frontend cosmetic.

The TypeScript types in `src/types/` use the backend's snake_case verbatim:

```ts
type CurveShapeRow = {
  curve_family: string;
  spread_bps: number | null;
  daily_change_bps: number | null;
  z_score: number | null;
  // ... mirrors backend Pydantic field-for-field ...
};
```

Renaming a wire field locally (e.g. mapping `daily_change_bps` to `dailyChangeBps` for "TypeScript idiom") is forbidden — it produces an alternate name, which is P10 violation.

## Forbidden naming patterns

| Pattern | Why forbidden |
|---|---|
| Module folder named with friendly short form (`pca/` instead of `calculate_pca_yield_curve_tool/`) | FM1 |
| Surface file named outside the FM8 fixed list (`MyView.tsx` instead of `BuildSurface.tsx`) | FM8 |
| Wire field renamed to TypeScript-idiom camelCase | P10 |
| Display-string-only label that drifts from backend identifier (a card shows "Italian BTP" when the backend slug is `IT_BTP` and the display string would more honestly read "BTP · Italy") | Cosmetic vs identifier confusion |
| Hook named without `use` prefix | React convention |
| Test file named `<thing>.test.ts` instead of `<thing>.spec.ts` | Stylistic — pick one; we picked `.spec.ts` |
| Type prefix `I` (`IModule`, `IPrimitive`) | TypeScript idiom — types don't need a prefix |

## Open questions

1. **Display-name ergonomics for very long tool names** (e.g. `policy_futures_get_volume_open_interest_snapshot_tool`). The `displayName` field allows aggressive shortening; should there be a standard abbreviation table? Today: no — each module's THESIS documents its display choice.
2. **Should `displayName` be ASCII-only?** Today we allow `·` (middle dot) and other punctuation. Stable for now; revisit if i18n becomes scope.

## Version log

| Version | Date | Change | ADR |
|---|---|---|---|
| v1 | 2026-05-25 | Initial naming conventions. Strict module-folder-name rule, conventional rules for everything else, backend-identifier mirror, forbidden patterns. | [`../05_decisions/0014-frontend-module-architecture.md`](../05_decisions/0014-frontend-module-architecture.md) |
