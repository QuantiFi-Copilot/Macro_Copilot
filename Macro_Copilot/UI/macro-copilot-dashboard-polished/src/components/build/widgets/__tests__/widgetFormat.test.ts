/* eslint-disable @typescript-eslint/no-explicit-any */
// ============================================================================
// widgetFormat.test.ts — PR4 shared-helper assertions.
// ----------------------------------------------------------------------------
// Locks the load-bearing invariants of ``shared/artifactFormat.ts``:
//
//   - ``formatDate`` NEVER returns ``"1970-01-01"`` for invalid input
//     (the bug pattern Codex's audit flagged).
//   - Number formatters route unit kinds correctly + drop the suffix
//     when ``withUnit: false``.
//   - Payload pickers count events from ``event_dates`` (not
//     ``row_count``), pick series obs from the index, return SeriesSet
//     members in insertion order, and validate Panel cell bounds.
//
// Pattern matches the PR1/PR2/PR3 ``check`` shim style — no test
// runner needed, esbuild bundles into Node for execution.
// ============================================================================

import {
  eventCountFromPayload,
  firstSeriesObservation,
  formatDate,
  formatNumber,
  formatNumberWithUnits,
  lastSeriesObservation,
  MISSING_VALUE_DASH,
  panelCell,
  panelColumns,
  panelRowIndex,
  seriesFiniteCount,
  seriesObservationCount,
  seriesSetMemberAsSeries,
  seriesSetMembers,
  tradeRowCount,
  unitKind,
  windowedEventCount,
  windowedOffsets,
} from '../shared/artifactFormat';
import type {
  EventSetPayloadEnvelope,
  PanelPayloadEnvelope,
  SeriesPayloadEnvelope,
  SeriesSetPayloadEnvelope,
  TradeSetPayloadEnvelope,
  WindowedPanelPayloadEnvelope,
} from '@/types/artifacts';

type Check = { label: string; fn: () => void };
const _checks: Check[] = [];

function check(label: string, fn: () => void): void {
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

// ----------------------------------------------------------------------------
// formatDate — defensive against the 1970-epoch fallback bug
// ----------------------------------------------------------------------------

check('formatDate: ISO-8601 date → YYYY-MM-DD', () => {
  assertEqual(formatDate('2025-03-14'), '2025-03-14', 'iso date');
  assertEqual(
    formatDate('2025-03-14T00:00:00Z'),
    '2025-03-14',
    'iso datetime',
  );
});

check('formatDate: null / undefined / empty → MISSING_VALUE_DASH', () => {
  assertEqual(formatDate(null), MISSING_VALUE_DASH, 'null');
  assertEqual(formatDate(undefined), MISSING_VALUE_DASH, 'undefined');
  assertEqual(formatDate(''), MISSING_VALUE_DASH, 'empty');
});

check('formatDate: unparseable → MISSING_VALUE_DASH (NOT 1970)', () => {
  assertEqual(formatDate('not-a-date'), MISSING_VALUE_DASH, 'garbage');
  assertEqual(formatDate({}), MISSING_VALUE_DASH, 'object');
  assertEqual(formatDate(NaN), MISSING_VALUE_DASH, 'NaN');
});

check('formatDate: epoch-ms only when explicitly opted in', () => {
  assertEqual(formatDate(1700000000000), MISSING_VALUE_DASH, 'no flag');
  assertEqual(
    formatDate(1700000000000, { epochMs: true }),
    '2023-11-14',
    'flag enabled',
  );
});

check('formatDate: rejectEpochSentinel optionally suppresses 1970-01-01', () => {
  // Default keeps the legitimate sentinel intact (e.g. event-offset
  // anchors).
  assertEqual(formatDate('1970-01-01'), '1970-01-01', 'default keep');
  assertEqual(
    formatDate('1970-01-01', { rejectEpochSentinel: true }),
    MISSING_VALUE_DASH,
    'reject',
  );
});

// ----------------------------------------------------------------------------
// formatNumber — unit routing + decimals
// ----------------------------------------------------------------------------

check('formatNumber: percent suffix', () => {
  assertEqual(formatNumber(4.321, { unit: 'percent' }), '4.32%', 'percent');
});

check('formatNumber: bps suffix', () => {
  assertEqual(formatNumber(12.5, { unit: 'bps' }), '12.5 bps', 'bps');
});

check('formatNumber: z_score suffix', () => {
  assertEqual(formatNumber(1.234, { unit: 'z_score' }), '1.23σ', 'z');
});

check('formatNumber: count drops decimals', () => {
  assertEqual(formatNumber(1281, { unit: 'count' }), '1,281', 'count');
});

check('formatNumber: missing input → MISSING_VALUE_DASH', () => {
  assertEqual(formatNumber(null), MISSING_VALUE_DASH, 'null');
  assertEqual(formatNumber(NaN), MISSING_VALUE_DASH, 'NaN');
  assertEqual(formatNumber(undefined), MISSING_VALUE_DASH, 'undefined');
  assertEqual(formatNumber('not a number'), MISSING_VALUE_DASH, 'string');
});

check('formatNumber: withUnit:false drops the suffix', () => {
  assertEqual(
    formatNumber(4.321, { unit: 'percent', withUnit: false }),
    '4.32',
    'no suffix',
  );
});

check('unitKind: maps backend strings to formatter discriminator', () => {
  assertEqual(unitKind('bps'), 'bps', 'bps');
  assertEqual(unitKind('percent'), 'percent', 'percent');
  assertEqual(unitKind('YIELD_PCT'), 'percent', 'yield_pct (case-insens)');
  assertEqual(unitKind('z_score'), 'z_score', 'z');
  assertEqual(unitKind('integer'), 'count', 'integer→count');
  assertEqual(unitKind('USD'), 'currency', 'usd→currency');
  assertEqual(unitKind(undefined), 'decimal', 'undef→decimal');
  assertEqual(unitKind('unknown'), 'decimal', 'unknown→decimal');
});

check('formatNumberWithUnits: bps unit string', () => {
  assertEqual(formatNumberWithUnits(12.5, 'bps'), '12.5 bps', 'bps');
});

// ----------------------------------------------------------------------------
// Fixtures for payload-shape pickers
// ----------------------------------------------------------------------------

function seriesEnv(): SeriesPayloadEnvelope {
  return {
    artifact_type: 'Series',
    metadata: {
      series_key: 'UST/10Y',
      units: 'percent',
      frequency: 'B',
      missingness_policy: {},
      lineage: { steps: [] },
    },
    payload: {
      index: ['2025-01-01', '2025-01-02', '2025-01-03'],
      values: [4.0, null, 4.2],
      name: 'UST/10Y',
      type: 'Series',
    },
  };
}

function seriesSetEnv(): SeriesSetPayloadEnvelope {
  return {
    artifact_type: 'SeriesSet',
    metadata: {
      units_by_key: { UST_10Y: 'percent', BUND_10Y: 'percent' },
      missingness_by_key: {},
      upstream_lineage_by_key: {},
      frequency: 'B',
      lineage: { steps: [] },
    },
    payload: {
      common_index: ['2025-01-01', '2025-01-02'],
      series_by_key: {
        UST_10Y: [4.0, 4.05],
        BUND_10Y: [2.5, 2.55],
      },
    },
  };
}

function eventSetEnv(): EventSetPayloadEnvelope {
  return {
    artifact_type: 'EventSet',
    metadata: {
      source_series_key: 'CPI/yoy',
      frequency: 'B',
      lineage: { steps: [] },
    },
    payload: {
      mask_index: Array.from({ length: 1281 }, (_, i) =>
        new Date(2020, 0, 1 + i).toISOString().slice(0, 10),
      ),
      mask_values: Array.from({ length: 1281 }, (_, i) => i % 250 === 0),
      event_dates: ['2020-01-01', '2020-09-07', '2021-05-15', '2022-01-20'],
      per_event_metadata: [{ label: 'A' }, {}, { name: 'C' }, {}],
    },
  };
}

function panelEnv(rows = 3): PanelPayloadEnvelope {
  const index: string[] = [];
  const data: Array<Array<number | null>> = [];
  for (let i = 0; i < rows; i++) {
    index.push(`2025-01-0${i + 1}`);
    data.push([4 + i * 0.1, 2 + i * 0.05, null]);
  }
  return {
    artifact_type: 'Panel',
    metadata: {
      units_by_column: { UST_10Y: 'percent', BUND_10Y: 'percent', SPREAD: 'bps' },
      missingness_policy: {},
      lineage: { steps: [] },
    },
    payload: {
      index,
      columns: ['UST_10Y', 'BUND_10Y', 'SPREAD'],
      data,
    },
  };
}

function windowedPanelEnv(): WindowedPanelPayloadEnvelope {
  return {
    artifact_type: 'WindowedPanel',
    metadata: {
      offsets: [-5, -4, -3, -2, -1, 0, 1, 2, 3, 4, 5],
      target_series_key: 'UST/10Y',
      units: 'bps',
      lineage: { steps: [] },
    },
    payload: {
      data: [
        [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11],
        [2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12],
      ],
      event_dates: ['2024-03-13', '2024-09-18'],
      per_event_metadata: [{}, {}],
    },
  };
}

function tradeSetEnv(trades = 3): TradeSetPayloadEnvelope {
  const tradeArr = Array.from({ length: trades }, (_, i) => ({
    entry_date: `2024-0${(i % 9) + 1}-15`,
    exit_date: `2024-0${(i % 9) + 1}-22`,
    pnl: i % 2 === 0 ? 1500 + i * 100 : -300 - i * 50,
    return_pct: i % 2 === 0 ? 0.5 + i * 0.1 : -0.2,
  }));
  return {
    artifact_type: 'TradeSet',
    metadata: {
      source_event_key: 'fomc/decisions',
      methodology_policy: 'conservative',
      lineage: { steps: [] },
    },
    payload: { trades: tradeArr },
  };
}

// ----------------------------------------------------------------------------
// EventSet — the bug fix at the heart of PR4
// ----------------------------------------------------------------------------

check('eventCountFromPayload: returns event_dates.length, NOT mask length', () => {
  const env = eventSetEnv();
  assertEqual(env.payload.mask_index.length, 1281, 'fixture mask length');
  assertEqual(eventCountFromPayload(env), 4, 'event count from event_dates');
  // The mask has 1,281 entries; the OLD widget showed that as the
  // event count.  The new helper MUST return 4.
});

check('eventCountFromPayload: empty event_dates → 0', () => {
  const env = eventSetEnv();
  env.payload.event_dates = [];
  assertEqual(eventCountFromPayload(env), 0, 'no events');
});

check('eventCountFromPayload: malformed payload → 0', () => {
  const env = eventSetEnv();
  (env.payload as any).event_dates = null;
  assertEqual(eventCountFromPayload(env), 0, 'null event_dates');
});

// ----------------------------------------------------------------------------
// Series helpers
// ----------------------------------------------------------------------------

check('seriesObservationCount: index length', () => {
  assertEqual(seriesObservationCount(seriesEnv()), 3, '3 obs');
});

check('seriesFiniteCount: ignores null + NaN', () => {
  assertEqual(seriesFiniteCount(seriesEnv()), 2, '2 finite');
});

check('lastSeriesObservation: walks from the end past nulls', () => {
  const last = lastSeriesObservation(seriesEnv());
  assertEqual(last, { date: '2025-01-03', value: 4.2 }, 'last');
});

check('firstSeriesObservation: walks from the start past nulls', () => {
  const first = firstSeriesObservation(seriesEnv());
  assertEqual(first, { date: '2025-01-01', value: 4.0 }, 'first');
});

check('lastSeriesObservation: all nulls → null', () => {
  const env = seriesEnv();
  env.payload.values = [null, null, null];
  assertEqual(lastSeriesObservation(env), null, 'all null');
});

// ----------------------------------------------------------------------------
// SeriesSet helpers
// ----------------------------------------------------------------------------

check('seriesSetMembers: keys in insertion order', () => {
  assertEqual(seriesSetMembers(seriesSetEnv()), ['UST_10Y', 'BUND_10Y'], 'members');
});

check('seriesSetMemberAsSeries: produces a synthetic SeriesPayloadEnvelope', () => {
  const ss = seriesSetEnv();
  const member = seriesSetMemberAsSeries(ss, 'BUND_10Y');
  if (!member) throw new Error('member is null');
  assertEqual(member.artifact_type, 'Series', 'synthetic type');
  assertEqual(member.metadata.series_key, 'BUND_10Y', 'key');
  assertEqual(member.metadata.units, 'percent', 'units mapped');
  assertEqual(
    member.payload.index,
    ['2025-01-01', '2025-01-02'],
    'index from common_index',
  );
  assertEqual(member.payload.values, [2.5, 2.55], 'member values');
});

check('seriesSetMemberAsSeries: unknown key → null', () => {
  assertEqual(
    seriesSetMemberAsSeries(seriesSetEnv(), 'JP_JGB_10Y'),
    null,
    'unknown member',
  );
});

// ----------------------------------------------------------------------------
// Panel helpers
// ----------------------------------------------------------------------------

check('panelColumns: returns the column list', () => {
  assertEqual(
    panelColumns(panelEnv(3)),
    ['UST_10Y', 'BUND_10Y', 'SPREAD'],
    'columns',
  );
});

check('panelRowIndex: returns the row index', () => {
  assertEqual(
    panelRowIndex(panelEnv(3)),
    ['2025-01-01', '2025-01-02', '2025-01-03'],
    'rows',
  );
});

check('panelCell: returns the cell at (row, col)', () => {
  assertEqual(panelCell(panelEnv(3), 0, 0), 4, '(0,0)');
  assertEqual(panelCell(panelEnv(3), 2, 0), 4.2, '(2,0)');
  assertEqual(panelCell(panelEnv(3), 0, 2), null, 'null cell');
});

check('panelCell: out-of-bounds → null', () => {
  assertEqual(panelCell(panelEnv(3), 99, 0), null, 'row OOB');
  assertEqual(panelCell(panelEnv(3), 0, 99), null, 'col OOB');
});

// ----------------------------------------------------------------------------
// WindowedPanel helpers
// ----------------------------------------------------------------------------

check('windowedEventCount: from event_dates', () => {
  assertEqual(windowedEventCount(windowedPanelEnv()), 2, '2 events');
});

check('windowedOffsets: from metadata.offsets', () => {
  const off = windowedOffsets(windowedPanelEnv());
  assertEqual(off.length, 11, 'offset count');
  assertEqual(off[0], -5, 't-5');
  assertEqual(off[5], 0, 't0');
  assertEqual(off[10], 5, 't+5');
});

// ----------------------------------------------------------------------------
// TradeSet helpers
// ----------------------------------------------------------------------------

check('tradeRowCount: from trades.length', () => {
  assertEqual(tradeRowCount(tradeSetEnv(7)), 7, '7 trades');
  assertEqual(tradeRowCount(tradeSetEnv(0)), 0, '0 trades');
});

check('tradeRowCount: malformed payload → 0', () => {
  const env = tradeSetEnv(3);
  (env.payload as any).trades = null;
  assertEqual(tradeRowCount(env), 0, 'null trades');
});

// ----------------------------------------------------------------------------
// Runner
// ----------------------------------------------------------------------------

export function runAllWidgetFormatTests(): void {
  let passed = 0;
  let failed = 0;
  for (const { label, fn } of _checks) {
    try {
      fn();
      passed += 1;
    } catch (err) {
      failed += 1;
      // eslint-disable-next-line no-console
      console.error(`✗ ${label}\n  ${(err as Error).message}`);
    }
  }
  // eslint-disable-next-line no-console
  console.log(
    `\nwidget-format coverage: ${passed} passed, ${failed} failed`,
  );
  if (failed > 0) {
    throw new Error(`${failed} widget-format check(s) failed`);
  }
}

const _meta = (import.meta as unknown) as { main?: boolean };
if (_meta && _meta.main) {
  runAllWidgetFormatTests();
}
