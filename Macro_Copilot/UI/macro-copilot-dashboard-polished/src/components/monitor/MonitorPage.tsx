// ============================================================================
// MonitorPage — the new Home / Daily Monitor surface
// ----------------------------------------------------------------------------
// Replaces the legacy mock-data Dashboard.  Two-column layout (sidebar
// + main, no chat right-rail) handled by AppShell; this component
// renders only the main column.
//
// Composition:
//   - <RatesDataProvider>         page-level fetch shared by all
//                                 pre-aggregated rate widgets
//   - <MonitorHeader>             kicker + factual headline + Customize
//   - <WidgetGrid>                bento grid of widgets
//   - <WidgetCatalogModal>        catalog gallery + config form
//
// V1 default layout: yield snapshot (wide), scanner (medium), cross-
// market (medium), curve shapes (medium), regime monitor (medium),
// UST 10Y level (small).  Layout is persisted per-user in localStorage
// and survives across sessions.
// ============================================================================

import { useCallback, useMemo, useState } from 'react';
import { useWidgetLayout } from '@/hooks/useWidgetLayout';
import { useRatesDataContext } from './RatesDataProvider';
import { MonitorHeader } from './MonitorHeader';
import { WidgetGrid } from './WidgetGrid';
import { WidgetCatalogModal } from './WidgetCatalogModal';
import { defaultMonitorLayout } from './defaults';
import type { WidgetInstance } from './registry';

// RatesDataProvider is now mounted at the AppShell layout level so
// the Sidebar's Today panel can share the same fetch.  This page
// just reads from the context without wrapping its own provider.

export function MonitorPage() {
  // The default layout is built once at first render — its widgets get
  // freshly minted ids each time this function runs, but we only call
  // it on first paint via the lazy initializer in useState below.
  const defaultLayout = useMemo(() => defaultMonitorLayout(), []);
  return <MonitorPageInner defaultLayout={defaultLayout} />;
}

type InnerProps = {
  defaultLayout: ReturnType<typeof defaultMonitorLayout>;
};

function MonitorPageInner({ defaultLayout }: InnerProps) {
  const { layout, addWidget, removeWidget, updateWidget, resetLayout } =
    useWidgetLayout('monitor', defaultLayout);

  // Editing + modal state.  Modal can be in two modes: adding a new
  // widget (no instance), or configuring an existing one.
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

  const handleHeadline = useMonitorHeadline();

  return (
    // `h-full overflow-y-auto` rather than `flex-1` — AppShell's
    // routed wrapper is not a flex container, so flex-1 here would
    // not constrain the height and the page would expand past the
    // viewport with the outer overflow-hidden clipping the bottom.
    // h-full takes 100% of the parent (which IS a definite-height
    // flex-1 child of `main`), giving overflow-y-auto something to
    // scroll inside of.
    <div className="h-full overflow-y-auto">
      <div className="mx-auto w-full max-w-[1680px] px-6 py-7 lg:px-8 lg:py-8 3xl:max-w-[1880px] 3xl:px-10">
        <MonitorHeader
          kicker={handleHeadline.kicker}
          headlinePrefix={handleHeadline.headlinePrefix}
          headlineAccent={handleHeadline.headlineAccent}
          headlineSuffix={handleHeadline.headlineSuffix}
          subline={handleHeadline.subline}
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
// Headline derivation — pulls live rates data from context (provider
// is mounted in MonitorPage above) so the headline reflects today's
// market state instead of static placeholder text.
//
// IMPORTANT — threshold honesty:
// `data.scanner.results` comes from a request with hardcoded params
// `top_n=8` + `min_abs_z_score=1.5` (see useRatesData.ts).  These are
// OUR display defaults, not user preferences — the user has no way to
// configure them in V1.  An earlier version of this headline read
// "above your threshold" which is misleading.  The headline here
// names the actual numeric threshold (1.5σ) and the cap (top 8) so
// the user can read the truth without inspecting code.
//
// V2 will let users override `min_abs_z_score` + `top_n` via the
// Scanner widget's config form; at that point the headline can read
// the active threshold from the user's Scanner instance instead of
// the constants below.

const SCANNER_THRESHOLD_SIGMA = 1.5;
const SCANNER_TOP_N = 8;

function useMonitorHeadline(): {
  kicker: string;
  headlinePrefix: string;
  headlineAccent: string;
  headlineSuffix: string;
  subline?: string;
} {
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
  // The scanner endpoint returns up to `SCANNER_TOP_N` rows; if it
  // returned exactly that many, the true count above the threshold is
  // ≥ that — so we hedge the wording rather than overstating
  // precision.  Resolved properly in V2 when the backend reports both
  // filtered + total counts.
  const isAtCap = flagged !== null && flagged >= SCANNER_TOP_N;

  if (flagged === null) {
    return {
      kicker: `${dayKicker} · DAILY MONITOR`,
      headlinePrefix: 'Today\'s',
      headlineAccent: 'snapshot',
      headlineSuffix: '.',
      subline: 'Loading market data…',
    };
  }
  if (flagged === 0) {
    return {
      kicker: `${dayKicker} · DAILY MONITOR`,
      headlinePrefix: 'Markets are',
      headlineAccent: 'quiet',
      headlineSuffix: ' today.',
      subline: `No instruments above the ${SCANNER_THRESHOLD_SIGMA}σ z-score threshold.`,
    };
  }
  // Keep the serif-italic editorial accent on a real word
  // ("threshold").  Numbers go in the plain prefix — putting "1.5σ"
  // in the serif accent would render numerals in italic display
  // serif, which reads strangely.
  return {
    kicker: `${dayKicker} · DAILY MONITOR`,
    headlinePrefix: `${isAtCap ? 'At least ' : ''}${flagged} ${flagged === 1 ? 'instrument' : 'instruments'} above the ${SCANNER_THRESHOLD_SIGMA}σ`,
    headlineAccent: 'threshold',
    headlineSuffix: ' today.',
    subline: `Scanner threshold ≥ ${SCANNER_THRESHOLD_SIGMA}σ · top ${SCANNER_TOP_N} returned · widget data computed from TimescaleDB.`,
  };
}
