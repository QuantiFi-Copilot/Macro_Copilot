// ============================================================================
// WidgetRenderer
// ----------------------------------------------------------------------------
// Single dispatcher: given a WidgetInstance from the layout, look up the
// type's renderer and render it inside the shared WidgetCard chrome.
//
// Adding a new widget type:
//   1. Register its metadata in `registry.ts` (WIDGET_TYPES + CATALOG_ORDER)
//   2. Build its renderer in `widgets/<Name>Widget.tsx`
//   3. Add the renderer to RENDERERS below
// ============================================================================

import { WidgetCard } from './WidgetCard';
import { WidgetError } from './widgets/shared';
import { widgetMeta, type WidgetInstance } from './registry';

// Widget renderers
import { YieldSnapshotWidget } from './widgets/YieldSnapshotWidget';
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
    default:
      return (
        <WidgetError
          message={`No renderer registered for "${instance.type}".`}
        />
      );
  }
}
