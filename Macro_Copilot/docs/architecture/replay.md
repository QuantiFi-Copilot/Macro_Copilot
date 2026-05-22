# Replay architecture — Phase 0 close

Phase 0's deliverable is a substrate where **opening a workspace
URL today and opening it in six months reproduces the same
numbers** (assuming the underlying market data hasn't been
revised).  This doc explains how the four pins that make that
true compose.

For per-store schema details see [`state_schema.md`](state_schema.md).
For the system-level overview see [`state.md`](state.md).

---

## The four pins

1. **Content addressing** (PR 2).  Every artifact's identity is
   `Lineage.head_hash` — the SHA-256 of the canonical-JSON step
   chain.  Two artifacts produced by identical fetch + clean +
   primitive + operator chains share a hash.  Cross-version
   determinism is gated by
   [`tests/state/test_hash_stability.py`](../../tests/state/test_hash_stability.py)
   running on Python 3.11 + 3.12.

2. **Methodology pinning** (PR 9).  Every `PrimitiveStep` records
   the `methodology_version_id` of the YAML it loaded.  The
   registry stores the *parsed-and-canonicalised* YAML content (NOT
   the raw text), so cosmetic edits don't create new versions;
   semantic edits do.  `ToolConfig.model_validate(rec.yaml_content)`
   round-trips losslessly — verified per-YAML in
   [`tests/state/test_tool_config_round_trip.py`](../../tests/state/test_tool_config_round_trip.py).

3. **Application version** (PR 9).  Every artifact records the
   `application_version_id` of the git commit that produced it.
   `state.methodology_versions.current_application_version_id`
   resolves the current commit via `git rev-parse HEAD` with an
   env-var override (`MACRO_COPILOT_GIT_COMMIT`) and an
   unknown-sentinel fallback.

4. **URL stability** (PR 10).  Every workspace has a slug derived
   ONCE at create time: `slugify(name) + "-" + uuid.hex[:8]`.
   `rename_workspace` updates `name` only — the slug, and
   therefore the URL, is preserved.  The brief's six-month-replay
   guarantee is the consequence: the URL the user shared today
   still resolves to the same workspace in six months, even if
   the display name has changed.

---

## End-to-end resolution chain

```
User opens shared URL
        │
        ▼   GET /api/v1/workspace/{slug}
copilot_state.workspaces.slug  ──▸  workspaces.id
                                    workspaces.dag_hash
                                            │
                                            ▼
                          copilot_state.dags.hash
                                            │
                                            ▼
                          copilot_state.dag_nodes
                          per-node ``artifact_hash``
                                            │
                                            ▼
                          copilot_state.artifact_metadata
                          ├─ methodology_version_ids[]
                          │            │
                          │            ▼
                          │  copilot_state.methodology_versions
                          │  ├─ yaml_content (JSONB, parsed +
                          │  │              canonicalised)
                          │  └─ yaml_content_hash
                          │
                          └─ application_version_id
                                       │
                                       ▼
                            copilot_state.application_version
                            └─ git_commit (40 hex)
```

The route handler at `api/routes/workspace.py` walks this graph
and returns either:
- **`mode=original`**: reconstructs each pinned YAML via
  `ToolConfig.model_validate(rec.yaml_content)` and exposes the
  resulting `tool_config_hash` so a caller can verify byte-
  identical replay would land at the same artifact hash.
- **`mode=current`**: compares each pinned YAML against the same
  on-disk path NOW.  Returns `methodology_diffs` listing every
  changed `conventions.<key>`.  Drift detection is at the
  `yaml_content_hash` level — the cheapest sound comparison.

---

## Two-mode contract

| `mode=original` (default) | `mode=current` |
|---|---|
| Source of truth: `methodology_versions.yaml_content` (the registry). | Source of truth: on-disk YAML at `rec.yaml_path`. |
| Filesystem edits CANNOT taint the response. | Filesystem edits surface as `methodology_diffs`. |
| Returns `reconstructed[]` — each with `tool_config_round_trip_ok` and the resulting `tool_config_hash`. | Returns `methodology_diffs[]` — each with `original_content_hash`, `current_content_hash`, and a top-level diff of `conventions.<key>`. |
| Six-month replay guarantee: a caller that rebuilds the `PrimitiveStep` with the reconstructed `tool_config_hash` produces the SAME artifact hash as the original. | Operator signal: "if I re-execute today, would the output differ?"  Yes iff the diff is non-empty. |

The [`tests/integration/test_phase0_demo.py`](../../tests/integration/test_phase0_demo.py)
acceptance test exercises this in
`test_yaml_override_original_recovers_current_diverges`: mutate
the YAML on disk (z_score_window_days: 252 → 200), assert
`mode=original` reconstructs to the pre-mutation
`conventions_hash`, and `mode=current` reports the diff.

---

## Why we deliberately do NOT re-execute in Phase 0

The replay route's job is to **detect divergence + reconstruct
configuration**, not to re-run primitives.  Re-execution requires:

- A live market-data slice as of the artifact's `as_of_date`.
- The per-tool `calculate_*` function path wired into a runner.
- The full DB engine surface for the executor.

That stack lives in the workflow runner (and a Phase 1+ executor
PR closes the loop end-to-end).  Phase 0 ships the substrate that
makes re-execution faithful: content-addressed inputs +
content-pinned configuration + content-pinned code revision.  When
re-execution lands, the assertion will be **"replay produces a
byte-identical artifact hash"** — and the work this PR does is
what makes that assertion meaningful.

---

## Restart resilience

Phase 0 PR 10's
[`tests/integration/test_workspace_replay.py`](../../tests/integration/test_workspace_replay.py)
and PR 11's
[`tests/integration/test_phase0_demo.py`](../../tests/integration/test_phase0_demo.py)
exercise the simulated server restart end-to-end:

1. Persist artifact + DAG + workspace + working-set bindings.
2. Hash all node artifacts.  Save the set.
3. `engine.dispose()` + `clear_caches()` for every in-process
   cache (`state.methodology_versions._yaml_hash_to_id`,
   `shared.config.tool_config._CACHE`).
4. Build a fresh `engine = create_engine(...)`.
5. Re-fetch the workspace by SLUG.  Re-hash every node.
6. **Assert byte-identical to step 2's set.**

This proves nothing in-process is silently load-bearing for
replay.  The URL + the database + the YAML registry is
sufficient.

---

## Cache-staleness self-healing

A subtle correctness corner: PR 9's `current_application_version_id`
keeps an in-process cache of `git_commit → application_version.id`.
If an out-of-band `DELETE FROM application_version` wipes the row
the cache points at, a subsequent `put_artifact` would otherwise
fail with an FK violation referencing a missing row.

`state.artifact_store._resolve_application_version_id` handles
this: it SELECTs the cached id before stamping it on a new
artifact, and on a missing-row reading, clears caches + retries
once.  Steady state is zero overhead; rare cache-staleness is
one extra round trip + a retry.  Documented in
[`state_schema.md`](state_schema.md#cache-staleness-self-healing).

---

## Phase 1 hand-off

Phase 0 ships **the substrate**: deterministic hashes, content-
addressed artifacts, replayable workspaces, version-pinned
methodology + code revision.  The brief's six-month-replay
guarantee is buildable on it.

Phase 1's job is to **plug the executor in**: when a workspace
opens under `mode=original`, the runner re-executes every primitive
against the reconstructed `ToolConfig` and asserts the resulting
artifact hashes equal the originals.  That assertion is meaningful
ONLY because Phase 0 made the configuration content-addressed.
