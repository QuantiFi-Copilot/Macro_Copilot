// ============================================================================
// shared/build/lib/types.ts — Typed props for the dual-view Build shells.
// ----------------------------------------------------------------------------
// Per docs_revamped/03_standards/rendering_density.md §5.1 every per-tool
// wrapper composes the shared shell components by passing typed descriptor
// arrays — KPIDescriptor[] for the strip, ChartPoint[] for the chart,
// MethodologyRow[] for the methodology card, etc.  The shell components
// are FINANCE-BLIND: they render whatever descriptors are handed to them
// without knowing what "real yield" or "curve spread" means.
//
// Closed-family discipline applies (per closed_family_discipline.md):
// the TYPES enumerated here are the closed family; the per-tool
// INSTANCES (how many KPIs, which labels, what data) are an open
// catalogue.  Adding a new descriptor TYPE requires updating this file
// + every shell consumer + an ADR.  Adding new descriptor instances
// per tool is unconstrained.
// ============================================================================

import type { ComponentType, ReactNode } from 'react';
import type { DecodedPrimitive } from '@/components/build/primitive/contextDecoder';

// ---------------------------------------------------------------------------
// Surface-prop interfaces (mirror docs_revamped/03_standards/rendering_density.md §5.1).
// ---------------------------------------------------------------------------

/** Extended Build view props.  Full canvas; mounted for single-tool
 *  queries.  Per-tool wrappers fetch their own data via the typed-
 *  detail endpoint and pass descriptor arrays to the shell. */
export interface BuildExtendedProps {
  toolName: string;
  params: Record<string, string>;
  decoded: DecodedPrimitive;
  askHandoff?: boolean;
  /** Stage D — control-edit sink.  When provided (by the multi-tool
   *  DAG's expand-to-modal infrastructure, ``openExtendedView``), the
   *  extended view's controls call THIS with the next params instead of
   *  navigating the global URL — so editing inside the modal stays local
   *  and does NOT blow away the multi-tool DAG context behind it.  When
   *  ABSENT (single-tool full-page mount via ``VirtualPrimitiveCanvas``),
   *  the extended view navigates ``/workspace?context=`` as before.
   *  Every per-tool ``buildExtended`` honours this the same way — generic
   *  across all tools, no per-tool special-casing. */
  onParamsChange?: (next: Record<string, string>) => void;
}

/** Compact Build view props.  Grid card; mounted as a node body
 *  inside multi-tool query DAG visualizations.  ``size`` is
 *  negotiated by the DAG renderer; ``onExpand`` is the shared-
 *  infrastructure callback that opens the extended view in a
 *  modal/drawer with a breadcrumb back to the DAG.  ``callMeta``
 *  surfaces the per-call ordinal when ≥2 cards of the same
 *  (toolName, params) appear in one DAG. */
export interface BuildCompactProps {
  toolName: string;
  params: Record<string, string>;
  size?: 'small' | 'medium';
  /** Invoked when the user clicks the compact card's expand
   *  affordance.  Shared infrastructure handles modal mounting +
   *  breadcrumb; the per-tool component just renders the trigger
   *  and calls this callback.  Optional in dev/test contexts
   *  (storybook, standalone preview routes); REQUIRED in production
   *  multi-tool DAG renders. */
  onExpand?: () => void;
  callMeta?: { n: number; m: number };
}

// ---------------------------------------------------------------------------
// Descriptor types — what the per-tool wrappers pass to the shells.
// ---------------------------------------------------------------------------

/** Tone — the closed family of visual treatments for numeric values.
 *  Carries semantic meaning, not literal color (the theme owns the
 *  hex).  Use the ``toneForChange`` / ``toneForZScore`` helpers in
 *  ./tone.ts to map raw numbers to tones. */
export type ValueTone =
  | 'neutral'        // no semantic emphasis
  | 'positive'       // typically green/mint (e.g. yield down = easing)
  | 'negative'       // typically red/coral (e.g. yield up = tightening)
  | 'elevated'       // amber band (|z| 1.5 – 2.0)
  | 'extreme-up'     // coral (positive z ≥ 2.0)
  | 'extreme-down';  // mint (negative z ≤ -2.0)

/** One headline-metric cell in a KPI strip.  Used in both compact
 *  (3 cells) and extended (N cells, possibly with sparklines)
 *  contexts. */
export interface KPIDescriptor {
  /** Short uppercase label (e.g. "REAL YIELD", "1D CHANGE"). */
  label: string;
  /** The value as a formatted display string (let the per-tool
   *  wrapper apply formatting — sign, decimals, etc.). */
  value: string;
  /** Optional unit suffix (e.g. "%", "bp", "th" for percentile). */
  unit?: string;
  /** Optional tone for the value's color treatment. */
  tone?: ValueTone;
  /** Optional secondary line below the value (e.g. "(+0.065%)" for
   *  a bps change with the percent equivalent). */
  subtext?: string;
  /** Optional emphasis level — used to make ONE cell larger than
   *  its siblings (e.g. the "REAL YIELD" cell in the compact view
   *  is the canonical headline). */
  emphasis?: 'primary' | 'secondary';
  /** Optional descriptor for a small sparkline UNDER the value
   *  (extended view only). */
  sparkline?: SparkPoint[];
  /** Optional one-line caveat / status word under the value
   *  (e.g. "Elevated" under a 1.84 z-score). */
  caption?: string;
}

/** One point on a chart or sparkline. */
export interface ChartPoint {
  date: string;
  value: number;
}

/** Reduced-shape sparkline point — value only (date inferred). */
export type SparkPoint = number;

/** Horizontal reference band on a chart — typically the z-score
 *  envelope (mean ± Nσ) translated into the chart's y-units. */
export interface ReferenceBand {
  value: number;
  label?: string;
  tone: 'neutral' | 'positive' | 'negative' | 'elevated' | 'extreme';
  style?: 'dashed' | 'solid';
}

/** One row in the methodology card — a label + value pair. */
export interface MethodologyRow {
  label: string;
  value: ReactNode;
}

/** One reference citation chip in the methodology card. */
export interface ReferenceChip {
  label: string;
  /** Optional URL — when provided the chip renders as a link. */
  href?: string;
}

/** Stretch-context content for the extended view's side panel.  Per
 *  rendering_density.md §2.1 this is a per-tool optional slot; tools
 *  that don't have a "stretch" concept omit it. */
export interface StretchContext {
  percentile?: { value: number; bucket: 'Low' | 'Normal' | 'High' };
  zScoreRegime?: {
    value: number;
    regime: 'Normal' | 'Elevated' | 'Extreme';
    bands?: { amber: number; coral: number };  // |z| thresholds
  };
  interpretation?: string;
}

/** One control on the controls strip.  The wrapper declares which
 *  Pydantic Input fields are exposed and how.  Per rendering_density.md
 *  §2.1 the controls strip only appears in the extended view. */
export interface ControlDescriptor {
  /** Pydantic field name (e.g. "curve_family"). */
  name: string;
  /** Human-facing label (e.g. "Curve Family"). */
  label: string;
  /** Control kind — drives the JSX shape.  ``date`` renders a native
   *  date picker plus a "Live" clear affordance (the as-of / replay
   *  control); an empty ``value`` means latest live data. */
  kind: 'enum' | 'text' | 'number' | 'date';
  /** Current value (string-form to mirror URL state). */
  value: string;
  /** Option list when ``kind === 'enum'``. */
  options?: ReadonlyArray<{ value: string; label: string }>;
  /** Numeric bounds when ``kind === 'number'``. */
  min?: number;
  max?: number;
  /** Whether this control lives in the "Advanced" expandable
   *  (collapsed by default).  Per rendering_density.md §2.1 the
   *  controls strip is structured so primary instrument-selection
   *  controls are always visible; methodology-override controls
   *  (Phase-1 exposed conventions) are advanced-tier. */
  advanced?: boolean;
}

/** One card on the top-right of the extended view (z-score, percentile,
 *  country caveat, etc.).  Up to ~3 cards per the mockup convention. */
export interface TopRightCard {
  /** React node — typically one of the small element components
   *  from ../elements/ (CountryCaveatBadge, etc.) or a per-tool
   *  custom card. */
  node: ReactNode;
  /** Stable key for React reconciliation. */
  key: string;
}

/** Identity-row content — typically curve_family + tenor + flag + subtitle. */
export interface IdentityDescriptor {
  /** Primary instrument identifier (e.g. "USD_TIPS"). */
  primary: string;
  /** Secondary identifier (e.g. "10Y"). */
  secondary?: string;
  /** Optional country / instrument flag emoji or icon. */
  flag?: ReactNode;
  /** One- to two-line subtitle (e.g. "US Treasury Inflation-Protected
   *  Securities / Generic benchmark real yield"). */
  subtitle?: ReactNode;
  /** As-of date string (YYYY-MM-DD). */
  asOfDate?: string;
  /** Compact metadata line (e.g. "365d window · YLD_YTM_MID"). */
  meta?: string;
}

/** Category strip content — the kicker row at the top of the extended
 *  view (e.g. "REAL YIELD LEVEL • SNAPSHOT • DETERMINISTIC"). */
export interface CategoryDescriptor {
  /** Tool display name in upper-case (e.g. "REAL YIELD LEVEL"). */
  name: string;
  /** Tags — short descriptors like "SNAPSHOT", "DETERMINISTIC",
   *  "BUCKET 1A". */
  tags?: ReadonlyArray<string>;
}

/** Lineage footer descriptor — bottom row of the extended view. */
export interface LineageDescriptor {
  /** Short lineage hash (8 chars). */
  hash?: string;
  /** Tool name (full canonical form). */
  toolName: string;
  /** Tool version. */
  version?: string;
  /** Free-form description of what kind of computation this is
   *  (e.g. "Deterministic snapshot"). */
  kind?: string;
  /** Provider chain — backing infrastructure (e.g. ["TimescaleDB",
   *  "macro_data.v_market_data_daily_enriched"]). */
  providers?: ReadonlyArray<string>;
  /** As-of timestamp (full ISO or "YYYY-MM-DD HH:MM UTC"). */
  asOf?: string;
  /** Freshness indicator. */
  freshness?: 'fresh' | 'stale' | 'unknown';
}

// ---------------------------------------------------------------------------
// Slot props — the Build shells accept these as slot fillers.
// ---------------------------------------------------------------------------

export interface BuildExtendedShellProps {
  category: CategoryDescriptor;
  identity: IdentityDescriptor;
  topRightCards?: ReadonlyArray<TopRightCard>;
  controls: ReadonlyArray<ControlDescriptor>;
  onControlChange: (name: string, value: string) => void;
  onResetControls?: () => void;
  kpis: ReadonlyArray<KPIDescriptor>;
  chartPoints: ReadonlyArray<ChartPoint>;
  chartUnit: string;
  chartValueDecimals?: number;
  referenceBands?: ReadonlyArray<ReferenceBand>;
  stretchContext?: StretchContext;
  methodology: ReadonlyArray<MethodologyRow>;
  methodologyReferences?: ReadonlyArray<ReferenceChip>;
  lineage: LineageDescriptor;
  /** Loading state — when true the chart + KPIs render skeleton
   *  shimmer instead of values. */
  isLoading?: boolean;
  /** Fetch error message (if any) — rendered in place of the chart
   *  body when present. */
  errorMessage?: string;
}

export interface BuildCompactShellProps {
  /** Tool display name + status pill (e.g. "Real Yield Level · SNAPSHOT"). */
  toolDisplayName: string;
  statusPill?: string;
  /** Optional small icon in the header left (component or emoji). */
  headerIcon?: ReactNode;
  identity: IdentityDescriptor;
  /** Three headline KPIs — the compact view enforces exactly 3 cells
   *  per the rendering-density spec §2.2.  Extra entries are
   *  truncated to the first 3 with a console warning in dev. */
  kpis: ReadonlyArray<KPIDescriptor>;
  /** Sparkline series.  Semantics: OMIT (undefined) when the tool's
   *  wire carries NO series at all — pure-snapshot / categorical
   *  shapes render a chartless KPI card (rendering_density.md §2.2
   *  semantic contract: never fabricate a tape).  Pass an EMPTY array
   *  only when the tool IS series-shaped but the window came back
   *  empty — that renders the honest "No data in window" state. */
  chartPoints?: ReadonlyArray<ChartPoint>;
  chartUnit?: string;
  referenceBands?: ReadonlyArray<ReferenceBand>;
  /** Compact methodology — one-line caveat that renders inline in the
   *  footer (or via an info-tooltip).  Per rendering_density.md §2.2
   *  methodology MUST be reachable in the compact view (no hiding). */
  caveatText: string;
  /** Footer right-side meta. */
  asOf?: string;
  freshness?: 'fresh' | 'stale' | 'unknown';
  /** Click-to-expand callback — opens the extended view in a modal/
   *  drawer.  Optional in standalone-preview contexts; the trigger
   *  is hidden when undefined. */
  onExpand?: () => void;
  size?: 'small' | 'medium';
  isLoading?: boolean;
  errorMessage?: string;
  callMeta?: { n: number; m: number };
}

// ---------------------------------------------------------------------------
// Re-export utility — modules can import the surface-prop interfaces
// from this file directly (mirror of @/modules/types contract).
// ---------------------------------------------------------------------------

export type BuildExtendedComponent = ComponentType<BuildExtendedProps>;
export type BuildCompactComponent = ComponentType<BuildCompactProps>;
