// ============================================================================
// src/components/shared/build/model/ModelResultLayout.tsx — the model
// results scaffold.
// ----------------------------------------------------------------------------
// Consolidation target #4: the rich-model analog of
// ``PrimitiveCanvasShell`` — ONE section order for every model's
// results body so PCA reads as consistently as curve_spread:
//
//   1. hero        — ModelKpiStrip (the 3-5 numbers a PM reads first)
//   2. primary     — the model's main visual (MatrixTable /
//                    DecompositionBars / ModelSeriesPanel)
//   3. secondary   — supporting visuals, in order
//   4. diagnostics — fit-health reads (R², condition flags, CIs) —
//                    rendered LAST, always present when supplied (P5)
//   5. methodology — the SINGLE methodology home for the model result
//                    (threaded from the backend response, never a TSX
//                    literal — P5)
//
// The scaffold enforces ORDER, not content: a model that lacks a zone
// omits the prop and the rhythm stays consistent.  Finance-blind
// (FP13); modules compose it from their own surfaces folder.
// ============================================================================

export interface ModelResultLayoutProps {
  hero: React.ReactNode;
  primary: React.ReactNode;
  secondary?: React.ReactNode[];
  diagnostics?: React.ReactNode;
  methodology?: React.ReactNode;
}

export function ModelResultLayout({
  hero,
  primary,
  secondary,
  diagnostics,
  methodology,
}: ModelResultLayoutProps) {
  return (
    <div className="space-y-5">
      {hero}
      {primary}
      {secondary?.map((node, i) => (
        // Section order is the contract; index keys are stable because
        // the zone list is a fixed composition, not reorderable data.
        // eslint-disable-next-line react/no-array-index-key
        <div key={i}>{node}</div>
      ))}
      {diagnostics}
      {methodology}
    </div>
  );
}
