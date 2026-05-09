// ============================================================================
// RatesAgentPage — same widget engine, rates-deep defaults
// ----------------------------------------------------------------------------
// Identical structure to MonitorPage but:
//   - Uses `useWidgetLayout('rates', ...)` so the layout is persisted
//     independently from Monitor.
//   - Default layout is rates-deep: snapshot + scanner + cross-market
//     + curve shapes + regime + a UST 2s10s spread chart + a BTP-Bund
//     cross-market spread + four single-yield level cards.
//   - Header kicker says "RATES AGENT · SOVEREIGN + OIS" and the
//     headline accent points at rates-domain depth.
//
// Sharing the same engine guarantees that Monitor → Rates Agent feel
// like the same tool, just with different defaults.  Future agent
// pages (FX, Credit, etc.) will adopt this same skeleton when their
// data lands.
// ============================================================================

import { useCallback, useMemo, useState } from 'react';
import { useWidgetLayout } from '@/hooks/useWidgetLayout';
import {
  RatesDataProvider,
  useRatesDataContext,
} from '@/components/monitor/RatesDataProvider';
import { MonitorHeader } from '@/components/monitor/MonitorHeader';
import { WidgetGrid } from '@/components/monitor/WidgetGrid';
import { WidgetCatalogModal } from '@/components/monitor/WidgetCatalogModal';
import { defaultRatesAgentLayout } from '@/components/monitor/defaults';
import type { WidgetInstance } from '@/components/monitor/registry';

export function RatesAgentPage() {
  const defaultLayout = useMemo(() => defaultRatesAgentLayout(), []);

  return (
    <RatesDataProvider>
      <RatesAgentInner defaultLayout={defaultLayout} />
    </RatesDataProvider>
  );
}

type InnerProps = {
  defaultLayout: ReturnType<typeof defaultRatesAgentLayout>;
};

function RatesAgentInner({ defaultLayout }: InnerProps) {
  const { layout, addWidget, removeWidget, updateWidget, resetLayout } =
    useWidgetLayout('rates', defaultLayout);

  const [isEditing, setIsEditing] = useState(false);
  const [modalState, setModalState] = useState<
    | { kind: 'closed' }
    | { kind: 'add' }
    | { kind: 'configure'; instance: WidgetInstance }
  >({ kind: 'closed' });

  const handleConfigure = useCallback(
    (instanceId: string) => {
      const instance = layout.widgets.find((w) => w.id === instanceId);
      if (!instance) return;
      setModalState({ kind: 'configure', instance });
    },
    [layout.widgets],
  );

  const headline = useRatesHeadline();

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-[1680px] px-6 py-7 lg:px-8 lg:py-8 3xl:max-w-[1880px] 3xl:px-10">
        <MonitorHeader
          kicker={headline.kicker}
          headlinePrefix={headline.headlinePrefix}
          headlineAccent={headline.headlineAccent}
          headlineSuffix={headline.headlineSuffix}
          subline={headline.subline}
          isEditing={isEditing}
          onToggleEdit={() => setIsEditing((v) => !v)}
          onResetDefault={isEditing ? resetLayout : undefined}
        />
        <WidgetGrid
          layout={layout}
          isEditing={isEditing}
          onRemove={removeWidget}
          onConfigure={handleConfigure}
          onAddWidget={() => setModalState({ kind: 'add' })}
        />
      </div>

      {modalState.kind === 'add' && (
        <WidgetCatalogModal
          open
          mode="add"
          onAdd={(typeId, overrides) => addWidget(typeId, overrides)}
          onClose={() => setModalState({ kind: 'closed' })}
        />
      )}
      {modalState.kind === 'configure' && (
        <WidgetCatalogModal
          open
          mode="configure"
          instance={modalState.instance}
          onSave={(id, patch) => updateWidget(id, patch)}
          onClose={() => setModalState({ kind: 'closed' })}
        />
      )}
    </div>
  );
}

// ----------------------------------------------------------------------------

function useRatesHeadline() {
  const { data } = useRatesDataContext();
  const dayKicker = useMemo(() => {
    const d = new Date();
    return d
      .toLocaleDateString('en-US', {
        weekday: 'long',
        month: 'short',
        day: 'numeric',
      })
      .toUpperCase();
  }, []);

  const flagged = data?.scanner.results.length ?? null;
  const subline = data
    ? `Sovereign + OIS · ${data.yieldSnapshot.curve_families.length} curves · ${data.yieldSnapshot.tenors.length} tenors · TimescaleDB`
    : 'Loading rates data…';

  return {
    kicker: `${dayKicker} · RATES AGENT · SOVEREIGN + OIS`,
    headlinePrefix: 'The',
    headlineAccent: 'curve',
    headlineSuffix:
      flagged === null
        ? '.'
        : flagged === 0
          ? ' is calm.'
          : `, with ${flagged} ${flagged === 1 ? 'extreme' : 'extremes'} flagged.`,
    subline,
  };
}
