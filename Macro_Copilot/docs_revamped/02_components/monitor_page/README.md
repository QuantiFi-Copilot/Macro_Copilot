# Monitor Page

> The Monitor page shell — `MonitorPage.tsx`, `RatesAgentPage.tsx`, bento grid, widget catalogue. Hosts module Monitor widgets; owns no primitive-specific logic.

**Version:** v1
**Last reviewed:** 2026-05-25
**Status:** load-bearing page-shell contract. Changes to the layout-state schema bump `LAYOUT_VERSION`.
**Operationalises principles:** [FP2](../../00_thesis/03_frontend_thesis.md), [FP12](../../00_thesis/03_frontend_thesis.md), [SI1](../frontend_infrastructure/README.md), P3.
**See also:** [`../frontend_module/README.md`](../frontend_module/README.md), [`../frontend_infrastructure/render_shells.md`](../frontend_infrastructure/render_shells.md#bento-grid-monitor-surface).

---

## What this is

`MonitorPage` is the page shell for `/` (the default Monitor) and `RatesAgentPage` is the analog for `/rates` (rates-scoped Monitor). Both share the bento-grid layout engine; the only difference is the default layout and the data scope.

The composition:

```
┌─────────────────────────────────────────────────────────────┐
│  MonitorHeader  (date, scope, status ribbon)                │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  WidgetGrid                                                 │
│    ┌──────┐  ┌──────┐  ┌────────────┐                       │
│    │ W1   │  │ W2   │  │ W3 (wide)  │                       │
│    └──────┘  └──────┘  └────────────┘                       │
│    ┌──────────────────────────────┐  ┌────┐                 │
│    │ W4 (medium)                  │  │ W5 │                 │
│    └──────────────────────────────┘  └────┘                 │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

The user can add widgets from a catalog modal, resize them within allowed sizes, drag-reorder. The layout persists to localStorage per `LAYOUT_VERSION`.

## The widget catalogue (derived)

`WIDGET_TYPES` (defined in `src/components/monitor/registry.ts`) is the dictionary the bento grid reads. Per FP4, it is *derived* from modules that claim `monitor_surface`:

```ts
import { ALL_PRIMITIVE_MODULES } from '@/modules';

export const WIDGET_TYPES: Record<string, WidgetTypeMeta> = Object.fromEntries(
  ALL_PRIMITIVE_MODULES
    .filter((m) => m.tiers.includes('monitor_surface'))
    .map((m) => [m.toolName, m.monitorMeta!]),
);
```

A module that does not claim `monitor_surface` does not appear in the catalogue. The catalogue is regenerated on every page load (the derivation is pure).

## Per-widget metadata (`MODULE.monitorMeta`)

A module claiming `monitor_surface` sets `MODULE.monitorMeta` to a `WidgetTypeMeta`:

```ts
type WidgetTypeMeta = {
  id: string;                       // = MODULE.toolName
  label: string;                    // human-facing label
  description: string;              // one-line description for the catalog tile
  category: 'data' | 'analysis' | 'anomaly';
  defaultSize: WidgetSize;          // 'small' | 'medium' | 'wide' | 'tall'
  allowedSizes: WidgetSize[];
  parameterized: boolean;
  paramFields?: WidgetParamField[];
  sourceTool: string;               // = MODULE.toolName (for provenance footer)
};
```

The `monitorMeta` field is required if `tiers ∋ monitor_surface`; the round-trip test (FM11) catches the inconsistency.

## How a module contributes to Monitor

| Module declaration | Monitor behaviour |
|---|---|
| `tiers ∋ monitor_surface` AND `MODULE.surfaces.monitor` set AND `MODULE.monitorMeta` set | Appears in the catalog modal; user can add it. Renders via `MODULE.surfaces.monitor` when present in the layout. |
| Default (most modules) | Does not appear in the Monitor catalogue. |

## Default layouts

`defaultMonitorLayout` and `defaultRatesAgentLayout` (in `src/components/monitor/registry.ts`) define the layout a new user sees. They reference widget ids; if a referenced widget doesn't exist (the module was removed), the widget is silently dropped on read (defensive).

Default layouts are hand-curated. A new module claiming `monitor_surface` does NOT automatically appear in the default layout; that's a design decision per module.

## LAYOUT_VERSION

Bumping `LAYOUT_VERSION` invalidates all user layouts in localStorage. Bump only when the layout-state schema is structurally incompatible (renamed fields, new required fields). Adding a new widget type does NOT require a bump.

Per FP4, widget IDs are module tool names. Renaming a tool name = bumping `LAYOUT_VERSION` (the existing layouts referencing the old id will be silently dropped).

## RatesDataProvider

Path: `src/components/monitor/RatesDataProvider.tsx`

Lifted to AppShell level (not MonitorPage-local) so the Sidebar's Today panel + status ribbon reads from it on every widget surface (Monitor + RatesAgent + agent placeholders share the provider).

Provides:
- `data` — aggregated rates data (curve shapes, scanner, yield snapshot, cross-market, regimes).
- `isLoading`, `error`, `refetch`.

A widget that needs more data than the aggregated provider supplies fetches its own (via its own hook), but this is rare — most Monitor widgets are pre-aggregated.

## What MonitorPage does NOT do

- **Branch on widget id in the render loop.** Per FP12; the dispatch is on the widget id key → WIDGET_TYPES lookup.
- **Compute analytical values.** Per FP9; Monitor renders backend values.
- **Define the widget catalogue.** WIDGET_TYPES is derived.

## Open questions

1. **Per-user layout persistence in backend.** Today layouts are local-storage only. Should they sync to a per-user backend store? Today: no — single-user assumption holds; revisit when multi-tenant.
2. **Adaptive default layouts.** Today the default layout is hand-curated. Should it adapt to which modules a user has used most? Today: no — the default is a curated starter; user customisation is the personalisation surface.

## Version log

| Version | Date | Change |
|---|---|---|
| v1 | 2026-05-25 | Initial Monitor page-shell doc. Widget catalogue derivation, monitorMeta requirement, default layouts, LAYOUT_VERSION semantics. |
