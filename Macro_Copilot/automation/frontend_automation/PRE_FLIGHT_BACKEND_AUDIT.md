# Pre-Flight: Backend Readiness Audit

Before building any frontend module, the orchestrator MUST verify that the backend tool the frontend wraps is fully shipped (BUILD_GUIDE.md Stages 1–3 complete) AND the user has committed the two mockup PNGs into the target frontend module folder.

This is the **frontend-factory equivalent** of the primitive factory's `PRE_FLIGHT_LOAD_AUDIT.md`. Same shape — a per-tool gate, read-only, blocks the dispatch on miss, advances to next tool.

**Version:** v1
**Status:** load-bearing operational policy.

---

## 1. Why this gate exists

The frontend factory builds a typed UI on top of a backend tool. If the backend tool is missing or incomplete, the frontend has nothing to bind to — the builder would either fabricate a Pydantic Output shape (a P5 violation), invent endpoint contracts (silent failure at runtime), or ship a module that fails the `npm run typecheck` gate.

Additionally, the mockup PNGs are the **design source-of-truth** for both the builder and the reviewer. Without them, the builder has no visual contract to render against, and the reviewer has no baseline to score against. A build dispatched without mockups would either invent UI freely (a mockup-first workflow violation) or fail review on visual conformance.

The pre-flight check makes both preconditions explicit + loud rather than implicit assumptions.

## 2. The four checks

For each tool about to be built, the orchestrator runs these four checks IN ORDER. ALL must pass for the tool to advance to builder dispatch.

### Check 1 — Backend tool folder exists with 4-file shape

Per BUILD_GUIDE Stage 1.0, the canonical primitive folder shape is:

```
rates_agent/<sub_agent>/tools/<tool_slug>/
├── __init__.py
├── config.yaml
├── schemas.py
└── compute.py
```

Verify:

```bash
test -f rates_agent/<sub_agent>/tools/<tool_slug>/__init__.py && \
test -f rates_agent/<sub_agent>/tools/<tool_slug>/config.yaml && \
test -f rates_agent/<sub_agent>/tools/<tool_slug>/schemas.py && \
test -f rates_agent/<sub_agent>/tools/<tool_slug>/compute.py
```

The catalog entry's `pre_flight_backend_audit.backend_folder` field names the exact path.

### Check 2 — MCP wrapper is registered

Per BUILD_GUIDE Stage 1E, the MCP wrapper lives in `rates_agent/<sub_agent>/mcp_server.py`. Verify:

```bash
grep -q "<mcp_tool_name>" rates_agent/<sub_agent>/mcp_server.py
```

Where `<mcp_tool_name>` is the catalog entry's `pre_flight_backend_audit.mcp_tool_name` field. This is the **actual Python function name in the mcp_server.py file** — which is normally the same as `backend_tool_name`, but for a few legacy-named tools it diverges.

**The distinction between `backend_tool_name` and `mcp_tool_name`:**

- `backend_tool_name` (catalog top-level field) — the CANONICAL workflow-registry name. This is what the frontend dispatcher routes by, what the FM1 folder is named after, and what the `tool_metadata.tool_name` row uses.
- `pre_flight_backend_audit.mcp_tool_name` (nested field) — the literal Python function name registered with `@mcp.tool()` in the matching `mcp_server.py`. For most tools both fields are identical (e.g. `calculate_breakeven_butterfly_tool`); for two known tools they differ:

| Tool | `backend_tool_name` (canonical / workflow registry) | `mcp_tool_name` (actual function in mcp_server.py) |
|---|---|---|
| inflation_swaps scanner | `scan_inflation_swaps_extremes_tool` | `get_scan_inflation_swaps_extremes_tool` |
| policy_futures price-level | `policy_futures_get_futures_price_level_tool` | `get_futures_price_level_tool` |

Why they diverge: the `inflation_swaps` MCP wrapper was scaffolded with an extra `get_` prefix that doesn't match the registry. The `policy_futures` MCP wrapper uses the unprefixed name locally because the `bond_futures` domain ALSO registers `get_futures_price_level_tool` in its own `mcp_server.py` (a distinct tool on a different instrument family); the workflow registry disambiguates them with the `policy_futures_` prefix. Both inconsistencies are pre-existing backend bugs; the catalog accommodates them rather than papering over them.

A loose `grep` is acceptable here — the orchestrator is just checking the wrapper has been added; the builder will read the actual function signature.

### Check 3 — DB curated metadata is populated OR the migration file exists

Per BUILD_GUIDE Stage 3, the tool's `tool_metadata` row carries the human-curated `theoretical_reference`, `known_limitations`, `desk_narrative`, and `output_field_units` fields. These flow into the per-tool README the frontend's THESIS may cite.

Two acceptable states:

**(a) The DB row is populated** (preferred):
```bash
docker exec macro-tsdb psql -U quantuser -d macrodata -tAc \
  "SELECT 1 FROM macro_data.tool_metadata WHERE tool_name = '<backend_tool_name>'"
```
Returns `1` → check passes.

**(b) The DB row is NOT yet populated, but the curated migration file exists**:
```bash
ls database/migrations/*_phase1_<backend_tool_name>_curated.sql
```
Returns a matching file → check passes (the DB write is human-gated; the migration file existing is sufficient for the frontend factory to proceed, since the backend tool is metadata-complete).

If both (a) and (b) fail → mark `blocked` with reason `backend_metadata_missing`.

### Check 4 — Mockup PNGs are committed in the target frontend module folder

This is the human input. The user MUST commit both files BEFORE the cron job dispatches:

```bash
test -f UI/macro-copilot-dashboard-polished/src/modules/primitives/<verb>_<slug>_tool/mockups/Compact.png && \
test -f UI/macro-copilot-dashboard-polished/src/modules/primitives/<verb>_<slug>_tool/mockups/Extended.png
```

The catalog entry's `pre_flight_backend_audit.mockups_required` field lists both expected paths.

If either is missing → mark `blocked` with reason `mockups_missing: <which file>`.

## 3. On a miss

If ANY of the four checks fail:

1. **Do NOT** dispatch the builder.
2. Mark the tool `blocked` in `frontend_tool_catalog.yaml`:
   ```yaml
   - id: <tool_id>
     status: blocked
     block_reason: "<which check failed and the specific path>"
   ```
3. Write to `frontend_runtime_state.yaml`:
   ```yaml
   last_stop_reason: pre_flight_backend_audit_missing
   human_required:
     reason: pre_flight_backend_audit_missing
     missing_artifact: "<specific path>"
     tool_id: "<catalog entry id>"
     remediation: |
       <For Check 1 / 2 / 3: backend factory must complete the
       missing artifact on primitive_automation branch + the user
       must merge or cherry-pick before this tool is unblocked.
       For Check 4: human must commit the missing mockup PNG into
       the target frontend module folder.>
   ```
4. **Continue** to the next eligible tool in the same wake — one missing artifact blocks only its dependent tool.

## 4. Read-only

This check is read-only. The orchestrator MUST NOT:

- create the backend folder
- scaffold the MCP wrapper
- write to `macro_data.tool_metadata`
- apply the curated migration SQL
- generate placeholder mockup PNGs

If any artifact is missing, surface it. Do NOT manufacture it.

## 5. Why the mockup check is at the same level as the backend checks

The mockups are equal-priority to the backend artifacts because:

- BUILD_GUIDE.md §Stage 6 6D mandates the mockup-first workflow.
- The reviewer's prompt (`CLAUDE_REVIEWER_PROMPT.md` §B "Mockup conformance") scores the implementation against the committed PNGs. Without them, the reviewer cannot score this dimension and would have to skip a load-bearing review axis.
- The builder's prompt (`CLAUDE_BUILDER_PROMPT.md` §"The mockups are the design source-of-truth") reads the PNGs as the visual contract.

Dispatching without mockups would force the builder to invent UI freely (mockup-first workflow violation) and force the reviewer to lower its bar (single-engine independence trade-off would compound).

## 6. Catalog-entry shape

The catalog entry must carry the `pre_flight_backend_audit` block with the four resolved paths:

```yaml
pre_flight_backend_audit:
  backend_folder: rates_agent/<sub_agent>/tools/<tool_slug>/
  mcp_wrapper_in: rates_agent/<sub_agent>/mcp_server.py
  mcp_tool_name: <MCP tool name>
  tool_metadata_check: "SELECT 1 FROM macro_data.tool_metadata WHERE tool_name='<name>'"
  mockups_required:
    - UI/macro-copilot-dashboard-polished/src/modules/primitives/<verb>_<slug>_tool/mockups/Extended.png
    - UI/macro-copilot-dashboard-polished/src/modules/primitives/<verb>_<slug>_tool/mockups/Compact.png
```

The orchestrator reads this block verbatim and runs each check against the named path.

## 7. Anti-patterns

- The orchestrator silently advancing past a missing mockup (the build would proceed without the design contract).
- The orchestrator creating a placeholder mockup to satisfy the check (violates the mockup-first workflow + the read-only rule).
- The orchestrator scaffolding the backend folder when Check 1 fails (out of scope — this factory is frontend-only).
- The orchestrator falling back to the generic `/api/v1/tools/{name}/run` endpoint when Check 3 fails (the curated metadata is what the frontend's methodology card consumes — without it, the rendered card is empty or hardcoded).
- A tool catalog entry that omits the `pre_flight_backend_audit` block (the orchestrator cannot run the check; mark `human_required`).

## 8. Re-entry behavior

A tool blocked on this check stays `blocked` until either:

- the human merges the missing backend artifact from `primitive_automation` (or builds it on `frontend_automation`'s sibling branch and merges)
- the human commits the missing mockup PNG
- the human manually flips the catalog entry back to `todo` after fixing the underlying issue

On the next wake, the orchestrator selects the tool again (per the standard selection order) and re-runs the pre-flight check. If it passes this time → proceed to builder dispatch. If it still fails → stay blocked.

## 9. Relationship to the primitive factory's `PRE_FLIGHT_LOAD_AUDIT.md`

| Aspect | Primitive factory | Frontend factory |
|---|---|---|
| **Gate timing** | Before builder dispatches | Before builder dispatches |
| **Subject** | DB `load_audit` SUCCESS row | Backend artifacts + mockup PNGs |
| **Read-only** | yes | yes |
| **Block-and-advance** | yes | yes |
| **Auto-fix** | NO | NO |

Both gates exist for the same architectural reason: do not dispatch a builder against an environment that cannot honestly support the build. The substrate (DB load_audit) is the primitive factory's precondition; the backend + mockups are the frontend factory's.

## 10. Links

- BUILD_GUIDE.md §Stage 1.0 — backend folder shape
- BUILD_GUIDE.md §Stage 1E — MCP wrapper registration
- BUILD_GUIDE.md §Stage 3 — DB curated migration
- BUILD_GUIDE.md §Stage 6 6D — mockup-first workflow
- `frontend_tool_catalog.yaml` — the `pre_flight_backend_audit` block per tool
- `ORCHESTRATOR_PROMPT.md` §"Pre-flight backend audit rule" — the operational embodiment
- `SINGLE_REVIEW_ROUND_POLICY.md` §3 — why this gate is load-bearing (a missing mockup escalates to `human_required` via the reviewer's mockup-conformance check)
