// ============================================================================
// shared/build/index.ts — Barrel export for the dual-view Build shells.
// ----------------------------------------------------------------------------
// Per-tool wrappers (modules/primitives/<tool>/surfaces/BuildExtended.tsx +
// BuildCompact.tsx) import from this single barrel.  Adding a new shared
// shell component = export it here; no per-tool import edits needed.
// ============================================================================

// Shells (orchestrators)
export { BuildCompactShell } from './compact/BuildCompactShell';
export { BuildExtendedShell } from './extended/BuildExtendedShell';

// Compact sub-pieces
export { MiniChart } from './compact/MiniChart';

// Extended sub-pieces
export { ControlsStrip } from './extended/ControlsStrip';
export { KPIStrip } from './extended/KPIStrip';
export { LineageFooter } from './extended/LineageFooter';
export { MainChart } from './extended/MainChart';
export { MethodologyCard } from './extended/MethodologyCard';
export { StretchContextCard } from './extended/StretchContextCard';

// Elements
export { CountryCaveatBadge } from './elements/CountryCaveatBadge';
export { FreshnessPill } from './elements/FreshnessPill';
export { InfoTooltip } from './elements/InfoTooltip';
export { ZScoreRegimeSlider } from './elements/ZScoreRegimeSlider';

// Lib (helpers + types)
export { asOfDateControl } from './lib/asOfDateControl';

export {
  countryCaveatFor,
  registeredCurveFamilies,
} from './lib/countryCaveats';
export type { CountryCaveatEntry } from './lib/countryCaveats';

export {
  bpsAsPercentSubtext,
  longDate,
  observationCount,
  percentileLabel,
  signedFixed,
  signedFixedWithUnit,
  unsignedFixed,
} from './lib/format';

export {
  Z_ELEVATED_THRESHOLD,
  Z_EXTREME_THRESHOLD,
  bucketForPercentile,
  referenceBandStrokeClass,
  regimeForZScore,
  toneForChange,
  toneForPercentile,
  toneForZScore,
  toneTextClass,
} from './lib/tone';

export type {
  BuildCompactComponent,
  BuildCompactProps,
  BuildCompactShellProps,
  BuildExtendedComponent,
  BuildExtendedProps,
  BuildExtendedShellProps,
  CategoryDescriptor,
  ChartPoint,
  ControlDescriptor,
  IdentityDescriptor,
  KPIDescriptor,
  LineageDescriptor,
  MethodologyRow,
  ReferenceBand,
  ReferenceChip,
  SparkPoint,
  StretchContext,
  TopRightCard,
  ValueTone,
} from './lib/types';
