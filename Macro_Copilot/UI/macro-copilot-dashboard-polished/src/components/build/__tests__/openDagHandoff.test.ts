/* eslint-disable @typescript-eslint/no-explicit-any */
// ============================================================================
// openDagHandoff.test.ts — PR-11D D.3 smoke test.
// ----------------------------------------------------------------------------
// Plan §D.3 acceptance: when an Ask turn carries a persisted open-DAG
// workspace (``template_id === null`` AND ``workflow.workspace.slug``
// populated), the "Open in Build" CTA must route to
// ``/workspace/:slug`` — NOT to ``?workflow=`` (which surfaces a
// "paused / unavailable" card via ``WorkflowStatusCanvas``, the
// template-lane fallback).
//
// This is the load-bearing contract Codex flagged on the BuildShell
// auto-navigation gap: a Build-launched prompt has its own
// auto-navigation effect, but ASK-launched prompts depend on
// ``resolveBuildHref`` to land the user on the right URL.  Pre-PR-11B
// open-DAG turns either had no workspace.slug (persistence was
// missing) or got dropped to the template-id-only branch and rendered
// "unavailable workflow".  PR-11B wired the slug-first branch; this
// smoke locks the contract.
//
// What this test covers
// ---------------------
//   1. Open-DAG handoff (template_id=null + workspace.slug populated)
//      → ``/workspace/:slug``.
//   2. Template-lane handoff (template_id="event_study" + workspace.slug)
//      → ``/workspace/:slug`` (same priority as open-DAG; slug wins).
//   3. Template-lane without a slug (paused / persist failed)
//      → ``/workspace?workflow=<template_id>&workflow_status=…``.
//   4. Open-DAG without a slug (persistence failed) MUST fall through
//      to the supervisor path or null — NOT to the
//      ``?workflow=null&workflow_status=…`` template-paused card.
//   5. Pure supervisor turn with workspaceContext → ``?context=`` URL.
// ============================================================================

import type { CopilotMessage } from '@/types/copilot';
import { resolveBuildHref } from '@/components/ask/messages/ActionRow';

type Check = { label: string; fn: () => void | Promise<void> };
const _checks: Check[] = [];

function check(label: string, fn: () => void | Promise<void>): void {
  _checks.push({ label, fn });
}

function assertEqual<T>(actual: T, expected: T, label: string): void {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a !== e) {
    throw new Error(
      `assertEqual failed: ${label}\n  expected: ${e}\n  actual:   ${a}`,
    );
  }
}

function assertNull(value: unknown, label: string): void {
  if (value !== null) {
    throw new Error(`assertNull failed: ${label} — got ${JSON.stringify(value)}`);
  }
}

// ----------------------------------------------------------------------------
// Minimal CopilotMessage builders — the SUT only inspects
// ``message.workflow.*`` and ``message.workspaceContext``; every
// other field is purely for type-checking.
// ----------------------------------------------------------------------------

function _baseMessage(): CopilotMessage {
  return {
    id: 'msg-test',
    role: 'assistant',
    content: '',
    timestamp: new Date(0),
    traceSteps: [],
    workspaceContext: null,
    isStreaming: false,
    workflow: null,
  };
}

function openDagMessage(
  workspaceSlug: string | null,
): CopilotMessage {
  const m = _baseMessage();
  m.workflow = {
    // template_id === null is the load-bearing open-DAG marker.
    routeDecision: {
      template_id: null as unknown as string,
      slot_values: {},
      rationale: 'Open DAG composed from primitives + operators',
    },
    status: 'complete',
    result: {
      ok: true,
      template_id: null as unknown as string,
      terminal_artifact: {
        type: 'ScalarMetric',
        metric_key: 'correlation_coefficient',
        value: -0.342,
        units: 'RATIO',
      } as any,
      workflow_lineage_summary: 'p1 → p2 → align → correlation',
    },
    workspace: workspaceSlug
      ? {
          id: 'ws-uuid-stub',
          slug: workspaceSlug,
          name: null,
          dag_hash: 'd'.repeat(64),
          url: `/workspace/${workspaceSlug}`,
        }
      : null,
  };
  return m;
}

function templateLaneMessage(
  templateId: string,
  workspaceSlug: string | null,
): CopilotMessage {
  const m = _baseMessage();
  m.workflow = {
    routeDecision: {
      template_id: templateId,
      slot_values: { signal: { tool_name: 'calculate_cpi_surprise_tool' } },
      rationale: 'Template-bound run',
    },
    status: 'complete',
    result: {
      ok: true,
      template_id: templateId,
      terminal_artifact: { type: 'Series', units: 'BPS' } as any,
      workflow_lineage_summary: 'signal -> align -> ...',
    },
    workspace: workspaceSlug
      ? {
          id: 'ws-uuid-stub',
          slug: workspaceSlug,
          name: null,
          dag_hash: 'd'.repeat(64),
          url: `/workspace/${workspaceSlug}`,
        }
      : null,
  };
  return m;
}

function supervisorMessage(): CopilotMessage {
  const m = _baseMessage();
  m.workspaceContext = {
    tools: [
      {
        tool: 'calculate_curve_spread_tool',
        params: {
          curve_family: 'UST',
          short_tenor: '2Y',
          long_tenor: '10Y',
        },
      },
    ],
    tool_count: 1,
  };
  return m;
}

// ----------------------------------------------------------------------------
// Tests
// ----------------------------------------------------------------------------

check('§D.3 · open-DAG with workspace.slug → /workspace/:slug', () => {
  const msg = openDagMessage('corr-2s10s-be5y-abc');
  const href = resolveBuildHref(msg);
  assertEqual(
    href,
    '/workspace/corr-2s10s-be5y-abc',
    'open-DAG with slug must route directly to the persisted workspace',
  );
});

check('§D.3 · template-lane with slug also routes to /workspace/:slug', () => {
  const msg = templateLaneMessage(
    'event_study',
    'evt-cpi-us-1m-xyz',
  );
  const href = resolveBuildHref(msg);
  assertEqual(
    href,
    '/workspace/evt-cpi-us-1m-xyz',
    'template-lane with slug shares the same priority — slug always wins',
  );
});

check('§D.3 · template-lane WITHOUT slug → /workspace?workflow=…', () => {
  const msg = templateLaneMessage('event_study', null);
  const href = resolveBuildHref(msg);
  // We assert the shape (the workflow_status suffix varies per
  // classifyWorkflow's runtime resolution) without being brittle
  // about the status string.
  if (href === null) {
    throw new Error('template_id present should produce a non-null href');
  }
  if (!href.startsWith('/workspace?workflow=event_study')) {
    throw new Error(
      `expected ?workflow=event_study URL; got ${href}`,
    );
  }
});

check(
  '§D.3 · open-DAG without slug (persist failed) MUST NOT route to ?workflow=null',
  () => {
    // Codex correction: open-DAG runs that lose persistence have
    // ``template_id: null`` AND no workspace.slug.  The pre-PR-11B
    // falsy check would have routed these to
    // ``?workflow=null&workflow_status=…`` and rendered a "null
    // template paused" card.  The PR-11B fix at ActionRow.tsx:152
    // ``if (templateId)`` correctly excludes null — fall through to
    // the supervisor path or return null cleanly.
    const msg = openDagMessage(null);
    const href = resolveBuildHref(msg);
    // No workspaceContext set on this synthetic message → href is
    // null (fall-through path).
    assertNull(
      href,
      'open-DAG without slug AND without workspace context must return null',
    );
    // Also assert we did NOT route to ?workflow=null — defensive
    // string check.
    if (href !== null) {
      throw new Error(
        `defensive: href must not contain workflow=null; got ${href}`,
      );
    }
  },
);

check(
  '§D.3 · pure supervisor turn (no workflow) → ?context= URL',
  () => {
    const msg = supervisorMessage();
    const href = resolveBuildHref(msg);
    if (href === null) {
      throw new Error('supervisor turn with workspaceContext must produce a href');
    }
    if (!href.startsWith('/workspace?context=')) {
      throw new Error(
        `expected ?context= URL for supervisor turn; got ${href}`,
      );
    }
    if (!href.includes('handoff=ask')) {
      throw new Error(
        `supervisor turn must carry handoff=ask marker; got ${href}`,
      );
    }
  },
);

// ============================================================================
// Runner
// ============================================================================

export async function runAllOpenDagHandoffTests(): Promise<void> {
  let passed = 0;
  let failed = 0;
  for (const { label, fn } of _checks) {
    try {
      await fn();
      passed += 1;
    } catch (err) {
      failed += 1;
      // eslint-disable-next-line no-console
      console.error(`✗ ${label}\n  ${(err as Error).message}`);
    }
  }
  // eslint-disable-next-line no-console
  console.log(
    `\nopen-dag handoff coverage: ${passed} passed, ${failed} failed`,
  );
  if (failed > 0) {
    throw new Error(`${failed} open-dag handoff check(s) failed`);
  }
}

const _meta = (import.meta as unknown) as { main?: boolean };
if (_meta && _meta.main) {
  void runAllOpenDagHandoffTests();
}
