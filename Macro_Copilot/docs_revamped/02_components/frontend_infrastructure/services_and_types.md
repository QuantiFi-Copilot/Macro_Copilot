# Frontend Infrastructure — Services and Types

> The REST clients (`src/services/`) and wire types (`src/types/`). The boundary between the frontend and `/api/v1/...`.

**Version:** v1
**Last reviewed:** 2026-05-25
**Status:** load-bearing. Changes to service files or wire types are coordinated with backend API changes.
**Operationalises principles:** [SI5](README.md#si5--service--type-boundary), [SI1](README.md#si1--domain-blind-dispatch), P3 (consistency by contract), P10 (single source of truth).
**See also:** [`README.md`](README.md), backend FastAPI router definitions, the relevant Pydantic schemas under `shared/` and `rates_agent/`.

---

## Services

One service file per backend route family. The mapping:

| Service | Wraps | Used by |
|---|---|---|
| `ratesApi.ts` | `/api/v1/rates/yield-snapshot`, `/api/v1/rates/curve-shapes`, `/api/v1/rates/scanner`, `/api/v1/rates/cross-market`, `/api/v1/rates/regimes`, `/api/v1/rates/detail/{type}` | Monitor widgets, typed primitive views |
| `workflowsApi.ts` | `/api/v1/workflows/catalogue`, `/api/v1/workflows/run`, `/api/v1/tools/{name}` (ToolCard), `/api/v1/tools/{name}/run` (PrimitiveRunResult) | GenericPrimitiveBuilder, WorkflowsCataloguePage, BuilderCanvas |
| `libraryApi.ts` | `/api/v1/library/manifest` | LibraryPage |
| `workspaceApi.ts` | `/api/v1/workspace/{slug}`, `/api/v1/workspace/{slug}/replay`, `/api/v1/artifacts/{hash}/payload`, `/api/v1/artifacts/{hash}/replay`, `POST /api/v1/workspace` | BuildShell, useWorkspaceDetail hook |
| `copilot.ts` | WebSocket `/ws/copilot_chat` | CopilotContext |

### Service file shape

Each service file:
- Defines typed request + response interfaces (or imports them from `src/types/`).
- Exports one async function per endpoint.
- Uses `fetch` directly; no axios / ky / wrapper library.
- Throws on non-2xx responses (callers catch and surface error UI).
- Does NOT depend on React or any React hook.
- Does NOT import from other service files (SI5 boundary).
- Does NOT import from `src/modules/`.

Example skeleton:

```ts
// src/services/libraryApi.ts
import type { ManifestResponse } from '@/types/library';

const BASE = '/api/v1/library';

export async function getManifest(): Promise<ManifestResponse> {
  const r = await fetch(`${BASE}/manifest`);
  if (!r.ok) {
    throw new Error(`getManifest failed: ${r.status} ${r.statusText}`);
  }
  return (await r.json()) as ManifestResponse;
}
```

### Backend-side parity

Every service function corresponds to one backend route. The backend route's request and response shapes (FastAPI Pydantic models) are mirrored in `src/types/`. When the backend route's shape changes, the matching type file is updated in the same coordinated PR.

Drift is detectable: a typed service call that returns malformed data surfaces as a JSON decode error at the call site; a stricter check is provided by running the API's OpenAPI schema against the type files in a periodic CI job (forthcoming).

## Types

One type file per wire-shape family. The mapping:

| Type file | Owns | Imported by |
|---|---|---|
| `rates.ts` | Rates aggregated + typed-detail response shapes (CurveShapeRow, ScannerRow, CrossMarketRow, RegimeRow, YieldSnapshotRow, etc.) | Monitor widgets, typed primitive views |
| `workflows.ts` | `WorkflowCatalogueResponse`, `ToolCard`, `ToolCardEnvelope`, `ToolConventionDescriptor`, `ToolFieldDescriptor`, `PrimitiveRunResult`, `PrimitiveTimeSeriesRow`, `SeriesIndexKind`, `SlotDeclaration` | Workflows page, library drawer, GenericPrimitiveBuilder, BuilderCanvas |
| `library.ts` | `ManifestResponse`, `ManifestTool`, `ManifestAgent`, `CATEGORY_LABELS`, `CATEGORY_TONE`, `SUB_AGENT_LABELS`, `AGENT_LABELS` | LibraryPage and its sub-components |
| `artifacts.ts` | Wire `Series`, `Panel`, `SeriesSet`, `EventSet`, `WindowedPanel`, `TradeSet`, `NamedArtifact`, `ArtifactSummary`, `ArtifactPayloadResponse`, `ArtifactReplayResponse` | AutoRenderer, per-type widgets, workspace replay |
| `copilot.ts` | Closed-family `CopilotMessage` union, workflow result envelope, server-event envelopes | CopilotContext, AskPage, BuildShell |
| `common.ts` | Shared scalar types: `IsoDate`, `Currency`, `TenorString`, `CurveFamily`, primitives like `Bps`, `Percent` | All other type files (the only cross-import allowed) |

### Type file shape

Each type file:
- Uses TypeScript `type` aliases + interfaces only (no classes, no enums except where the backend uses them).
- Re-exports shared scalars from `common.ts` rather than redefining.
- Marks fields the backend treats as optional with `?` (matching the Pydantic `Optional[...]`).
- Documents non-obvious fields with TSDoc comments that link to the backend Pydantic schema.

### Hardcoded label maps (FM5 + FM6)

Some type files carry hardcoded label maps for backend identifiers:

- `CATEGORY_LABELS` — manifest `category` keys → human labels.
- `CATEGORY_TONE` — manifest `category` keys → design-system tones.
- `SUB_AGENT_LABELS` — sub-agent slugs → human labels.
- `AGENT_LABELS` — agent slugs → human labels.

These maps MUST cover every value the backend can emit. The current Library bug (Codex audit finding 3) — `SUB_AGENT_LABELS` only covers `sovereign_bonds` and `ois` — is fixed by extending the map with `inflation_indexed_bonds`, `inflation_swaps`, `bond_futures`, `policy_futures`. Missing labels render as raw snake_case in the UI; that's an FP7 violation (honest disclosure).

Adding a new sub-agent or category requires:
1. Backend ships the new manifest with the new key.
2. Frontend extends `SUB_AGENT_LABELS` / `CATEGORY_LABELS` in the coordinated PR.
3. A test asserts the label map covers every key emitted by the backend (or fails loudly).

## What lives in `src/lib/`

`src/lib/` contains the central registries (toolNames, modelRegistry) and chart utilities. The boundary:

- `lib/` files derive from module specs (per FP4). They do NOT define wire shapes — those live in `types/`.
- `lib/` files import from `types/` and `modules/`; they do NOT import from `services/` (services are I/O, lib is pure).

## What lives in `src/hooks/`

`src/hooks/` contains cross-surface data hooks. Each hook:
- Wraps a service call.
- Owns local state (loading, error, refetch).
- Is read-side per SI6.
- Does NOT contain UI logic (returns data; renderers render).

Examples: `useRatesData`, `useWorkspaceDetail`, `useLibraryManifest`, `useTool` (fetches one ToolCard).

A hook MAY combine multiple service calls when the calls are semantically one read (e.g. `useWorkspaceDetail` fetches both `/workspace/{slug}` and `/workspace/{slug}/replay`). Multiple service calls in one hook is fine; the hook still returns one read-shaped value.

## Open questions

1. **OpenAPI sync.** Today the type files are manually mirrored from the backend Pydantic schemas. Should we generate them from the API's OpenAPI spec? Today: no — manual mirroring is small overhead; generated types add a build step. Revisit when the type-file count exceeds 10.
2. **Service-level retry.** Today services throw on first failure. Should they retry transient 5xx? Today: no — callers (hooks) own retry policy.

## Version log

| Version | Date | Change |
|---|---|---|
| v1 | 2026-05-25 | Initial services + types boundary doc. 5 service files, 6 type files, hook contract, sub-agent label coverage requirement. |
