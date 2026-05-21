// ============================================================================
// FXAgentPage — same widget engine, FX-deep defaults
// ----------------------------------------------------------------------------
// Identical structure to RatesAgentPage but:
//   - Uses `useWidgetLayout('fx', ...)` so the layout is persisted
//     independently from Rates and Monitor.
//   - Default layout is FX-shallow for V1: spot snapshot + scanner +
//     carry monitor (the three pre-aggregated FX widgets shipped with
//     the FX agent's first three tools).
//   - Header kicker says "FX AGENT · SPOT + CARRY" and the headline
//     accent points at FX-domain depth.
//
// Sharing the same engine as Rates guarantees that Monitor → Rates →
// FX feel like the same tool with different defaults.  Future agent
// pages (Credit, MacroEquity, etc.) will adopt this same skeleton.
// ============================================================================

import { useCallback, useMemo, useState } from 'react';
import { useWidgetLayout } from '@/hooks/useWidgetLayout';
import { useFxDataContext } from '@/components/monitor/FXDataProvider';
import { MonitorHeader } from '@/components/monitor/MonitorHeader';
import { WidgetGrid } from '@/components/monitor/WidgetGrid';
import { WidgetCatalogModal } from '@/components/monitor/WidgetCatalogModal';
import { defaultFxAgentLayout } from '@/components/monitor/defaults';
import type { WidgetInstance } from '@/components/monitor/registry';

// FXDataProvider is mounted at the AppShell layout level (shared with
// the Sidebar's Today panel).  This page reads from context directly
// without wrapping its own provider.

export function FXAgentPage() {
  const defaultLayout = useMemo(() => defaultFxAgentLayout(), []);
  return <FXAgentInner defaultLayout={defaultLayout} />;
}

type InnerProps = {
  defaultLayout: ReturnType<typeof defaultFxAgentLayout>;
};

function FXAgentInner({ defaultLayout }: InnerProps) {
  const { layout, addWidget, removeWidget, updateWidget, resetLayout } =
    useWidgetLayout('fx', defaultLayout);

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

  const headline = useFxHeadline();

  return (
    // `h-full overflow-y-auto` — see MonitorPage for the same fix
    // explanation (AppShell's routed wrapper is not a flex container,
    // so flex-1 here would not constrain the height).
    <div className="h-full overflow-y-auto">
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

function useFxHeadline() {
  const { data } = useFxDataContext();
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

  const flagged = data?.scanner.rows.length ?? null;
  const subline = data
    ? `Spot · Forwards · ${data.scanner.rows.length} pairs scanned · ${data.carry.rows.length} carry rows · TimescaleDB`
    : 'Loading FX data…';

  return {
    kicker: `${dayKicker} · FX AGENT · SPOT + CARRY`,
    headlinePrefix: 'The',
    headlineAccent: 'carry',
    headlineSuffix:
      flagged === null
        ? '.'
        : flagged === 0
          ? ' is quiet.'
          : `, with ${flagged} ${flagged === 1 ? 'pair' : 'pairs'} ranked.`,
    subline,
  };
}
