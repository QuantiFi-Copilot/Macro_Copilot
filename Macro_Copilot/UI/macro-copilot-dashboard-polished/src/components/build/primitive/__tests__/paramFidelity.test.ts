/* eslint-disable @typescript-eslint/no-explicit-any */
// ============================================================================
// paramFidelity.test.ts — PR-B-β Ask-handoff fidelity invariants.
// ============================================================================
//
// Locks every PR-B-β load-bearing rule:
//
//   §A — ``decodePrimitiveContext`` / ``decodePrimitiveList`` preserve
//        nested objects + arrays in ``paramsStructured`` while keeping
//        ``params`` as the flat string-only projection typed-detail
//        endpoints accept.  (Backward-compat: the flat ``params`` shape
//        is unchanged.)
//
//   §B — ``isAskHandoff`` / ``parseHandoffOrigin`` correctly classify
//        URLs by the explicit ``handoff=ask`` marker.  Unmarked URLs
//        default to ``"library"`` so pre-PR-B-β bookmarks / Library
//        opens keep the silent-defaults policy.
//
//   §C — ``requiredParamsFor`` + ``missingRequiredTypedParams`` flag
//        the semantically-required params per typed-view kind.
//
//   §D — Source-level contracts: ``MultiPrimitiveCard`` /
//        ``VirtualPrimitiveCanvas`` import the missing-param helpers,
//        gate on ``askHandoff``, and render ``MissingParamsCard``
//        when both conditions hit.
//
//   §E — ``MultiPrimitiveCanvas`` computes call-index metadata so
//        duplicate-tool cards render the ``call N of M`` chip
//        instead of looking like a rendering bug.
//
//   §F — Round-trip lock: a workspace_context with structured params
//        survives encode → decode without losing nested entries.
// ============================================================================

interface NodeFs {
  readFileSync: (path: string, encoding: string) => string;
}
interface NodeGlobal {
  process?: { cwd?: () => string };
}

import {
  decodePrimitiveContext,
  decodePrimitiveList,
} from '../contextDecoder';
import {
  missingRequiredTypedParams,
  requiredParamsFor,
} from '../paramSpecs';
import {
  HANDOFF_ASK_VALUE,
  HANDOFF_QUERY_PARAM,
  handoffQueryFragment,
  isAskHandoff,
  parseHandoffOrigin,
} from '../handoffSignal';

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

function assertTruthy(value: unknown, label: string): void {
  if (!value) throw new Error(`assertTruthy failed: ${label}`);
}

function assertFalsy(value: unknown, label: string): void {
  if (value) throw new Error(`assertFalsy failed: ${label}`);
}

function assertContains(
  haystack: string,
  needle: string,
  label: string,
): void {
  if (!haystack.includes(needle)) {
    throw new Error(`assertContains failed: ${label}\n  needle: ${needle}`);
  }
}

async function loadSource(relativePath: string): Promise<string> {
  // @ts-ignore - node-only; esbuild --platform=node resolves it.
  const fs = (await import('fs')) as NodeFs;
  const _g = globalThis as unknown as NodeGlobal;
  const cwd = _g.process?.cwd?.() ?? '.';
  return fs.readFileSync(`${cwd}/${relativePath}`, 'utf8');
}

function encodeContext(payload: unknown): string {
  return encodeURIComponent(JSON.stringify(payload));
}

// ============================================================================
// §A — Decoder preserves structured params
// ============================================================================

check('§A · scalar-only params survive in both projections', () => {
  const ctx = encodeContext({
    tools: [
      {
        tool: 'calculate_cross_market_spread_tool',
        params: {
          curve_family_1: 'UST',
          curve_family_2: 'DE_BUND',
          tenor: '10Y',
        },
      },
    ],
    tool_count: 1,
  });
  const list = decodePrimitiveList(ctx);
  assertEqual(list.length, 1, 'one decoded entry');
  assertEqual(list[0].kind, 'generic_builder', 'generic_builder kind');
  assertEqual(
    list[0].params,
    {
      curve_family_1: 'UST',
      curve_family_2: 'DE_BUND',
      tenor: '10Y',
    },
    'flat params preserved',
  );
  assertEqual(
    list[0].paramsStructured,
    {
      curve_family_1: 'UST',
      curve_family_2: 'DE_BUND',
      tenor: '10Y',
    },
    'structured params match flat for scalar input',
  );
});

check('§A · nested objects survive in paramsStructured, drop from params', () => {
  const ctx = encodeContext({
    tools: [
      {
        tool: 'calculate_rolling_regression_tool',
        params: {
          regression_window_days: 252,
          target_spec: {
            curve_family: 'UST',
            tenor: '10Y',
            field_name: 'YLD_YTM_MID',
          },
          regressor_specs: [
            { curve_family: 'UST', tenor: '2Y' },
            { curve_family: 'DE_BUND', tenor: '2Y' },
          ],
        },
      },
    ],
    tool_count: 1,
  });
  const list = decodePrimitiveList(ctx);
  // builders are excluded from list, BUT regression IS a builder
  // (hasModelMetadata).  Use decodePrimitiveContext instead.
  const decoded = decodePrimitiveContext(ctx);
  assertTruthy(decoded, 'decoded non-null');
  assertEqual(decoded!.kind, 'builder', 'rolling_regression → builder');
  // Flat projection drops nested entries (backward-compat).
  assertEqual(
    decoded!.params,
    { regression_window_days: '252' },
    'flat params: scalar only',
  );
  // Structured projection preserves nested entries verbatim.
  const struct = decoded!.paramsStructured;
  assertEqual(struct.regression_window_days, 252, 'window_days preserved');
  assertEqual(
    struct.target_spec,
    { curve_family: 'UST', tenor: '10Y', field_name: 'YLD_YTM_MID' },
    'target_spec object preserved',
  );
  assertEqual(
    struct.regressor_specs,
    [
      { curve_family: 'UST', tenor: '2Y' },
      { curve_family: 'DE_BUND', tenor: '2Y' },
    ],
    'regressor_specs array preserved',
  );
});

check('§A · null params get dropped from both projections', () => {
  const ctx = encodeContext({
    tools: [
      {
        tool: 'calculate_curve_spread_tool',
        params: {
          curve_family: 'UST',
          short_tenor: null,
          long_tenor: '10Y',
        },
      },
    ],
    tool_count: 1,
  });
  const list = decodePrimitiveList(ctx);
  assertEqual(list.length, 1, 'one');
  assertFalsy('short_tenor' in list[0].params, 'null dropped from params');
  assertFalsy(
    'short_tenor' in list[0].paramsStructured,
    'null dropped from structured',
  );
});

check('§A · arrays of primitives survive in paramsStructured', () => {
  // PCA tenors are an array of strings (multi_tenor control).
  const ctx = encodeContext({
    tools: [
      {
        tool: 'calculate_pca_yield_curve_tool',
        params: {
          curve_family: 'UST',
          tenors: ['2Y', '5Y', '10Y', '30Y'],
          n_components: 3,
        },
      },
    ],
    tool_count: 1,
  });
  const decoded = decodePrimitiveContext(ctx);
  assertTruthy(decoded, 'decoded');
  assertEqual(decoded!.kind, 'builder', 'PCA → builder');
  assertEqual(
    decoded!.paramsStructured.tenors,
    ['2Y', '5Y', '10Y', '30Y'],
    'tenors array preserved',
  );
  // Flat projection drops the array.
  assertFalsy('tenors' in decoded!.params, 'array dropped from flat');
});

// ============================================================================
// §B — handoffSignal: explicit marker vs default
// ============================================================================

check('§B · handoff=ask → isAskHandoff true', () => {
  const sp = new URLSearchParams('handoff=ask&context=abc');
  assertEqual(isAskHandoff(sp), true, 'explicit ask');
  assertEqual(parseHandoffOrigin(sp), 'ask', 'parser returns ask');
});

check('§B · no handoff param → defaults to library', () => {
  // Backward compat: pre-PR-B-β URLs without the marker preserve the
  // existing silent-defaults policy.
  const sp = new URLSearchParams('context=abc');
  assertEqual(isAskHandoff(sp), false, 'no marker → not ask');
  assertEqual(parseHandoffOrigin(sp), 'library', 'defaults to library');
});

check('§B · handoff=library → library', () => {
  const sp = new URLSearchParams('handoff=library');
  assertEqual(isAskHandoff(sp), false, 'explicit library');
});

check('§B · handoff=unrecognised → library (defensive default)', () => {
  // Garbage values default to library so a malformed link can't
  // accidentally invoke the strict missing-param policy on a Library
  // user.
  const sp = new URLSearchParams('handoff=foo');
  assertEqual(isAskHandoff(sp), false, 'unrecognised → library');
});

check('§B · null / undefined / empty source → library', () => {
  assertEqual(isAskHandoff(null), false, 'null');
  assertEqual(isAskHandoff(undefined), false, 'undefined');
  assertEqual(isAskHandoff(''), false, 'empty string');
});

check('§B · accepts a raw query-string fragment', () => {
  assertEqual(isAskHandoff('handoff=ask'), true, 'string parses');
});

check('§B · handoffQueryFragment composes correctly', () => {
  assertEqual(
    handoffQueryFragment('ask'),
    `${HANDOFF_QUERY_PARAM}=${HANDOFF_ASK_VALUE}`,
    'ask fragment',
  );
  assertEqual(handoffQueryFragment('library'), '', 'library = no fragment');
});

// ============================================================================
// §C — requiredParamsFor / missingRequiredTypedParams
// ============================================================================

check('§C · regime requires curve_family, front_tenor, back_tenor', () => {
  assertEqual(
    [...requiredParamsFor('regime')].sort(),
    ['back_tenor', 'curve_family', 'front_tenor'],
    'regime required',
  );
});

check('§C · spread requires curve_family, short_tenor, long_tenor', () => {
  assertEqual(
    [...requiredParamsFor('spread')].sort(),
    ['curve_family', 'long_tenor', 'short_tenor'],
    'spread required',
  );
});

check('§C · scanner / forward have no required params', () => {
  assertEqual([...requiredParamsFor('scanner')], [], 'scanner empty');
  assertEqual([...requiredParamsFor('forward')], [], 'forward empty');
});

check('§C · missingRequiredTypedParams flags every empty required field', () => {
  const out = missingRequiredTypedParams('regime', {
    curve_family: 'UST',
    // front_tenor missing
    back_tenor: '10Y',
  });
  assertEqual(out, ['front_tenor'], 'one missing');
});

check('§C · missingRequiredTypedParams returns [] when all present', () => {
  const out = missingRequiredTypedParams('regime', {
    curve_family: 'UST',
    front_tenor: '2Y',
    back_tenor: '10Y',
  });
  assertEqual(out, [], 'all present');
});

check('§C · empty-string values count as missing', () => {
  const out = missingRequiredTypedParams('yield', {
    curve_family: '',
    tenor: '10Y',
  });
  assertEqual(out, ['curve_family'], 'empty string = missing');
});

check('§C · undefined input → all required fields missing', () => {
  const out = missingRequiredTypedParams('yield', undefined);
  assertEqual(out.sort(), ['curve_family', 'tenor'], 'all missing');
});

// ============================================================================
// §D — Source-level contracts on the consumer files
// ============================================================================

check('§D · MultiPrimitiveCard imports missing-param helpers + MissingParamsCard', async () => {
  const src = await loadSource(
    'src/components/build/primitive/MultiPrimitiveCard.tsx',
  );
  assertContains(src, 'missingRequiredTypedParams', 'imports validator');
  assertContains(src, 'MissingParamsCard', 'imports the tile');
  assertContains(
    src,
    'askHandoff && missingForAsk.length > 0',
    'gates on askHandoff AND missing',
  );
});

check('§D · MultiPrimitiveCard skips the fetch when missing-param tile would render', async () => {
  const src = await loadSource(
    'src/components/build/primitive/MultiPrimitiveCard.tsx',
  );
  assertContains(src, 'shouldFetch', 'tracks shouldFetch flag');
  assertContains(src, '!(askHandoff && missingForAsk.length > 0)', 'flag derivation');
});

check('§D · VirtualPrimitiveCanvas takes askHandoff prop + threads to body', async () => {
  const src = await loadSource(
    'src/components/build/primitive/VirtualPrimitiveCanvas.tsx',
  );
  assertContains(src, 'askHandoff', 'prop present');
  assertContains(src, 'MissingParamsCard', 'imports tile');
  assertContains(src, 'missingForAsk', 'computes missing');
  assertContains(
    src,
    'if (missingForAsk.length > 0)',
    'body branches on missing',
  );
});

check('§D · MultiPrimitiveCanvas threads askHandoff into each card', async () => {
  const src = await loadSource(
    'src/components/build/primitive/MultiPrimitiveCanvas.tsx',
  );
  assertContains(src, 'askHandoff', 'prop present');
  assertContains(
    src,
    'askHandoff={askHandoff}',
    'forwarded to each MultiPrimitiveCard',
  );
});

check('§D · BuildShell.ContextCanvasRouter reads handoff URL marker', async () => {
  const src = await loadSource('src/components/build/BuildShell.tsx');
  assertContains(src, 'isAskHandoff', 'imports the helper');
  assertContains(src, 'askHandoff={askHandoff}', 'passes to canvases');
});

check('§D · ActionRow appends handoff=ask to context URLs', async () => {
  const src = await loadSource(
    'src/components/ask/messages/ActionRow.tsx',
  );
  assertContains(src, '&handoff=ask', 'ActionRow sets the marker');
});

check('§D · MissingParamsCard navigates with handoff=ask preserved', async () => {
  const src = await loadSource(
    'src/components/build/primitive/MissingParamsCard.tsx',
  );
  assertContains(src, 'HANDOFF_QUERY_PARAM', 'imports the constant');
  assertContains(src, 'HANDOFF_ASK_VALUE', 'imports the literal');
  assertContains(
    src,
    'handleConfigure',
    'has the configure-and-open handler',
  );
});

// ============================================================================
// §E — Multi-card identity chip (call N of M)
// ============================================================================

check('§E · MultiPrimitiveCanvas exports CallMeta type', async () => {
  const src = await loadSource(
    'src/components/build/primitive/MultiPrimitiveCanvas.tsx',
  );
  assertContains(src, 'export interface CallMeta', 'CallMeta exported');
  assertContains(src, 'computeCallIndex', 'computation present');
});

check('§E · MultiPrimitiveCard renders the chip via data-testid', async () => {
  const src = await loadSource(
    'src/components/build/primitive/MultiPrimitiveCard.tsx',
  );
  assertContains(
    src,
    'data-testid="multi-card-call-chip"',
    'chip test hook',
  );
  assertContains(src, 'call {callMeta.n} of {callMeta.m}', 'chip label');
});

// ============================================================================
// §F — End-to-end round-trip: ActionRow encode → decoder
// ============================================================================

check('§F · realistic multi-tool Ask handoff round-trips with structured fidelity', () => {
  // Simulates ``ActionRow.resolveBuildHref`` building the URL from a
  // ``message.workspaceContext`` that includes both scalar + nested
  // params (the bug case PR-B-β was created to fix).
  const wc = {
    tools: [
      {
        tool: 'calculate_cross_market_spread_tool',
        params: {
          curve_family_1: 'UST',
          curve_family_2: 'DE_BUND',
          tenor: '10Y',
          lookback_days: 252,
        },
      },
      {
        tool: 'calculate_cross_market_spread_tool',
        params: {
          curve_family_1: 'UST',
          curve_family_2: 'UK_GILT',
          tenor: '10Y',
          lookback_days: 252,
        },
      },
      {
        tool: 'calculate_rolling_regression_tool',
        params: {
          regression_window_days: 60,
          target_spec: { curve_family: 'UST', tenor: '10Y' },
          regressor_specs: [{ curve_family: 'DE_BUND', tenor: '10Y' }],
        },
      },
    ],
    tool_count: 3,
  };
  const ctx = encodeContext(wc);
  const list = decodePrimitiveList(ctx);
  // Rolling regression is a builder → filtered out of the list path.
  assertEqual(list.length, 2, 'two non-builder entries in list');
  assertEqual(list[0].toolName, 'calculate_cross_market_spread_tool', 'first');
  assertEqual(list[1].toolName, 'calculate_cross_market_spread_tool', 'second');
  assertEqual(
    (list[0] as { params: Record<string, string> }).params.curve_family_2,
    'DE_BUND',
    'first card preserves Bund',
  );
  assertEqual(
    (list[1] as { params: Record<string, string> }).params.curve_family_2,
    'UK_GILT',
    'second card preserves Gilt',
  );
  // Builder (rolling regression) accessible via decodePrimitiveContext.
  const best = decodePrimitiveContext(ctx);
  assertEqual(best!.kind, 'builder', 'builder wins single-best lookup');
  assertEqual(
    (best!.paramsStructured.target_spec as Record<string, unknown>)
      .curve_family,
    'UST',
    'nested target_spec preserved',
  );
});

check('§F · empty regime params → missing-param check would fire', () => {
  // Empty params for a typed view (regime: classify_curve_move) → without
  // PR-B-β this defaults silently.  PR-B-β reports the missing fields explicitly.
  const out = missingRequiredTypedParams('regime', {});
  assertEqual(
    out.sort(),
    ['back_tenor', 'curve_family', 'front_tenor'],
    'all three required fields flagged',
  );
});

// ============================================================================
// Runner
// ============================================================================

export async function runAllParamFidelityTests(): Promise<void> {
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
    `\nparam-fidelity coverage: ${passed} passed, ${failed} failed`,
  );
  if (failed > 0) {
    throw new Error(`${failed} param-fidelity check(s) failed`);
  }
}

const _meta = (import.meta as unknown) as { main?: boolean };
if (_meta && _meta.main) {
  void runAllParamFidelityTests();
}
