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
import { CrossMarketWidget } from './widgets/CrossMarketWidget';
import { CurveShapesWidget } from './widgets/CurveShapesWidget';
import { RegimeMonitorWidget } from './widgets/RegimeMonitorWidget';
import { YieldLevelWidget } from './widgets/YieldLevelWidget';
import { SpreadChartWidget } from './widgets/SpreadChartWidget';
import { CrossMarketSpreadWidget } from './widgets/CrossMarketSpreadWidget';

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
    case 'cross_market':
      return <CrossMarketWidget />;
    case 'curve_shapes':
      return <CurveShapesWidget />;
    case 'regime_monitor':
      return <RegimeMonitorWidget />;
    case 'yield_level':
      return <YieldLevelWidget params={instance.params} />;
    case 'spread_chart':
      return <SpreadChartWidget params={instance.params} />;
    case 'cross_market_spread':
      return <CrossMarketSpreadWidget params={instance.params} />;
    default:
      return (
        <WidgetError
          message={`No renderer registered for "${instance.type}".`}
        />
      );
  }
}
