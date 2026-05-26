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

## The widget catalogue (Stage 4d hybrid)

`WIDGET_TYPES` (defined in `src/components/monitor/registry.ts`) is the dictionary the bento grid reads. Per FP4 it is *derived*, with a small hand-authored carve-out for non-primitive dashboard widgets (today: only `yield_snapshot`, which reads from the dashboard aggregate endpoint rather than any one primitive's typed-detail endpoint):

```ts
import { ALL_PRIMITIVE_MODULES } from '@/modules';

const HAND_AUTHORED_WIDGETS: Record<string, WidgetTypeMeta> = {
  yield_snapshot: { /* ... pre-aggregated dashboard tile ... */ },
};

const MODULE_DERIVED_WIDGETS: Record<string, WidgetTypeMeta> = {};
for (const m of ALL_PRIMITIVE_MODULES) {
  for (const w of m.monitorWidgets ?? []) {
    MODULE_DERIVED_WIDGETS[w.id] = {
      id: w.id,
      label: w.label,
      description: w.description,
      category: w.category,
      defaultSize: w.defaultSize,
      allowedSizes: w.allowedSizes,
      parameterized: w.parameterized,
      paramFields: w.paramFields,
      sourceTool: m.toolName,  // implicit — derived from owning module
    };
  }
}

export const WIDGET_TYPES: Record<string, WidgetTypeMeta> = {
  ...HAND_AUTHORED_WIDGETS,
  ...MODULE_DERIVED_WIDGETS,
};
```

A module that does not declare `monitorWidgets` (and does not set the legacy single-widget `surfaces.monitor`) does not appear in the catalogue. The catalogue is regenerated on every page load (the derivation is pure).

## Per-widget metadata (`MODULE.monitorWidgets`)

A module claiming `monitor_surface` declares one OR MORE `MonitorWidgetMeta` entries on `MODULE.monitorWidgets`. Stage 4d introduced multi-variant support because several primitives ship both a pre-aggregated dashboard tile AND a parameterised single-pair tile from the same backend tool (`calculate_curve_spread_tool` ships `curve_spreads` + `spread_chart`; `calculate_cross_market_spread_tool` ships `cross_market_spreads` + `cross_market_spread`).

```ts
type MonitorWidgetMeta = {
  id: string;                       // unique widget id (key in WIDGET_TYPES + layout state)
  label: string;                    // human-facing catalog label
  description: string;              // one-line catalog tile description
  category: 'data' | 'analysis' | 'anomaly';
  defaultSize: WidgetSize;          // 'small' | 'medium' | 'wide' | 'tall'
  allowedSizes: ReadonlyArray<WidgetSize>;
  parameterized: boolean;
  paramFields?: ReadonlyArray<WidgetParamField>;
  component: ComponentType<any>;    // renderer (lives under surfaces/monitor/<Name>.tsx)
};
```

The legacy single-widget shape (`MODULE.surfaces.monitor: ComponentType<MonitorWidgetProps>`) is still supported for modules that only ship one Monitor variant — the round-trip test (FM11) accepts EITHER `surfaces.monitor` populated OR `monitorWidgets.length > 0`.

## How a module contributes to Monitor

| Module declaration | Monitor behaviour |
|---|---|
| `tiers ∋ monitor_surface` AND `MODULE.monitorWidgets` has ≥1 entries | Each `monitorWidgets[i]` appears as a separate catalog modal tile under its `id`. The registry walker derives `sourceTool = MODULE.toolName` for the provenance footer. |
| `tiers ∋ monitor_surface` AND `MODULE.surfaces.monitor` set (legacy single-widget) | Module contributes one entry; the entry's id, label, etc. come from a hand-authored `HAND_AUTHORED_WIDGETS` record. (No Stage 4d module uses this shape today.) |
| Default (most modules) | Does not appear in the Monitor catalogue. |

## Default layouts

`defaultMonitorLayout` and `defaultRatesAgentLayout` (in `src/components/monitor/registry.ts`) define the layout a new user sees. They reference widget ids; if a referenced widget doesn't exist (the module was removed), the widget is silently dropped on read (defensive).

Default layouts are hand-curated. A new module claiming `monitor_surface` does NOT automatically appear in the default layout; that's a design decision per module.

## LAYOUT_VERSION

Bumping `LAYOUT_VERSION` invalidates all user layouts in localStorage. Bump only when the layout-state schema is structurally incompatible (renamed fields, new required fields). Adding a new widget type does NOT require a bump.

Stage 4d note — widget IDs are PER-WIDGET ids declared inline on each module's `MODULE.monitorWidgets[i].id` (e.g. `curve_spreads`, `spread_chart`, `cross_market_spreads`, `cross_market_spread`, `scanner`, `yield_level`, `curve_classifier`), NOT module tool names.  This decouples the catalog from the backend's tool-naming convention: a module can ship multiple Monitor variants under different ids from the same backend tool, and the layout state survives a tool-name rename as long as the widget ids stay stable.  Renaming a widget id (or splitting one widget into two) does require bumping `LAYOUT_VERSION` — existing layouts referencing the old id are silently dropped on read.

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
