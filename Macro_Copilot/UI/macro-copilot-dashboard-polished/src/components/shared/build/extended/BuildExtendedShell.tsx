// ============================================================================
// shared/build/extended/BuildExtendedShell.tsx — the extended Build canvas shell.
// ----------------------------------------------------------------------------
// Composes all the section components (category strip → identity row →
// top-right cards → controls strip → KPI strip → main chart →
// stretch-context panel → methodology card → lineage footer) into the
// full extended canvas per docs_revamped/03_standards/rendering_density.md §2.1.
//
// Finance-blind — accepts BuildExtendedShellProps descriptors and
// renders.  Per-tool wrappers (e.g.
// modules/primitives/get_real_yield_level_tool/surfaces/BuildExtended.tsx)
// supply the descriptors after fetching data from the tool's typed-
// detail endpoint.
// ============================================================================

import { AlertCircle, Loader2 } from 'lucide-react';
import type { BuildExtendedShellProps } from '../lib/types';
import { ControlsStrip } from './ControlsStrip';
import { KPIStrip } from './KPIStrip';
import { LineageFooter } from './LineageFooter';
import { MainChart } from './MainChart';
import { MethodologyCard } from './MethodologyCard';
import { StretchContextCard } from './StretchContextCard';

export function BuildExtendedShell({
  category,
  identity,
  topRightCards,
  controls,
  onControlChange,
  onResetControls,
  kpis,
  chartPoints,
  chartUnit,
  chartValueDecimals = 2,
  referenceBands,
  stretchContext,
  methodology,
  methodologyReferences,
  lineage,
  isLoading,
  errorMessage,
}: BuildExtendedShellProps) {
  return (
    <div
      className="flex h-full min-h-0 flex-col overflow-y-auto"
      data-testid="build-extended-shell"
    >
      {/* ---------- Title section ---------- */}
      <section className="grid grid-cols-1 gap-4 border-b border-line-subtle px-6 pt-5 pb-5 lg:grid-cols-[1fr_auto]">
        <div className="flex flex-col gap-2">
          <CategoryRow category={category} />
          <IdentityBlock identity={identity} />
        </div>
        {topRightCards && topRightCards.length > 0 && (
          <div className="grid grid-flow-col auto-cols-[minmax(180px,1fr)] gap-3">
            {topRightCards.map((card) => (
              <div key={card.key}>{card.node}</div>
            ))}
          </div>
        )}
      </section>

      {/* ---------- Controls strip ---------- */}
      <div className="px-6 pt-4">
        <ControlsStrip
          controls={controls}
          onChange={onControlChange}
          onReset={onResetControls}
        />
      </div>

      {/* ---------- KPI strip ---------- */}
      <div className="px-6 pt-3">
        <KPIStrip kpis={kpis} />
      </div>

      {/* ---------- Chart + Stretch-context panel ---------- */}
      <div className="grid grid-cols-1 gap-3 px-6 pt-3 lg:grid-cols-[3fr_2fr]">
        <div>
          {errorMessage ? (
            <ChartError message={errorMessage} />
          ) : isLoading ? (
            <ChartLoading />
          ) : (
            <MainChart
              points={chartPoints}
              unit={chartUnit}
              valueDecimals={chartValueDecimals}
              referenceBands={referenceBands}
              header={`${category.name} HISTORY`}
            />
          )}
        </div>
        <div className="flex flex-col gap-3">
          {stretchContext && <StretchContextCard context={stretchContext} />}
          <MethodologyCard
            rows={methodology}
            references={methodologyReferences}
          />
        </div>
      </div>

      {/* ---------- Lineage footer (spans full width at bottom) ---------- */}
      <div className="mt-auto pt-3">
        <LineageFooter lineage={lineage} />
      </div>
    </div>
  );
}

function CategoryRow({
  category,
}: {
  category: BuildExtendedShellProps['category'];
}) {
  return (
    <div className="flex flex-wrap items-center gap-1.5 text-[11px] uppercase tracking-[0.05em]">
      <span className="font-semibold text-fg-secondary">{category.name}</span>
      {category.tags?.map((tag, i) => (
        <span key={`${tag}-${i}`} className="flex items-center gap-1.5 text-fg-muted">
          <span className="text-fg-faint" aria-hidden>·</span>
          <span>{tag}</span>
        </span>
      ))}
    </div>
  );
}

function IdentityBlock({
  identity,
}: {
  identity: BuildExtendedShellProps['identity'];
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <h1 className="flex items-baseline gap-3 text-[26px] font-medium leading-tight tracking-[-0.012em] text-fg-primary">
        <span>{identity.primary}</span>
        {identity.secondary && (
          <>
            <span className="text-fg-faint" aria-hidden>·</span>
            <span>{identity.secondary}</span>
          </>
        )}
        {identity.flag && (
          <span className="text-[22px] leading-none">{identity.flag}</span>
        )}
      </h1>
      {identity.subtitle && (
        <p className="text-[12.5px] leading-[1.55] text-fg-secondary">
          {identity.subtitle}
        </p>
      )}
      {(identity.asOfDate || identity.meta) && (
        <p className="flex flex-wrap items-center gap-2 text-[11.5px] text-fg-muted">
          {identity.asOfDate && <span>As of {identity.asOfDate}</span>}
          {identity.meta && (
            <>
              <span className="text-fg-faint" aria-hidden>·</span>
              <span>{identity.meta}</span>
            </>
          )}
        </p>
      )}
    </div>
  );
}

function ChartLoading() {
  return (
    <section className="card flex h-[400px] flex-col items-center justify-center gap-3">
      <Loader2 size={16} className="animate-spin text-ice-300" />
      <span className="text-[12px] text-fg-muted">Loading chart…</span>
    </section>
  );
}

function ChartError({ message }: { message: string }) {
  return (
    <section className="card flex h-[400px] flex-col items-center justify-center gap-3 px-6">
      <AlertCircle size={20} className="text-coral-300" />
      <p className="max-w-[420px] text-center text-[12px] leading-[1.5] text-fg-secondary">
        {message}
      </p>
    </section>
  );
}
