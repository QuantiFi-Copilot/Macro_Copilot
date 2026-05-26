# Frontend File and Folder Layout

> The canonical file-and-folder layout for the frontend codebase. Counterpart to backend's [`file_and_folder_layout.md`](file_and_folder_layout.md). Strict; deviations require an ADR.

**Version:** v1
**Last reviewed:** 2026-05-25
**Status:** load-bearing standard. Changes require an ADR in [`../05_decisions/`](../05_decisions/).
**Operationalises principles:** [FP1](../00_thesis/03_frontend_thesis.md), [FP2](../00_thesis/03_frontend_thesis.md), [FM1](../02_components/frontend_module/README.md), [FM8](../02_components/frontend_module/README.md), [FM12](../02_components/frontend_module/README.md), P3 (consistency by contract).
**See also:** [`file_and_folder_layout.md`](file_and_folder_layout.md) — backend analog; [`../01_architecture/02_frontend_architecture.md`](../01_architecture/02_frontend_architecture.md) — high-level layout.

---

## Top-level

```
UI/macro-copilot-dashboard-polished/
├── public/
├── src/
│   ├── main.tsx                       # Bootstrap
│   ├── App.tsx                        # Router + providers
│   ├── index.css                      # Tailwind base + global tokens
│   ├── vite-env.d.ts
│   │
│   ├── modules/                       # L6.1 + L6.2 (per-module folders)
│   │   ├── index.ts                   # Central loader (hand-maintained barrel)
│   │   ├── types.ts                   # PrimitiveModuleSpec, WorkflowModuleSpec, SurfaceTier
│   │   ├── __test-utils.ts            # assertStandardModuleInvariants helper
│   │   ├── primitives/<tool_name>/    # One folder per primitive (FM1)
│   │   └── workflows/<template_id>/   # One folder per workflow template
│   │
│   ├── lib/                           # L6.3 (central registries — derived)
│   │   ├── toolNames.ts
│   │   ├── modelRegistry.ts
│   │   └── chart.ts
│   │
│   ├── components/                    # L6.4 + L6.5
│   │   ├── shared/                    # L6.4 — finance-blind render shells
│   │   │   ├── render/                # AutoRenderer, RichModelWidget, typed views
│   │   │   ├── dag/                   # DagStrip, deriveDagNodes, parseWorkflowLineage
│   │   │   └── bento/                 # WidgetGrid, WidgetCard, WidgetRenderer
│   │   ├── ui/                        # L6.4 — atoms (Sparkline, Card, Modal)
│   │   ├── layout/                    # L6.5 — AppShell, Sidebar, TopNav, ChatDrawer
│   │   ├── build/                     # L6.5 — BuildShell + canvas modes
│   │   ├── library/                   # L6.5 — LibraryPage + chips/strips
│   │   ├── monitor/                   # L6.5 — MonitorPage + bento layout state
│   │   ├── ask/                       # L6.5 — AskPage + Composer + messages
│   │   ├── catalogue/                 # L6.5 — legacy WorkflowsCataloguePage
│   │   ├── agents/                    # L6.5 — RatesAgentPage + placeholders
│   │   └── briefcase/                 # L6.5 — placeholder
│   │
│   ├── context/                       # L6.4 — CopilotContext (singleton WebSocket)
│   ├── hooks/                         # L6.4 — read-side hooks
│   ├── services/                      # L6.4 — REST + WS clients
│   ├── types/                         # L6.4 — wire types
│   └── utils/                         # L6.4 — cn helper, etc.
│
├── package.json
├── tsconfig.json
├── vite.config.ts
├── tailwind.config.ts
└── vitest.config.ts
```

## Per-primitive module folder

```
src/modules/primitives/<tool_name>/        # FM1: folder name = backend tool_name
├── THESIS.md                              # REQUIRED (FM10)
├── module.ts                              # REQUIRED (FM7) — pure spec export
├── surfaces/                              # Per-capability-tier JSX
│   ├── BuildSurface.tsx                   # if tiers ∋ custom_build_surface
│   ├── PreviewWidget.tsx                  # if tiers ∋ custom_preview_widget
│   ├── MonitorWidget.tsx                  # if tiers ∋ monitor_surface AND single-widget legacy shape
│   ├── monitor/                           # if tiers ∋ monitor_surface AND Stage 4d multi-variant shape
│   │   └── <WidgetName>.tsx               # one file per MODULE.monitorWidgets[i].component
│   └── AskCard.tsx                        # if tiers ∋ ask_surface
├── types.ts                               # OPTIONAL — bespoke wire shapes
└── __tests__/
    └── module.spec.ts                     # REQUIRED (FM11) — round-trip
```

## Per-workflow module folder

```
src/modules/workflows/<template_id>/
├── THESIS.md                              # REQUIRED
├── module.ts                              # REQUIRED — pure spec export
├── surfaces/
│   └── ResultsDashboard.tsx               # if tiers ∋ custom_build_surface
├── types.ts                               # OPTIONAL
└── __tests__/
    └── module.spec.ts                     # REQUIRED
```

## Naming

Per [`frontend_naming_conventions.md`](frontend_naming_conventions.md):

| Artifact | Convention | Example |
|---|---|---|
| Module folder name | `snake_case` matching backend tool_name | `calculate_cpi_surprise_tool/` |
| Module file `module.ts` | `module.ts` (singular, lowercase) | — |
| Surface files | `PascalCase.tsx` named exactly per FM8 | `MonitorWidget.tsx` |
| Test file | `module.spec.ts` (singular, lowercase) | — |
| Type files outside modules | `kebab-case.ts` is also acceptable for new files; existing `camelCase.ts` is kept (no churn) | `library.ts`, `artifacts.ts` |
| React components | `PascalCase` default exports | `<MonitorWidget />` |
| Hooks | `useXyz` camelCase | `useRatesData` |
| Services | `camelCase` (e.g. `ratesApi.ts`) | — |

The strict rule: **module folder name MUST equal backend tool_name exactly** (FM1). Other naming is conventions; the module folder name is a contract.

## Import paths

The repo uses TypeScript path aliases (configured in `tsconfig.json`):

| Alias | Resolves to |
|---|---|
| `@/` | `src/` |
| `@/modules` | `src/modules` |
| `@/lib` | `src/lib` |
| `@/components` | `src/components` |
| `@/hooks` | `src/hooks` |
| `@/services` | `src/services` |
| `@/types` | `src/types` |
| `@/utils` | `src/utils` |
| `@/context` | `src/context` |

Per FP12, page-shell files (`src/components/{build,library,monitor,ask,layout}/`) MAY NOT import from `@/modules/primitives/<name>/`. The ESLint custom rule `boundaries/no-module-from-shells` enforces this.

## Test file colocation

Tests live next to the code they exercise:

- Module test → `src/modules/.../<name>/__tests__/module.spec.ts`.
- Shared infrastructure test → `src/components/shared/<area>/__tests__/<Component>.spec.tsx`.
- Page-shell test → `src/components/<page>/__tests__/<Component>.spec.tsx`.
- Hook test → `src/hooks/__tests__/<hook>.spec.ts`.
- Service test → `src/services/__tests__/<service>.spec.ts`.
- Type-level test → `src/types/__tests__/<type-area>.spec.ts`.

Test directories are named `__tests__` (with underscores). Test files end in `.spec.ts` or `.spec.tsx`.

## Generated / build artefacts

| Artifact | Location | Tracked in git? |
|---|---|---|
| `dist/` (production bundle) | repo root | NO |
| `node_modules/` | repo root | NO |
| `coverage/` (vitest) | repo root | NO |
| `.next/`, `.vite/` cache | repo root | NO |
| `package-lock.json` | repo root | YES |
| `tsconfig.tsbuildinfo` | repo root | NO |

## Forbidden patterns

| Pattern | Why forbidden |
|---|---|
| `index.tsx` re-exporting a primitive's surface | FM8: surfaces have fixed file names. Re-exports hide the contract. |
| Two modules sharing a folder | FM2: one module, one backend unit. |
| A module file outside `src/modules/` (e.g. `src/components/build/widgets/PcaPreviewWidget.tsx`) — pre-migration | Legacy; migrated per [`../06_roadmap/frontend_migration.md`](../06_roadmap/frontend_migration.md). |
| `default-export` from a non-surface module file (e.g. `module.ts` default-exporting) | Stylistic: pure values are named exports for explicit imports. |
| `*.d.ts` next to source files (other than `vite-env.d.ts`) | Vite type augmentation belongs in `vite-env.d.ts`; module-specific types belong in `types.ts`. |

## Open questions

1. **Should `src/components/shared/` be promoted to `src/shared/`?** Today shared infrastructure lives under `components/shared/`. A flat `src/shared/` would mirror backend's `shared/` more closely. Today: no — the existing nesting works; promotion would force every consumer to update import paths.
2. **Per-module Storybook stories.** If we adopt Storybook, where do `.stories.tsx` files live? Today's tentative answer: next to the surface they exercise (`surfaces/MonitorWidget.stories.tsx`). Revisit when Storybook lands.

## Version log

| Version | Date | Change | ADR |
|---|---|---|---|
| v1 | 2026-05-25 | Initial frontend file layout. Module folder shape, alias map, naming rules, forbidden patterns. | [`../05_decisions/0014-frontend-module-architecture.md`](../05_decisions/0014-frontend-module-architecture.md) |
