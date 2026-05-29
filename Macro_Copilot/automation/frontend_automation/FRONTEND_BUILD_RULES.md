# Frontend Build Rules

Step-by-step rules for building one frontend module. This file is the operational walk-through; the contract is in [`STANDARD_MODULE_RULES.md`](STANDARD_MODULE_RULES.md) and the master spec is [`docs_revamped/02_components/primitive/BUILD_GUIDE.md`](../../docs_revamped/02_components/primitive/BUILD_GUIDE.md) Stages 4 → 5 → 6.

**Version:** v1
**Status:** load-bearing build procedure.

---

## Step 0 — Read the inputs

Before any code, read:

1. `BUILD_GUIDE.md` §Stage 4 — Frontend module spec
2. `BUILD_GUIDE.md` §Stage 5 — Frontend bridge endpoint
3. `BUILD_GUIDE.md` §Stage 6 — Frontend surfaces (dual-view + Monitor + Mockups)
4. `frontend_module/README.md` FM1–FM12
5. `frontend_module/thesis_template.md`
6. `rendering_density.md` (the dual-view mandate)
7. `methodology_exposure.md §5` (the standalone bridge)
8. The catalog entry the orchestrator embedded in the builder prompt
9. The **mockup PNGs** at `pre_flight_backend_audit.mockups_required` paths (read as IMAGES)
10. The **reference frontend module** at `reference_frontend_module` path

If any of (8), (9), (10) cannot be read, STOP and surface the blocker.

## Step 1 — Read the backend you're wrapping

Inspect:

- `rates_agent/<sub_agent>/tools/<tool_slug>/schemas.py` — the Pydantic Input + Output you're consuming
- `rates_agent/<sub_agent>/tools/<tool_slug>/config.yaml` — the exposed convention overrides (the `exposure.expose: true` fields become controls in BuildExtended)
- `rates_agent/<sub_agent>/mcp_server.py` — confirm the MCP tool name + the integer-sentinel pattern for exposed overrides
- `database/migrations/*_<tool_name>_curated.sql` — the `theoretical_reference`, `known_limitations`, `desk_narrative` text the methodology card will surface
- `manifesto/03_tool_manifest/rates_agent/<NN>_<sub_agent>_manifest.yml` — the `one_liner` you mirror into `MODULE.oneLineSummary`

The Pydantic Output is your typed contract end-to-end.

## Step 2 — Read the reference module file-for-file

Open `reference_frontend_module` (e.g. `UI/macro-copilot-dashboard-polished/src/modules/primitives/calculate_breakeven_inflation_simple_tool/`) and study:

- `module.ts` — the tier set, the surfaces wiring, the monitorWidgets array, the defaultParams
- `THESIS.md` — the five-question structure + per-surface specificity
- `surfaces/BuildExtended.tsx` — the full canvas layout, controls strip, chart, KPI strip, methodology card
- `surfaces/BuildCompact.tsx` — the grid card, 3 KPIs, sparkline, caveat footer, expand affordance
- `surfaces/breakevenShared.ts` (or equivalent) — the cross-surface helper
- `surfaces/monitor/<Widget>.tsx` — the Monitor tile
- `mockups/Compact.png` + `mockups/Extended.png` — the design source-of-truth for the reference
- `__tests__/module.spec.ts` — the round-trip boilerplate + dual-view checks

Your new module mirrors this shape file-for-file. **Mirror the shape; differ in the content.**

## Step 3 — Create the module folder

```bash
mkdir -p UI/macro-copilot-dashboard-polished/src/modules/primitives/<verb>_<tool_slug>_tool/surfaces/monitor
mkdir -p UI/macro-copilot-dashboard-polished/src/modules/primitives/<verb>_<tool_slug>_tool/__tests__
mkdir -p UI/macro-copilot-dashboard-polished/src/modules/primitives/<verb>_<tool_slug>_tool/mockups
```

The mockups folder already has `Compact.png` + `Extended.png` (the pre-flight verified this). Do NOT overwrite them.

## Step 4 — Write `surfaces/<tool_slug>Shared.ts` first

The shared helper is the single source of truth the three surfaces consume. Write it FIRST so the surfaces can import from it.

Required contents (per `STANDARD_MODULE_RULES.md §5`):

- Data hook `use<X>Data(params)` that calls `fetchDetail<X>` from `ratesApi.ts` and returns shape-normalised data
- Option enumerations (e.g. `<X>_PAIR_OPTIONS: Array<{label, value}>`)
- KPI / descriptor builders
- Caveat strings
- Tone-cue lookups

Pattern: read the reference `surfaces/<slug>Shared.ts` and mirror its sections; replace contents with the new tool's specifics.

## Step 5 — Write `surfaces/BuildExtended.tsx`

The full canvas. Per `rendering_density.md §2.1`:

1. Top bar: tool identity + as-of date
2. Controls strip: dropdowns / sliders for each Pydantic Input field with `exposure.expose: true` on the backend
3. Output canvas:
   - Top-right cards: Z-Score / Percentile / Country-Pair (or equivalent per the mockup)
   - KPI strip (headline numbers + period changes + range stats + the two underlying yields for decomposition tools)
   - Main chart with z-score band overlays
   - Stretch-context panel (if applicable per the mockup)
4. Methodology card: surfaces `current_metrics.methodology_label` from the typed-detail response (NOT hardcoded)
5. Lineage footer: tool name + as-of date + lineage hash if available

Receives `BuildExtendedProps`: `{ toolName, params, decoded, askHandoff? }`.

Calls the shared `use<X>Data` from Step 4.

**Compare to the mockup `Extended.png`** as you write — layout / order / spacing should match.

## Step 6 — Write `surfaces/BuildCompact.tsx`

The grid card. Per `rendering_density.md §2.2`:

1. Tool identity chip (top — kicker)
2. Headline 3 KPIs in the order shown in `Compact.png`
3. Sparkline (small chart, ±2σ z-score bands)
4. Caveat footer (one-line methodology caveat + the parameter chip, e.g. *"US · UST/TIPS"*)
5. Expand affordance (corner button or click area) calling the `onExpand` prop

Receives `BuildCompactProps`: `{ toolName, params, size, onExpand, callMeta? }`.

Calls the SAME shared `use<X>Data` from Step 4 (NOT a smaller endpoint).

**Do NOT:**
- Include the controls strip (editing happens via the expand path)
- Mount your own modal (the `onExpand` prop triggers the shared modal infrastructure)
- Hide methodology entirely (the caveat footer satisfies the disclosure requirement)
- Re-render the entire extended view smaller

**Compare to the mockup `Compact.png`** — the 3 KPIs must be in the same order; the sparkline must be in the same position; the caveat must be in the footer.

## Step 7 — Write `surfaces/monitor/<Widget>.tsx` (if monitor_surface claimed)

Monitor widgets are inherently compact per `rendering_density.md §8`. Mirror the reference's `surfaces/monitor/<Widget>.tsx` shape.

Calls the SAME shared `use<X>Data` from Step 4.

Receives `MonitorWidgetProps` (per `src/modules/types.ts`).

## Step 8 — Write `module.ts`

The pure-spec assembly. Per `STANDARD_MODULE_RULES.md §2`. Imports from `./surfaces/BuildExtended`, `./surfaces/BuildCompact`, `./surfaces/monitor/<Widget>`, `./surfaces/<tool_slug>Shared`.

Wire `MODULE.surfaces = { build: BuildExtended, buildExtended: BuildExtended, buildCompact: BuildCompact }` (the `build` alias is transitional per the pilot tools' inline comment).

Wire `MODULE.monitorWidgets[]` with the widget metadata.

Set `defaultParams` to runnable values that match the backend's expected Input.

## Step 9 — Write `THESIS.md`

Copy `docs_revamped/02_components/frontend_module/thesis_template.md` verbatim and answer all five questions.

**Q1 MUST enumerate BOTH `buildExtended` and `buildCompact` explicitly** (one bullet each). Don't just say "the Build surface".

**Q2 MUST describe what the PM reads off EACH surface** — concrete metrics in the order shown in the mockup. NOT "the user sees the data"; "the user reads breakeven (bps), 1-day change (bps, tone-coloured), z-score regime (Normal / Elevated / Extreme)".

**Q3 MUST justify the compact view's 3 chosen headline KPIs** — why these 3 and not the alternatives.

**Q5 MUST cite by ID** — at least one FM-number, at least one rendering_density.md section, at least one BUILD_GUIDE.md stage.

## Step 10 — Write `__tests__/module.spec.ts`

Mirror the Phase-1 pilot file verbatim. Change `FOLDER` to the new module's folder name. The inline `check(...)` blocks for dual-view + mockups are the same.

## Step 11 — Wire the cross-references

### `src/types/rates.ts`

Add the TS type mirror of the Pydantic Output. One TS interface per Pydantic class. Field names + types MUST match exactly.

### `src/services/ratesApi.ts`

Add `<X>DetailParams` type + `fetchDetail<X>` async function. Hits `/api/v1/rates/detail/<tool_kind>?<qs>`.

### `src/modules/index.ts`

Add the loader entry in ALPHABETICAL position (FM12). The import line + the array entry.

## Step 12 — Add the typed-detail endpoint route

> **`api/routes/rates/detail.py` is a SHARED FILE.** It already contains an APIRouter + 11 endpoints for the Phase-1 pilot tools (`/detail/yield`, `/detail/spread`, `/detail/cross-market`, `/detail/butterfly`, `/detail/regime`, `/detail/breakeven`, `/detail/real-yield`, etc.). You ADD your tool's route + the imports it needs. **You DO NOT replace the file. You DO NOT remove existing endpoints.** Use the existing `@router.get(...)` pattern below.

The catalog entry's `endpoint_slug` is the CURATED short slug — e.g. `breakeven-butterfly`, `zcis-scanner`, `ois-curve-spread`, `policy-futures-price`. The route mounts at `/api/v1/rates/detail/<endpoint_slug>`.

Add to `api/routes/rates/detail.py`:

```python
# ── imports near the top of the file ──
from rates_agent.<sub_agent>.tools.<tool_slug> import (
    CONFIG_PATH as <UPPER_SLUG>_CONFIG_PATH,
    calculate_<tool_slug>,
)
from rates_agent.<sub_agent>.tools.<tool_slug>.schemas import (
    <Tool>Input,
    <Tool>Output,
)

# ── route placed near the other @router.get() handlers ──
@router.get(
    "/detail/<endpoint_slug>",
    response_model=<Tool>Output,
    summary="<Tool Title> Detail (workspace)",
)
def <endpoint_slug_snake>_detail(
    # ── Pydantic Input fields, in declaration order ──
    <instrument_field>: str = Query(...),
    tenor: str = Query(...),
    lookback_days: int = Query(365, ge=30, le=7300),
    field_name: Optional[str] = Query(None),
    # any expose:true convention overrides as Query parameters with None defaults
    engine: Engine = Depends(get_engine),
) -> <Tool>Output:
    """<one-line description mirroring the manifest one_liner>"""
    params = <Tool>Input(
        <instrument_field>=<instrument_field>,
        tenor=tenor,
        lookback_days=lookback_days,
        field_name=field_name,
        # ... other fields ...
    )
    config = load_tool_config(<UPPER_SLUG>_CONFIG_PATH)
    return calculate_<tool_slug>(engine=engine, params=params, config=config)
```

**Notes:**
- The endpoint slug in the URL is **kebab-case** (`breakeven-butterfly`); the function name is **snake_case** (`breakeven_butterfly_detail`).
- The function name uses underscore, NOT hyphen, to be a valid Python identifier.
- `response_model=<Tool>Output` enforces the wire contract via FastAPI/Pydantic.
- The `summary="..."` annotation matches the existing pattern in the file.
- Place the route grouped with related handlers if a natural group exists (e.g. all `/detail/breakeven-*` handlers together).

## Step 13 — Run the pre-commit gate locally

From the repo root:

```bash
cd UI/macro-copilot-dashboard-polished
npm run test:modules
npm run test:build
npm run typecheck
```

ALL THREE must exit 0 before you emit your BUILDER REPORT.

If any fail, fix and re-run. Common failures (with the fix):

- `test:modules` fails on `assertStandardModuleInvariants` — the module folder structure doesn't match FM8. Check: folder name === `MODULE.toolName` EXACTLY (FM1); each surface file at `surfaces/<Name>.tsx` with the FM8 canonical name; surface declared in `MODULE.surfaces` but file missing on disk; surface file present but not declared.
- `test:modules` fails on the dual-view check — `surfaces.buildExtended` or `surfaces.buildCompact` missing in `module.ts`. Fix: populate both, AND include the transitional alias `build: BuildExtended`.
- `test:modules` fails on the mockups check — `mockups/Compact.png` or `mockups/Extended.png` missing. Check the EXACT case of filenames (capital `C` and `E`).
- `test:modules` fails on `typedView` — for new modules `MODULE.typedView` must be `null` (standalone-bridge pattern). Setting any other value triggers the check failure.
- `test:modules` fails on `loaderPresence` — the module is missing from `src/modules/index.ts` (FM12). Add the import + the array entry in alphabetical position.
- `test:build` fails on import resolution — the loader entry in `src/modules/index.ts` points at a non-existent path; OR a surface file imports from a path that doesn't exist (e.g. typo in the shared-helper file name).
- `test:build` fails on TSX parse — usually a missing closing tag, mismatched braces, or an unfinished JSX expression. Check `npm run typecheck` output first (it's usually more specific).
- `typecheck` fails on `types/rates.ts` — TS types don't match the Pydantic Output. Common cause: a field declared as `float` in Pydantic but `string` in TS; or `Optional[float]` in Pydantic but `number` (not `number | null`) in TS. Mirror the Pydantic field-for-field WITHOUT renaming to camelCase.
- `typecheck` fails on `services/ratesApi.ts` — usually a wrong return type on `fetchDetail<X>` (the function MUST return `Promise<<Tool>Output>`).
- `typecheck` fails on a surface — usually a missing prop type (e.g. `BuildCompactProps.onExpand` not being called when the button is clicked) or a wrong destructuring of the typed-detail response.
- `typecheck` fails on `api/routes/rates/detail.py` (NOT TS but the build can flag this) — usually a missing import for the new tool's Input/Output types, or a wrong parameter type in the route signature.

If a failure is structural (e.g. the backend Output shape doesn't match the mockup), STOP and surface it in the BUILDER REPORT — do NOT invent workarounds.

## Step 14 — Emit the BUILDER REPORT

End your work with:

```markdown
## BUILDER REPORT

### Files created
- UI/macro-copilot-dashboard-polished/src/modules/primitives/<verb>_<slug>_tool/module.ts
- UI/macro-copilot-dashboard-polished/src/modules/primitives/<verb>_<slug>_tool/THESIS.md
- UI/macro-copilot-dashboard-polished/src/modules/primitives/<verb>_<slug>_tool/surfaces/BuildExtended.tsx
- UI/macro-copilot-dashboard-polished/src/modules/primitives/<verb>_<slug>_tool/surfaces/BuildCompact.tsx
- UI/macro-copilot-dashboard-polished/src/modules/primitives/<verb>_<slug>_tool/surfaces/<slug>Shared.ts
- UI/macro-copilot-dashboard-polished/src/modules/primitives/<verb>_<slug>_tool/surfaces/monitor/<Widget>.tsx
- UI/macro-copilot-dashboard-polished/src/modules/primitives/<verb>_<slug>_tool/__tests__/module.spec.ts
- (api/routes/rates/detail.py route — if newly added)

### Files updated
- UI/macro-copilot-dashboard-polished/src/types/rates.ts (added <Tool>* types)
- UI/macro-copilot-dashboard-polished/src/services/ratesApi.ts (added fetchDetail<X> + <X>DetailParams)
- UI/macro-copilot-dashboard-polished/src/modules/index.ts (alphabetical loader entry)

### Mockup conformance
The Extended view matches mockups/Extended.png at: layout, KPI ordering, methodology placement, footer.
The Compact view matches mockups/Compact.png at: 3 KPIs in order, sparkline position, caveat footer, expand affordance.
No deviations.

### Test results
$ npm run test:modules → 0
$ npm run test:build → 0
$ npm run typecheck → 0

### Methodology label
Sourced from `current_metrics.methodology_label` in the typed-detail response.
Confirmed: no hardcoded disclosure string in TSX (grep for the manifest one_liner in surfaces/ returns no hits).

### Single-round mode
Dispatch 1 (initial build).
```

If Dispatch 3 (fix pass), replace the Single-round-mode block with:

```markdown
### Single-round mode
Dispatch 3 (fix pass).

### Reviewer findings applied
<list each finding the orchestrator embedded in the prompt, with the file + line that was changed and the rule that was honoured>
```

## Step 15 — Hand back to the orchestrator

The orchestrator parses your BUILDER REPORT (best-effort) and proceeds to Dispatch 2 (reviewer) per `ORCHESTRATOR_PROMPT.md` §"Required loop" step 6.

You are done. Do not loop.
