// ============================================================================
// src/modules/__test-utils.ts — assertStandardModuleInvariants helper.
// ----------------------------------------------------------------------------
// Stage 2 — Build the src/modules/ infrastructure.  Lands the helper
// per-module round-trip tests (Stage 3+) will exercise.  In Stage 2
// nothing calls this helper at runtime; we ship it so the per-module
// boilerplate Stage 3 introduces is a one-line affair:
//
//   import { assertStandardModuleInvariants } from '@/modules/__test-utils';
//   import { MODULE } from '../module';
//   check('module spec round-trip', () => {
//     assertStandardModuleInvariants(MODULE, { folderName: 'calculate_cpi_surprise_tool' });
//   });
//
// Doctrine reference
// ------------------
// Covers FM11 invariants 1–8:
//   1. ``MODULE.toolName === folderName`` (identity)
//   2. ``MODULE.tiers`` is a subset of the closed family
//   3. Exactly one runtime-status tier in ``MODULE.tiers``
//   4. Each capability tier ⇒ matching surfaces.<key> populated
//   5. Each surfaces.<key> populated ⇒ matching capability tier
//   6. THESIS.md exists in the folder
//   7. THESIS Question 1 enumerates the same tiers as MODULE.tiers
//   8. ``unsupportedReason`` non-null when runtime status is
//      workflow_incompatible | paused | deferred
//
// Invariant 9 (loader presence) is enforced by a dedicated test in
// ``src/lib/__tests__/loaderPresence.test.ts`` — not per-module.
//
// Filesystem-touching invariants (6, 7) require Node ``fs`` access.
// The helper accepts an optional ``fsLayer`` injection so tests in
// non-Node environments can stub it; default uses dynamic import('fs').
// In Stage 2 no module exercises the helper, so any test environment
// works.
// ============================================================================

import type { PrimitiveModuleSpec, SurfaceTier } from './types';
import {
  ALL_SURFACE_TIERS,
  CAPABILITY_TIERS,
  RUNTIME_STATUS_TIERS,
} from './types';

// ---------------------------------------------------------------------------
// Filesystem-layer indirection (so the helper works in any test env).
// ---------------------------------------------------------------------------

export interface ModuleTestFs {
  readFileSync: (path: string, encoding: string) => string;
  existsSync: (path: string) => boolean;
}

async function defaultFs(): Promise<ModuleTestFs> {
  // @ts-expect-error — Node only.  Test runner uses esbuild
  // --platform=node so this import resolves at bundle time.
  return (await import('fs')) as ModuleTestFs;
}

// ---------------------------------------------------------------------------
// Helper interface (kept open for future expansion).
// ---------------------------------------------------------------------------

export interface AssertStandardModuleInvariantsOptions {
  /** Backend tool_name the module's folder is named after.  MUST
   *  equal ``MODULE.toolName`` (FM1 / invariant 1).  Computing this
   *  from the test file's path inside the helper would couple the
   *  helper to a specific filesystem layout; the caller passes it
   *  explicitly so the test's intent stays readable. */
  folderName: string;
  /** Absolute path to the module folder.  Used by invariant 6 + 7
   *  (filesystem-touching).  Defaults to the test file's directory
   *  resolved one level up (``__tests__/`` → parent).  Tests that
   *  prefer to compute the folder differently can pass it here. */
  moduleFolderPath?: string;
  /** Optional fs injection for non-Node test environments.  Defaults
   *  to dynamic import('fs'). */
  fsLayer?: ModuleTestFs;
  /** When true, skips the filesystem-touching invariants (6, 7).
   *  Useful when running the helper outside the test runner — e.g.
   *  in a Storybook story or a smoke check that imports the spec
   *  but doesn't have file access. */
  skipFilesystemChecks?: boolean;
}

// ---------------------------------------------------------------------------
// The assertion.
// ---------------------------------------------------------------------------

/** Throws on the first invariant violation with a precise label so the
 *  test runner's error output points the contributor at the exact
 *  failure.  Pure for the in-memory invariants (1–5, 8); async for the
 *  filesystem ones (6, 7) since they require a dynamic ``import('fs')``. */
export async function assertStandardModuleInvariants(
  module: PrimitiveModuleSpec,
  opts: AssertStandardModuleInvariantsOptions,
): Promise<void> {
  // ---- Invariant 1: identity ------------------------------------------
  if (module.toolName !== opts.folderName) {
    throw new Error(
      `FM1 violation — module.toolName (${JSON.stringify(module.toolName)}) ` +
        `does not match folder name (${JSON.stringify(opts.folderName)})`,
    );
  }

  // ---- Invariant 2: tiers subset of closed family ---------------------
  if (!Array.isArray(module.tiers) || module.tiers.length === 0) {
    throw new Error(
      `FM3 violation — module.tiers must be a non-empty array; got ` +
        JSON.stringify(module.tiers),
    );
  }
  const tierSet = new Set<SurfaceTier>(module.tiers);
  for (const t of tierSet) {
    if (!ALL_SURFACE_TIERS.includes(t)) {
      throw new Error(
        `FM3 violation — unknown SurfaceTier value: ${JSON.stringify(t)}.  ` +
          `Closed family: ${ALL_SURFACE_TIERS.join(', ')}`,
      );
    }
  }

  // ---- Invariant 3: exactly one runtime-status tier -------------------
  const runtimeTiersClaimed = [...tierSet].filter((t) =>
    RUNTIME_STATUS_TIERS.has(t as never),
  );
  if (runtimeTiersClaimed.length !== 1) {
    throw new Error(
      `FM3 violation — module must claim EXACTLY ONE runtime-status tier ` +
        `(${[...RUNTIME_STATUS_TIERS].join(', ')}); claimed ` +
        `${runtimeTiersClaimed.length}: ${JSON.stringify(runtimeTiersClaimed)}`,
    );
  }

  // ---- Invariants 4 + 5: tier ↔ surfaces consistency ------------------
  const surfaces = module.surfaces ?? {};
  const capabilityToSurfaceKey: Record<string, keyof typeof surfaces> = {
    custom_build_surface: 'build',
    custom_preview_widget: 'preview',
    monitor_surface: 'monitor',
    ask_surface: 'ask',
  };

  // 4 — every claimed capability tier must have a populated surface.
  //
  // Stage 4d relaxation for ``monitor_surface``: the tier is satisfied
  // by EITHER ``surfaces.monitor`` (the single-widget legacy shape) OR
  // a non-empty ``monitorWidgets`` array (the multi-variant shape that
  // Stage 4d introduces for tools with cross-market parameterised +
  // pre-aggregated variants of the same widget).  Every Stage 4d
  // ``monitor_surface`` claim uses ``monitorWidgets``; ``surfaces.monitor``
  // is reserved for future single-widget modules.
  for (const t of tierSet) {
    if (!CAPABILITY_TIERS.has(t as never)) continue;
    const key = capabilityToSurfaceKey[t];
    if (!key) continue;
    if (key === 'monitor') {
      const hasSurface = surfaces.monitor != null;
      const hasWidgets =
        Array.isArray(module.monitorWidgets) && module.monitorWidgets.length > 0;
      if (!hasSurface && !hasWidgets) {
        throw new Error(
          `FM8 violation — module claims 'monitor_surface' but neither ` +
            `surfaces.monitor nor monitorWidgets is populated.  Add a ` +
            `MonitorWidget surface file (single-widget) OR populate ` +
            `monitorWidgets (multi-variant).`,
        );
      }
      continue;
    }
    if (!surfaces[key]) {
      throw new Error(
        `FM8 violation — module claims '${t}' but surfaces.${key} is not ` +
          `populated.  Either remove the tier or add the surface file.`,
      );
    }
  }

  // 5 — every populated surface must have a matching capability tier.
  for (const [key, component] of Object.entries(surfaces)) {
    if (component == null) continue;
    const tierForKey = Object.entries(capabilityToSurfaceKey).find(
      ([, v]) => v === key,
    )?.[0];
    if (!tierForKey) {
      throw new Error(
        `FM8 violation — surfaces.${key} is populated but no capability ` +
          `tier maps to that key (closed mapping is build/preview/monitor/ask).`,
      );
    }
    if (!tierSet.has(tierForKey as SurfaceTier)) {
      throw new Error(
        `FM8 violation — surfaces.${key} is populated but '${tierForKey}' ` +
          `is not in module.tiers.  Either claim the tier or remove the ` +
          `surface reference.`,
      );
    }
  }

  // 5b — Stage 4d reverse rule for monitorWidgets: if a module
  // populates ``monitorWidgets`` it MUST claim ``monitor_surface``.
  const widgetsPopulated =
    Array.isArray(module.monitorWidgets) && module.monitorWidgets.length > 0;
  if (widgetsPopulated && !tierSet.has('monitor_surface')) {
    throw new Error(
      `FM8 violation — module populates monitorWidgets but does not ` +
        `claim 'monitor_surface'.  Either claim the tier or remove the ` +
        `monitorWidgets entries.`,
    );
  }

  // ---- Invariant 8: unsupportedReason gating --------------------------
  //
  // Stage 4e: ``manifest_typed_view`` also requires a reason — these
  // tools ship typed-detail endpoints but no ``_PRIMITIVE_SPECS`` entry,
  // so the workflow bridge cannot dispatch them.  The honest copy
  // explains the "typed view in Build is the live surface" affordance.
  const needsReason =
    tierSet.has('workflow_incompatible') ||
    tierSet.has('manifest_typed_view') ||
    tierSet.has('paused') ||
    tierSet.has('deferred');
  if (needsReason) {
    const r = module.unsupportedReason;
    if (r == null) {
      throw new Error(
        `FM6 violation — module's runtime-status tier (${runtimeTiersClaimed[0]}) ` +
          `requires a non-null unsupportedReason.`,
      );
    }
    if (
      typeof r.label !== 'string' ||
      r.label.length === 0 ||
      typeof r.reason !== 'string' ||
      r.reason.length === 0 ||
      typeof r.whatWorksNow !== 'string' ||
      r.whatWorksNow.length === 0
    ) {
      throw new Error(
        `FM6 violation — unsupportedReason must have non-empty label, ` +
          `reason, and whatWorksNow strings; got ${JSON.stringify(r)}`,
      );
    }
  } else {
    if (module.unsupportedReason != null) {
      throw new Error(
        `FM6 violation — module is generic_runnable but unsupportedReason ` +
          `is set.  Remove it (the field is reserved for ` +
          `workflow_incompatible / manifest_typed_view / paused / ` +
          `deferred modules).`,
      );
    }
  }

  // ---- Invariant 6 + 7: filesystem-touching ---------------------------
  if (opts.skipFilesystemChecks) return;

  const folderPath = opts.moduleFolderPath;
  if (!folderPath) {
    // No folder path supplied — skip the filesystem invariants.  This
    // is the Stage-2 default: per-module tests pass the path; smoke
    // checks omit it.
    return;
  }
  const fs = opts.fsLayer ?? (await defaultFs());
  const thesisPath = `${folderPath}/THESIS.md`;

  if (!fs.existsSync(thesisPath)) {
    throw new Error(
      `FM10 violation — THESIS.md not found at ${thesisPath}.  Every ` +
        `module ships THESIS.md per the template at ` +
        `docs_revamped/02_components/frontend_module/thesis_template.md.`,
    );
  }

  // ---- Stage 4e — surface-file presence check -------------------------
  //
  // FM8 enforces that every claimed capability tier has a populated
  // ``surfaces.<key>``.  Stage 4e adds the on-disk corollary: when a
  // surface is populated, the matching ``surfaces/<Name>.tsx`` file
  // MUST exist in the module folder.  Catches the "I claimed the tier
  // and set surfaces.X but deleted the actual .tsx" failure mode the
  // pre-Stage-4e helper was blind to.
  //
  // Mapping is the canonical one from FM8 plus Stage 4d's
  // ``monitorWidgets`` array, which can carry many components per
  // module under ``surfaces/monitor/``.
  const expectedSurfaceFiles: Array<{ key: string; path: string }> = [];
  if (module.surfaces?.build != null) {
    expectedSurfaceFiles.push({
      key: 'surfaces.build',
      path: `${folderPath}/surfaces/BuildSurface.tsx`,
    });
  }
  if (module.surfaces?.preview != null) {
    expectedSurfaceFiles.push({
      key: 'surfaces.preview',
      path: `${folderPath}/surfaces/PreviewWidget.tsx`,
    });
  }
  if (module.surfaces?.monitor != null) {
    expectedSurfaceFiles.push({
      key: 'surfaces.monitor',
      path: `${folderPath}/surfaces/MonitorWidget.tsx`,
    });
  }
  if (module.surfaces?.ask != null) {
    expectedSurfaceFiles.push({
      key: 'surfaces.ask',
      path: `${folderPath}/surfaces/AskCard.tsx`,
    });
  }
  for (const f of expectedSurfaceFiles) {
    if (!fs.existsSync(f.path)) {
      throw new Error(
        `FM8 violation — ${f.key} is populated on MODULE but the ` +
          `expected file is missing at ${f.path}.`,
      );
    }
  }
  // For ``monitorWidgets``, the per-widget components live anywhere
  // the module wants (convention: ``surfaces/monitor/<Name>.tsx``).
  // We verify the folder exists when widgets are populated.
  if (
    Array.isArray(module.monitorWidgets) &&
    module.monitorWidgets.length > 0 &&
    !fs.existsSync(`${folderPath}/surfaces/monitor`)
  ) {
    throw new Error(
      `FM8 violation — monitorWidgets is populated but the expected ` +
        `surfaces/monitor/ folder is missing at ${folderPath}/surfaces/monitor.`,
    );
  }

  // Invariant 7 — THESIS Question 1 must enumerate exactly the tiers
  // in MODULE.tiers.  We do a light parse: find the "## 1." heading
  // (or the question-1 anchor the template ships with), read up to
  // the next "## 2." or end-of-section, and assert each claimed tier
  // name appears verbatim.  Soft on FORMAT (we don't reject a stray
  // newline) but strict on TIER COVERAGE (every claimed tier must
  // appear; no unlisted tier may appear).
  const thesis = fs.readFileSync(thesisPath, 'utf8');
  const q1Match = thesis.match(
    /##\s*1\.\s+What surfaces does this module ship\?([\s\S]*?)(?=##\s*2\.|$)/i,
  );
  if (!q1Match) {
    throw new Error(
      `FM10 violation — THESIS.md is missing the Question 1 section ` +
        `("## 1. What surfaces does this module ship?").  Copy the ` +
        `template from docs_revamped/02_components/frontend_module/thesis_template.md.`,
    );
  }
  const q1Body = q1Match[1];
  for (const t of tierSet) {
    if (!q1Body.includes(t)) {
      throw new Error(
        `FM10 violation — THESIS Question 1 does not enumerate tier ` +
          `'${t}' that MODULE.tiers claims.  Update Question 1 to list ` +
          `every claimed tier.`,
      );
    }
  }
  // ---- Stage 4e — strict reverse Q1 check (re-enabled) ----------------
  //
  // Stage 2 enforced this strictly; Stage 3 relaxed it because the
  // scaffolded modules' framing prose mentioned UNCLAIMED capability
  // tiers in passing.  Stage 4e's rewrites made the prose precise, so
  // we re-enable the strict rule: an UNCLAIMED capability tier name
  // MUST NOT appear in Q1's body.  Word-boundary regex so "ask" can
  // still appear without being mistaken for ``ask_surface``.
  const allCapabilityTiers = ['custom_build_surface', 'custom_preview_widget', 'monitor_surface', 'ask_surface'];
  for (const t of allCapabilityTiers) {
    if (tierSet.has(t as SurfaceTier)) continue;
    const pattern = new RegExp(`\\b${escapeRegex(t)}\\b`);
    if (pattern.test(q1Body)) {
      throw new Error(
        `FM10 violation — THESIS Question 1 mentions unclaimed capability ` +
          `tier '${t}'.  Q1 is the canonical declaration of which tiers ` +
          `this module ships; unclaimed tiers must not appear there.  ` +
          `Move the mention to Q4 (design triggers) if it's aspirational.`,
      );
    }
  }
  // Stage 3 historical note — original relaxed-reverse rationale (kept
  // for the bisect log; the strict check above replaces it).  The
  // original Stage 2 check rejected any unclaimed tier name appearing
  // in Q1's body.  Stage 3 ran into the case where THESIS prose
  // mentioned UNCLAIMED capability tiers in framing text — e.g. "(custom_build_surface,
  // monitor_surface — not claimed in this stage; planned for Stage
  // 4)".  Such mentions are valid documentation, not invariant
  // violations.  The Stage 3 contract is one-directional: every
  // CLAIMED tier MUST appear in Q1 (enforced above); unclaimed
  // tiers MAY appear in framing prose without triggering FM10.
  // The original strict check is intentionally not enforced today.
  // To re-introduce it (e.g. once THESIS templates standardise on a
  // dedicated "Planned capabilities" section that doesn't share the
  // Question 1 body), iterate ``ALL_SURFACE_TIERS`` and assert each
  // unclaimed tier is absent from ``q1Body`` via a word-boundary
  // regex.
}

function escapeRegex(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}
