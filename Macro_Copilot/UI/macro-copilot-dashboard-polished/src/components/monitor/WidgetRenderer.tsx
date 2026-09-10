// ============================================================================
// WidgetRenderer — Stage 4d module-driven dispatch.
// ----------------------------------------------------------------------------
// Single dispatcher: given a WidgetInstance from the layout, look up
// the type's renderer and render it inside the shared WidgetCard
// chrome.
//
// Stage 4d
// --------
// Per-primitive widget components no longer live under
// ``components/monitor/widgets/`` — they live in their owning module's
// ``surfaces/monitor/`` folder (FP12 compliant; modules consume page-
// shell infrastructure, page shells consume module specs via the
// loader).  This file walks ``ALL_PRIMITIVE_MODULES`` once at module
// load and builds an ``id → component`` map keyed by the per-module
// ``monitorWidgets[i].component`` reference.
//
// Hand-authored widgets (today: only the pre-aggregated
// ``yield_snapshot``) keep their direct import.
//
// Adding a new widget renderer:
//   * Per-primitive: add the component to the owning module's
//     ``monitorWidgets[i].component`` field; this dispatcher picks it
//     up automatically.
//   * Hand-authored: import the component below and add it to
//     ``HAND_AUTHORED_COMPONENTS``.
// ============================================================================

import { type ComponentType } from 'react';
import { WidgetCard } from './WidgetCard';
import { WidgetError } from './widgets/shared';
import { widgetMeta, type WidgetInstance } from './registry';
import { ALL_PRIMITIVE_MODULES } from '@/modules';

// Hand-authored renderers (kept here; not module-derived).
import { YieldSnapshotWidget } from './widgets/YieldSnapshotWidget';
<<<<<<< HEAD

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type RendererComponent = ComponentType<any>;

const HAND_AUTHORED_COMPONENTS: Record<string, RendererComponent> = {
  yield_snapshot: YieldSnapshotWidget,
};

// Build the module-derived component map once at module load.  The
// registry walker in ``registry.ts`` does the metadata side; we
// mirror its walk here for the renderers to keep both sides in
// lock-step from a single source (each module's ``monitorWidgets``
// array).
const MODULE_DERIVED_COMPONENTS: Record<string, RendererComponent> = (() => {
  const out: Record<string, RendererComponent> = {};
  for (const m of ALL_PRIMITIVE_MODULES) {
    for (const w of m.monitorWidgets ?? []) {
      out[w.id] = w.component;
    }
  }
  return out;
})();

const COMPONENTS: Record<string, RendererComponent> = {
  ...HAND_AUTHORED_COMPONENTS,
  ...MODULE_DERIVED_COMPONENTS,
};
=======
import { ScannerWidget } from './widgets/ScannerWidget';
import { CrossMarketSpreadsWidget } from './widgets/CrossMarketSpreadsWidget';
import { CurveSpreadsWidget } from './widgets/CurveSpreadsWidget';
import { CurveClassifierWidget } from './widgets/CurveClassifierWidget';
import { YieldLevelWidget } from './widgets/YieldLevelWidget';
import { SpreadChartWidget } from './widgets/SpreadChartWidget';
import { CrossMarketSpreadWidget } from './widgets/CrossMarketSpreadWidget';
import { FXSpotSnapshotWidget } from './widgets/FXSpotSnapshotWidget';
import { FXScannerWidget } from './widgets/FXScannerWidget';
import { FXCarryWidget } from './widgets/FXCarryWidget';
import { FXForwardCurveWidget } from './widgets/FXForwardCurveWidget';
import { FXCarryBasketWidget } from './widgets/FXCarryBasketWidget';
import { FXVolSmileWidget } from './widgets/FXVolSmileWidget';
import { FXCrossCurrencyBasisWidget } from './widgets/FXCrossCurrencyBasisWidget';
>>>>>>> origin/codex/fx-ui-wave2-widgets

type Props = {
  instance: WidgetInstance;
  isEditing: boolean;
  onRemove?: (instanceId: string) => void;
  onConfigure?: (instanceId: string) => void;
};

export function WidgetRenderer({
  instance,
  isEditing,
  onRemove,
  onConfigure,
}: Props) {
  const meta = widgetMeta(instance.type);

  if (!meta) {
    return (
      <WidgetCard
        category="data"
        size={instance.size}
        isEditing={isEditing}
        onRemove={onRemove ? () => onRemove(instance.id) : undefined}
      >
        <WidgetError
          message={`Unknown widget type "${instance.type}". This usually means the catalog removed it.`}
        />
      </WidgetCard>
    );
  }

  return (
    <WidgetCard
      category={meta.category}
      size={instance.size}
      isEditing={isEditing}
      isConfigurable={meta.parameterized}
      onRemove={onRemove ? () => onRemove(instance.id) : undefined}
      onConfigure={
        onConfigure && meta.parameterized
          ? () => onConfigure(instance.id)
          : undefined
      }
    >
      <Body instance={instance} />
    </WidgetCard>
  );
}

function Body({ instance }: { instance: WidgetInstance }) {
<<<<<<< HEAD
  const Component = COMPONENTS[instance.type];
  if (!Component) {
    return (
      <WidgetError
        message={`No renderer registered for "${instance.type}".`}
      />
    );
=======
  switch (instance.type) {
    case 'yield_snapshot':
      return <YieldSnapshotWidget />;
    case 'scanner':
      return <ScannerWidget />;
    case 'cross_market_spreads':
      return <CrossMarketSpreadsWidget />;
    case 'curve_spreads':
      return <CurveSpreadsWidget />;
    case 'curve_classifier':
      return <CurveClassifierWidget />;
    case 'yield_level':
      return <YieldLevelWidget params={instance.params} />;
    case 'spread_chart':
      return <SpreadChartWidget params={instance.params} />;
    case 'cross_market_spread':
      return <CrossMarketSpreadWidget params={instance.params} />;
    case 'fx_spot_snapshot':
      return <FXSpotSnapshotWidget />;
    case 'fx_scanner':
      return <FXScannerWidget />;
    case 'fx_carry':
      return <FXCarryWidget params={instance.params} />;
    case 'fx_forward_curve':
      return <FXForwardCurveWidget params={instance.params} />;
    case 'fx_carry_basket':
      return <FXCarryBasketWidget params={instance.params} />;
    case 'fx_vol_smile':
      return <FXVolSmileWidget params={instance.params} />;
    case 'fx_cross_currency_basis':
      return <FXCrossCurrencyBasisWidget params={instance.params} />;
    default:
      return (
        <WidgetError
          message={`No renderer registered for "${instance.type}".`}
        />
      );
>>>>>>> origin/codex/fx-ui-wave2-widgets
  }
  // Pre-aggregated widgets ignore the ``params`` prop; parameterised
  // widgets read from it.  Passing it unconditionally is harmless —
  // pre-aggregated components destructure no props.
  return <Component params={instance.params} />;
}
