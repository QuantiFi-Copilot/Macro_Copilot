// ============================================================================
// src/modules/primitives/calculate_nfp_surprise_tool/module.ts
// ----------------------------------------------------------------------------
// Dual-view Build implementation under the new standards:
//   - methodology_exposure.md §5 standalone-bridge (own typed-detail
//     endpoint at /api/v1/rates/detail/nfp-surprise + own surfaces; no
//   - rendering_density.md §1 dual-view mandate (buildExtended +
//     buildCompact both REQUIRED)
//
// Surface-contract retraction (2026-05-26) — STILL BINDING for Monitor +
// Ask: the Stage 6 ``monitor_surface`` + ``ask_surface`` claims were
// retracted because the shipped widget + card did not deliver value over
// the defaults (the Monitor widget was a stub; the bespoke Ask card was
// a strict regression vs the generic AssistantResearchCard).  Per the
// surface contract's containment principle, a module's ``tiers`` array
// MUST match delivery — claiming a tier you don't fully ship is
// "half-built state for the system", which the principle forbids.
//   * Monitor — NOT eligible.  NFP is a monthly event read, not a
//     desk-glanceable state.  Per the contract's Monitor eligibility
//     rule: event releases are excluded.  The dual-view Build claim
//     below does NOT reopen that decision.
//   * Ask — the generic ``AssistantResearchCard`` (7 zones including
//     chart, DAG, provenance, follow-ups) remains the Ask surface.
//
// Per FM7 (pure-spec assembly): exports a pure value; no side effects.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';
import BuildExtended from './surfaces/BuildExtended';
import BuildCompact from './surfaces/BuildCompact';

export const MODULE: PrimitiveModuleSpec = {
  // FM1 — identity (folder name === toolName)
  toolName: 'calculate_nfp_surprise_tool',

  // FM3 — tier claims:
  //   * generic_runnable — backend ships in _PRIMITIVE_SPECS
  //   * custom_build_surface — dual-view Build (rendering_density.md §1)
  //   NO monitor_surface / ask_surface — see the retraction note above.
  tiers: ['generic_runnable', 'custom_build_surface'],

  // FM5 — display metadata
  displayName: 'NFP Surprise',
  category: 'economic_release_surprises',
  oneLineSummary:
    "Per-release US nonfarm-payrolls (NFP) surprise series (actual − consensus_median, in thousands of jobs) plus a rolling z-score over a window of N releases.",


  // FM8 — dual Build-side surfaces (rendering_density.md §5).
  surfaces: {
    buildExtended: BuildExtended,
    buildCompact: BuildCompact,
  },

  // FM5 — defaults mirror the backend Input: this primitive is single-
  // country single-event (country=US + event_type=nfp BOTH YAML-locked;
  // ``extra='forbid'`` on the Pydantic Input), so ``lookback_releases`` is
  // the ONLY param (config.yaml default_lookback_releases = 24 ≈ 2Y at
  // monthly cadence).  The rolling z window is YAML-locked at 24 releases
  // and is NOT a param.
  defaultParams: {
    lookback_releases: '24',
  },

  workspaceLabel: 'NFP surprise — release timeline + rolling z',
};
