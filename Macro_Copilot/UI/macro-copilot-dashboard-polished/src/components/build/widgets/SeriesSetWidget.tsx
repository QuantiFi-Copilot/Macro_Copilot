// ============================================================================
// SeriesSetWidget — payload-backed renderer for SeriesSet artifacts.
// ----------------------------------------------------------------------------
// PR4 — loads the persisted SeriesSet body via the PR3 hook and lets
// the user switch between member series in-place.  Before PR4 the
// widget rendered only the lead member as an opaque sparkline with no
// way to see which other instruments / curves / tenors were on the
// card.  The user audit's "I can't tell what's in this set" complaint.
//
// Layout
// ------
//   - Member-key strip: chip-style selector listing every member; the
//     active member is highlighted.  For long sets (>= 8 members) we
//     route through a ``<select>`` to keep the card compact.
//   - Active member preview: reuses the SeriesWidget machinery via
//     ``seriesSetMemberAsSeries`` — same sparkline + stats strip the
//     single-Series card uses, so the visual register is identical.
//
// Empty-state contract
// --------------------
// A SeriesSet with zero members renders an honest "set persisted with
// no members" caption.  A member with zero finite observations
// renders the same caption SeriesWidget uses.
// ============================================================================

import { useMemo, useState } from 'react';
import type { NodeRenderer } from '@/components/build/lib/nodeRendererRegistry';
import { registerArtifactRenderer } from '@/components/build/lib/nodeRendererRegistry';
import { Sparkline } from '@/components/ui/Sparkline';
import type { ChartTone } from '@/lib/chart';
import { PayloadShell } from './shared/PayloadShell';
import {
  firstSeriesObservation,
  formatDate,
  formatNumberWithUnits,
  lastSeriesObservation,
  seriesFiniteCount,
  seriesObservationCount,
  seriesSetMemberAsSeries,
  seriesSetMembers,
  type SeriesSetPayloadEnvelope,
} from './shared/artifactFormat';
import type { StageCategory } from '@/components/build/lib/buildTypes';
import { cn } from '@/utils/cn';

const TONE_BY_CATEGORY: Record<StageCategory, ChartTone> = {
  input: 'blue',
  transform: 'rates',
  output: 'amber',
};

/** Switch from chip-list to <select> when there are this many members
 *  or more.  Keeps the card compact for sovereign-yield panels (10+
 *  countries) without losing the inline preview affordance. */
const SELECT_FALLBACK_THRESHOLD = 8;

const SeriesSetWidget: NodeRenderer = ({ node, artifact, category, size }) => {
  return (
    <PayloadShell<SeriesSetPayloadEnvelope>
      artifactHash={node.artifact_hash ?? artifact.hash}
      expectedType="SeriesSet"
      displayName="SeriesSet"
      isEmpty={(p) => seriesSetMembers(p).length === 0}
      emptyMessage="The series set persisted but contains no member series."
    >
      {(payload) => (
        <SeriesSetBody payload={payload} category={category} size={size} />
      )}
    </PayloadShell>
  );
};

function SeriesSetBody({
  payload,
  category,
  size,
}: {
  payload: SeriesSetPayloadEnvelope;
  category: StageCategory;
  size: 'small' | 'medium' | 'wide' | 'tall';
}) {
  const members = useMemo(() => seriesSetMembers(payload), [payload]);
  const [activeKey, setActiveKey] = useState<string>(members[0] ?? '');

  // Defensive: if the payload changes under us (e.g. cache refresh)
  // and the active member is gone, fall back to the first available.
  const effectiveKey =
    activeKey && members.includes(activeKey) ? activeKey : members[0] ?? '';

  const memberPayload = useMemo(
    () =>
      effectiveKey ? seriesSetMemberAsSeries(payload, effectiveKey) : null,
    [payload, effectiveKey],
  );

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 flex-col gap-1.5 px-5 pt-3 pb-2">
        <div className="kicker text-fg-muted">
          {members.length} member series
        </div>
        {members.length >= SELECT_FALLBACK_THRESHOLD ? (
          <MemberSelect
            members={members}
            activeKey={effectiveKey}
            onChange={setActiveKey}
          />
        ) : (
          <MemberChipStrip
            members={members}
            activeKey={effectiveKey}
            onChange={setActiveKey}
          />
        )}
      </div>

      {memberPayload && (
        <MemberPreview
          memberPayload={memberPayload}
          category={category}
          size={size}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Member selectors
// ---------------------------------------------------------------------------

function MemberChipStrip({
  members,
  activeKey,
  onChange,
}: {
  members: string[];
  activeKey: string;
  onChange: (key: string) => void;
}) {
  return (
    <div className="flex flex-wrap gap-1">
      {members.map((key) => (
        <button
          key={key}
          type="button"
          onClick={() => onChange(key)}
          className={cn(
            'rounded-sm border px-1.5 py-0.5 font-mono text-[10px] transition-colors',
            key === activeKey
              ? 'border-ice-400/45 bg-ice-500/15 text-ice-100'
              : 'border-line-soft bg-white/[0.025] text-fg-secondary hover:border-ice-400/30 hover:text-ice-200',
          )}
        >
          {key}
        </button>
      ))}
    </div>
  );
}

function MemberSelect({
  members,
  activeKey,
  onChange,
}: {
  members: string[];
  activeKey: string;
  onChange: (key: string) => void;
}) {
  return (
    <select
      value={activeKey}
      onChange={(e) => onChange(e.target.value)}
      className="cursor-pointer rounded-md border border-line-soft bg-ink-900/60 px-2 py-1 font-mono text-[11px] text-fg-primary transition-colors hover:border-ice-400/40 focus:border-ice-400/55 focus:outline-none"
    >
      {members.map((key) => (
        <option key={key} value={key}>
          {key}
        </option>
      ))}
    </select>
  );
}

// ---------------------------------------------------------------------------
// Per-member preview — same shape SeriesWidget renders for a single
// Series.  We don't import SeriesWidget itself (cycle risk + extra
// PayloadShell wrap); instead we inline the same primitives.
// ---------------------------------------------------------------------------

function MemberPreview({
  memberPayload,
  category,
  size,
}: {
  memberPayload: ReturnType<typeof seriesSetMemberAsSeries>;
  category: StageCategory;
  size: 'small' | 'medium' | 'wide' | 'tall';
}) {
  if (!memberPayload) return null;
  const units = memberPayload.metadata.units;
  const last = lastSeriesObservation(memberPayload);
  const first = firstSeriesObservation(memberPayload);
  const totalObs = seriesObservationCount(memberPayload);
  const finiteObs = seriesFiniteCount(memberPayload);

  const sparklineData = useMemo(() => {
    const idx = memberPayload.payload.index ?? [];
    const vs = memberPayload.payload.values ?? [];
    const out: { value: number; index: string }[] = [];
    for (let i = 0; i < vs.length; i++) {
      const v = vs[i];
      if (v === null || v === undefined || !Number.isFinite(v)) continue;
      out.push({ value: v, index: idx[i] ?? String(i) });
    }
    return out;
  }, [memberPayload]);

  if (finiteObs === 0) {
    return (
      <div className="px-5 pt-2 pb-4">
        <p className="text-[10.5px] leading-[1.5] text-fg-secondary">
          This member series has zero finite observations.
        </p>
      </div>
    );
  }

  const tone = TONE_BY_CATEGORY[category];
  return (
    <>
      {last && (
        <div className="px-5 pt-1">
          <div className="flex items-baseline gap-2">
            <span className="font-mono text-[16px] tabular-nums text-fg-primary">
              {formatNumberWithUnits(last.value, units)}
            </span>
            <span className="font-mono text-[10px] text-fg-faint">
              as-of {formatDate(last.date)}
            </span>
          </div>
        </div>
      )}
      {sparklineData.length >= 2 && (
        <Sparkline
          data={sparklineData}
          tone={tone}
          mode="area"
          height={size === 'small' ? 52 : 84}
        />
      )}
      <div className="grid gap-x-4 gap-y-1.5 px-5 pt-2 pb-3 grid-cols-2 sm:grid-cols-4">
        <MetaCell
          label="First date"
          value={first ? formatDate(first.date) : '—'}
        />
        <MetaCell
          label="Last date"
          value={last ? formatDate(last.date) : '—'}
        />
        <MetaCell label="Obs" value={totalObs.toLocaleString()} />
        <MetaCell label="Finite" value={finiteObs.toLocaleString()} />
      </div>
    </>
  );
}

function MetaCell({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <span className="text-[9.5px] font-medium uppercase tracking-[0.16em] text-fg-faint">
        {label}
      </span>
      <span className="truncate font-mono text-[12px] tabular-nums text-fg-secondary">
        {value}
      </span>
    </div>
  );
}

registerArtifactRenderer('SeriesSet', SeriesSetWidget);
export { SeriesSetWidget };
