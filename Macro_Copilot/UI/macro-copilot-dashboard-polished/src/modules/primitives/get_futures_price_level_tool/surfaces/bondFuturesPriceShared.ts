// ============================================================================
// bondFuturesPriceShared.ts — Per-tool helpers shared between
// BuildExtended.tsx, BuildCompact.tsx, and the Monitor widget for
// the bond_futures variant of ``get_futures_price_level_tool``.
// ----------------------------------------------------------------------------
// FINANCE-AWARE per-tool layer.  This module knows that the bond_futures
// rolling-generic price tape is in PRICE SPACE (not implied-rate space —
// that's the policy_futures cousin's domain), that per-contract
// ``quote_units`` vary ('points' / '% of par value' / '100 - yield' /
// 'GBP'), and that the 252d z-score lives on the PRICE level.  The shared
// shells do not.  Both BuildExtended.tsx and BuildCompact.tsx fetch the
// SAME data per rendering_density.md §1.1; KPI builders + formatting +
// tone live here in ONE place.
//
// Naming-divergence note (catalog-23): the bond_futures Pydantic schema
// uses the bare ``FuturesPriceLevelOutput`` symbol; on the frontend side
// the imported type is namespaced ``BondFutures…`` to avoid a clash with
// the policy_futures cousin's same-named-but-different shape.
// ============================================================================

import { useEffect, useState } from 'react';
import {
  fetchDetailBondFuturesPrice,
  type BondFuturesPriceDetailParams,
} from '@/services/ratesApi';
import type { BondFuturesPriceLevelOutput } from '@/types/rates';
import {
  bucketForPercentile,
  regimeForZScore,
  signedFixed,
  toneForChange,
  toneForZScore,
  type KPIDescriptor,
  type MethodologyRow,
  type ReferenceBand,
  type ReferenceChip,
  type StretchContext,
} from '@/components/shared/build';

// ---------------------------------------------------------------------------
// Per-contract metadata registry.  Bond-futures rolling-generics quote in
// HETEROGENEOUS price spaces — UST in 'points' (32nds notation by desk
// convention), DE/IT/ES/FR/JP/JB/CA in '% of par value' or 'points', UK
// in 'GBP', AU in '100 - yield'.  This registry is the SINGLE source of
// truth for display flag / long label / quote-units suffix / decimals
// per (curve_family, contract_code) pair.
// ---------------------------------------------------------------------------

export type BondFuturesQuoteUnits =
  | 'points'
  | '% of par value'
  | '100 - yield'
  | 'GBP';

/** UST family contracts use a desk-canonical 32nds notation
 *  (e.g. 129'08 == 129 + 8/32).  The compact / extended views render
 *  that notation in the primary cell with a decimal subtext so the
 *  trader sees both. */
export type PriceNotation = 'thirty_seconds' | 'decimal';

export interface BondFuturesContractMeta {
  curveFamily: string;
  contractCode: string;
  /** Country flag emoji rendered in the identity row. */
  flag: string;
  /** Tenor label for the rolling-generic (matches the backend wire field). */
  tenor: string;
  /** Short market label for the identity chip (e.g. 'UST', 'Bund'). */
  shortLabel: string;
  /** Long human-facing market name (e.g. 'CME 10-Year U.S. Treasury Note Futures'). */
  longLabel: string;
  /** Default quote units (the backend wire's ``quote_units`` is authoritative —
   *  this is a fallback when the wire field is missing). */
  quoteUnits: BondFuturesQuoteUnits;
  /** Notation: '32nds' for UST family, 'decimal' for the rest. */
  notation: PriceNotation;
  /** Decimals used by the decimal formatter (only consulted when
   *  notation === 'decimal'). */
  decimals: number;
}

const CONTRACT_REGISTRY: Record<string, BondFuturesContractMeta> = {
  // UST family — 'points' with 32nds notation per CBOT convention.
  TY1: {
    curveFamily: 'UST_FUT',
    contractCode: 'TY1',
    flag: '🇺🇸',
    tenor: '10Y',
    shortLabel: 'UST',
    longLabel: 'CME 10-Year U.S. Treasury Note Futures',
    quoteUnits: 'points',
    notation: 'thirty_seconds',
    decimals: 4,
  },
  UXY1: {
    curveFamily: 'UST_FUT',
    contractCode: 'UXY1',
    flag: '🇺🇸',
    tenor: '10Y',
    shortLabel: 'UST',
    longLabel: 'CME Ultra 10-Year U.S. Treasury Note Futures',
    quoteUnits: 'points',
    notation: 'thirty_seconds',
    decimals: 4,
  },
  US1: {
    curveFamily: 'UST_FUT',
    contractCode: 'US1',
    flag: '🇺🇸',
    tenor: '30Y',
    shortLabel: 'UST',
    longLabel: 'CME U.S. Treasury Bond Futures',
    quoteUnits: 'points',
    notation: 'thirty_seconds',
    decimals: 4,
  },
  WN1: {
    curveFamily: 'UST_FUT',
    contractCode: 'WN1',
    flag: '🇺🇸',
    tenor: '30Y',
    shortLabel: 'UST',
    longLabel: 'CME Ultra U.S. Treasury Bond Futures',
    quoteUnits: 'points',
    notation: 'thirty_seconds',
    decimals: 4,
  },
  TU1: {
    curveFamily: 'UST_FUT',
    contractCode: 'TU1',
    flag: '🇺🇸',
    tenor: '2Y',
    shortLabel: 'UST',
    longLabel: 'CME 2-Year U.S. Treasury Note Futures',
    quoteUnits: 'points',
    notation: 'thirty_seconds',
    decimals: 4,
  },
  FV1: {
    curveFamily: 'UST_FUT',
    contractCode: 'FV1',
    flag: '🇺🇸',
    tenor: '5Y',
    shortLabel: 'UST',
    longLabel: 'CME 5-Year U.S. Treasury Note Futures',
    quoteUnits: 'points',
    notation: 'thirty_seconds',
    decimals: 4,
  },
  // DE family — '% of par value', decimal notation (Eurex convention).
  RX1: {
    curveFamily: 'DE_FUT',
    contractCode: 'RX1',
    flag: '🇩🇪',
    tenor: '10Y',
    shortLabel: 'Bund',
    longLabel: 'Eurex Euro-Bund Futures',
    quoteUnits: '% of par value',
    notation: 'decimal',
    decimals: 2,
  },
  UB1: {
    curveFamily: 'DE_FUT',
    contractCode: 'UB1',
    flag: '🇩🇪',
    tenor: '30Y',
    shortLabel: 'Buxl',
    longLabel: 'Eurex Euro-Buxl Futures',
    quoteUnits: '% of par value',
    notation: 'decimal',
    decimals: 2,
  },
  DU1: {
    curveFamily: 'DE_FUT',
    contractCode: 'DU1',
    flag: '🇩🇪',
    tenor: '2Y',
    shortLabel: 'Schatz',
    longLabel: 'Eurex Euro-Schatz Futures',
    quoteUnits: '% of par value',
    notation: 'decimal',
    decimals: 3,
  },
  OE1: {
    curveFamily: 'DE_FUT',
    contractCode: 'OE1',
    flag: '🇩🇪',
    tenor: '5Y',
    shortLabel: 'Bobl',
    longLabel: 'Eurex Euro-Bobl Futures',
    quoteUnits: '% of par value',
    notation: 'decimal',
    decimals: 2,
  },
  // UK — 'GBP'.
  G1: {
    curveFamily: 'UK_FUT',
    contractCode: 'G1',
    flag: '🇬🇧',
    tenor: '10Y',
    shortLabel: 'Gilt',
    longLabel: 'ICE Long Gilt Futures',
    quoteUnits: 'GBP',
    notation: 'decimal',
    decimals: 2,
  },
  // JP — 'points'.
  JB1: {
    curveFamily: 'JP_FUT',
    contractCode: 'JB1',
    flag: '🇯🇵',
    tenor: '10Y',
    shortLabel: 'JGB',
    longLabel: 'OSE 10-Year JGB Futures',
    quoteUnits: 'points',
    notation: 'decimal',
    decimals: 2,
  },
  // FR — '% of par value'.
  OAT1: {
    curveFamily: 'FR_FUT',
    contractCode: 'OAT1',
    flag: '🇫🇷',
    tenor: '10Y',
    shortLabel: 'OAT',
    longLabel: 'Eurex Euro-OAT Futures',
    quoteUnits: '% of par value',
    notation: 'decimal',
    decimals: 2,
  },
  // IT — '% of par value'.
  IK1: {
    curveFamily: 'IT_FUT',
    contractCode: 'IK1',
    flag: '🇮🇹',
    tenor: '10Y',
    shortLabel: 'BTP',
    longLabel: 'Eurex Long-Term Euro-BTP Futures',
    quoteUnits: '% of par value',
    notation: 'decimal',
    decimals: 2,
  },
  BTS1: {
    curveFamily: 'IT_FUT',
    contractCode: 'BTS1',
    flag: '🇮🇹',
    tenor: '3Y',
    shortLabel: 'BTP-S',
    longLabel: 'Eurex Short-Term Euro-BTP Futures',
    quoteUnits: '% of par value',
    notation: 'decimal',
    decimals: 2,
  },
  // ES — '% of par value'.
  KOA1: {
    curveFamily: 'ES_FUT',
    contractCode: 'KOA1',
    flag: '🇪🇸',
    tenor: '10Y',
    shortLabel: 'Bono',
    longLabel: 'MEFF 10-Year Bono Futures',
    quoteUnits: '% of par value',
    notation: 'decimal',
    decimals: 2,
  },
  // CA — 'points'.
  CN1: {
    curveFamily: 'CA_FUT',
    contractCode: 'CN1',
    flag: '🇨🇦',
    tenor: '10Y',
    shortLabel: 'CGB',
    longLabel: 'MX 10-Year Government of Canada Bond Futures',
    quoteUnits: 'points',
    notation: 'decimal',
    decimals: 2,
  },
  // AU — '100 - yield' (price ≈ 100 − yield_pct on ASX 3Y / 10Y).
  YM1: {
    curveFamily: 'AU_FUT',
    contractCode: 'YM1',
    flag: '🇦🇺',
    tenor: '3Y',
    shortLabel: 'ASX 3Y',
    longLabel: 'ASX 3-Year Treasury Bond Futures',
    quoteUnits: '100 - yield',
    notation: 'decimal',
    decimals: 3,
  },
  XM1: {
    curveFamily: 'AU_FUT',
    contractCode: 'XM1',
    flag: '🇦🇺',
    tenor: '10Y',
    shortLabel: 'ASX 10Y',
    longLabel: 'ASX 10-Year Treasury Bond Futures',
    quoteUnits: '100 - yield',
    notation: 'decimal',
    decimals: 3,
  },
};

export function contractMetaFor(
  contractCode: string,
): BondFuturesContractMeta | null {
  return CONTRACT_REGISTRY[contractCode] ?? null;
}

/** Curve-family + contract-code options for the Monitor widget +
 *  extended view's controls strip.  Order matches the catalog
 *  enumeration (UST first, then DE, UK, JP, FR, IT, ES, CA, AU). */
export const BOND_FUTURES_CONTRACT_OPTIONS: ReadonlyArray<{
  value: string;
  label: string;
}> = Object.values(CONTRACT_REGISTRY).map((m) => ({
  value: m.contractCode,
  label: `${m.contractCode} · ${m.shortLabel} ${m.tenor} (${m.curveFamily})`,
}));

/** Curve-family options (collapsed from CONTRACT_REGISTRY) for the
 *  Monitor widget's first selector — picking a curve narrows the
 *  contract list. */
export const BOND_FUTURES_CURVE_OPTIONS: ReadonlyArray<{
  value: string;
  label: string;
}> = (() => {
  const seen = new Map<string, string>();
  for (const m of Object.values(CONTRACT_REGISTRY)) {
    if (!seen.has(m.curveFamily)) {
      seen.set(m.curveFamily, m.shortLabel);
    }
  }
  return Array.from(seen.entries()).map(([family, short]) => ({
    value: family,
    label: `${family} · ${short}`,
  }));
})();

/** Desk-canonical caveat string for the compact view footer.  Mirrors
 *  the per-contract V1-scope disclosure the methodology card surfaces
 *  in full. */
export const BOND_FUTURES_PRICE_COMPACT_CAVEAT =
  'V1 monitors-only; CTD analytics (basis, repo, DV01) not in V1.';

// ---------------------------------------------------------------------------
// Price formatting — 32nds notation for UST family + decimal for the rest.
// ---------------------------------------------------------------------------

/** Format a UST 'points' price into 32nds notation, e.g. 129.25 → "129'08"
 *  (8/32 = 0.25).  Apostrophe is the desk-standard separator. */
export function format32nds(price: number | null | undefined): string {
  if (price == null || !Number.isFinite(price)) return '—';
  const sign = price < 0 ? '-' : '';
  const abs = Math.abs(price);
  const whole = Math.floor(abs);
  const fraction32 = Math.round((abs - whole) * 32);
  // Carry: 32 → roll the whole part by 1.
  const rolledWhole = fraction32 === 32 ? whole + 1 : whole;
  const rolledFrac = fraction32 === 32 ? 0 : fraction32;
  return `${sign}${rolledWhole}'${rolledFrac.toString().padStart(2, '0')}`;
}

/** Subtext rendering of a UST 32nds price, e.g. 129.25 → "129 and 8/32nds". */
export function format32ndsSubtext(price: number | null | undefined): string {
  if (price == null || !Number.isFinite(price)) return '—';
  const abs = Math.abs(price);
  const whole = Math.floor(abs);
  const fraction32 = Math.round((abs - whole) * 32);
  if (fraction32 === 32) {
    return `${(price < 0 ? '-' : '') + (whole + 1)} and 0/32nds`;
  }
  return `${(price < 0 ? '-' : '') + whole} and ${fraction32}/32nds`;
}

/** Format a price using the per-contract notation + decimals.  Falls
 *  back to plain ``signedFixed(price, 2)`` when the contract is unknown
 *  (defensive — should never trigger if CONTRACT_REGISTRY is in sync
 *  with the backend universe). */
export function formatPrice(
  price: number | null | undefined,
  meta: BondFuturesContractMeta | null,
): string {
  if (price == null || !Number.isFinite(price)) return '—';
  if (meta?.notation === 'thirty_seconds') return format32nds(price);
  return price.toFixed(meta?.decimals ?? 2);
}

/** Format a price CHANGE.  UST 32nds changes are tick-style (e.g. +0'06)
 *  with sign; decimal contracts get signed decimal. */
export function formatPriceChange(
  delta: number | null | undefined,
  meta: BondFuturesContractMeta | null,
): string {
  if (delta == null || !Number.isFinite(delta)) return '—';
  if (meta?.notation === 'thirty_seconds') {
    const sign = delta >= 0 ? '+' : '-';
    const abs = Math.abs(delta);
    const whole = Math.floor(abs);
    const fraction32 = Math.round((abs - whole) * 32);
    const rolledWhole = fraction32 === 32 ? whole + 1 : whole;
    const rolledFrac = fraction32 === 32 ? 0 : fraction32;
    return `${sign}${rolledWhole}'${rolledFrac.toString().padStart(2, '0')}`;
  }
  return signedFixed(delta, meta?.decimals ?? 2);
}

/** Quote-units suffix used as a unit chip on KPI cells / chart axis.
 *  Renders the wire's authoritative ``quote_units`` if present;
 *  otherwise the per-contract default. */
export function quoteUnitsLabel(
  wireUnits: string | null | undefined,
  meta: BondFuturesContractMeta | null,
): string {
  const units = wireUnits ?? meta?.quoteUnits ?? 'points';
  return units;
}

/** Compute the percent-of-price subtext for a price change.  Used for the
 *  parallel "(+0.15%)" line under the 1D / 5D / 1M cells. */
export function priceChangePercentSubtext(
  delta: number | null | undefined,
  referencePrice: number | null | undefined,
  decimals: number = 2,
): string | undefined {
  if (
    delta == null ||
    referencePrice == null ||
    !Number.isFinite(delta) ||
    !Number.isFinite(referencePrice) ||
    referencePrice === 0
  ) {
    return undefined;
  }
  const pct = (delta / referencePrice) * 100;
  const sign = pct >= 0 ? '+' : '';
  return `(${sign}${pct.toFixed(decimals)}%)`;
}

// ---------------------------------------------------------------------------
// Data hook — single source consumed by BOTH Build views + the Monitor tile.
// ---------------------------------------------------------------------------

export interface UseBondFuturesPriceArgs {
  curveFamily: string;
  contractCode: string;
  lookbackDays?: number;
  fieldName?: string;
  /** As-of trade date (YYYY-MM-DD).  Undefined/empty → latest live data. */
  asOfDate?: string;
}

export interface UseBondFuturesPriceResult {
  data: BondFuturesPriceLevelOutput | null;
  isLoading: boolean;
  errorMessage: string | null;
}

export function useBondFuturesPrice(
  args: UseBondFuturesPriceArgs,
): UseBondFuturesPriceResult {
  const [data, setData] = useState<BondFuturesPriceLevelOutput | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const params: BondFuturesPriceDetailParams = {
    curve_family: args.curveFamily,
    contract_code: args.contractCode,
    lookback_days: args.lookbackDays,
    field_name: args.fieldName,
    as_of_date: args.asOfDate || undefined,
  };

  useEffect(() => {
    if (!args.curveFamily || !args.contractCode) {
      setData(null);
      setErrorMessage(null);
      setIsLoading(false);
      return;
    }
    let cancelled = false;
    setIsLoading(true);
    setErrorMessage(null);
    fetchDetailBondFuturesPrice(params)
      .then((p) => {
        if (cancelled) return;
        setData(p);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setErrorMessage(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [args.curveFamily, args.contractCode, args.lookbackDays, args.fieldName, args.asOfDate]);

  return { data, isLoading, errorMessage };
}

// ---------------------------------------------------------------------------
// KPI descriptor builders — compact (3 shell-standard cells per the
// Option-(c) precedent) + extended (9 cells matching mockups/Extended.png).
// ---------------------------------------------------------------------------

/** The compact view's THREE canonical headline KPIs per
 *  rendering_density.md §2.2 + mockups/Compact.png shell-standard density:
 *
 *    1. PRICE              — desk-recognised level in the contract's
 *                            quote_units (32nds for UST, decimal otherwise),
 *                            with the decimal subtext so the trader sees
 *                            both notations.
 *    2. 1D CHANGE          — raw subtraction in quote_units (sign-toned).
 *    3. Z-SCORE (252D)     — z of the PRICE level, toneForZScore.
 */
export function compactKPIs(
  data: BondFuturesPriceLevelOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  const meta = contractMetaFor(cm.contract_code);
  const units = quoteUnitsLabel(cm.quote_units, meta);
  const priceLabel = `PRICE (${units.toUpperCase()})`;
  const changeLabel = `1D CHANGE (${units.toUpperCase()})`;
  const priceSubtext =
    meta?.notation === 'thirty_seconds'
      ? format32ndsSubtext(cm.current_price)
      : undefined;
  const changeSubtext = priceChangePercentSubtext(
    cm.daily_change_price,
    cm.current_price,
    2,
  );
  return [
    {
      label: priceLabel,
      value: formatPrice(cm.current_price, meta),
      tone: 'neutral',
      emphasis: 'primary',
      subtext: priceSubtext,
    },
    {
      label: changeLabel,
      value: formatPriceChange(cm.daily_change_price, meta),
      // Positive price change = lower yield = rally → mint (positive tone).
      // Negative price change = higher yield = sell-off → coral (negative).
      // ``toneForChange`` maps positive → negative-color by sovereign-yield
      // convention; for PRICE we want the inverse, so negate the sign.
      tone: toneForChange(
        cm.daily_change_price != null ? -cm.daily_change_price : null,
      ),
      subtext: changeSubtext,
    },
    {
      label: 'Z-SCORE (252D)',
      value: signedFixed(cm.z_score, 2),
      unit: 'σ',
      tone: toneForZScore(cm.z_score),
      caption: regimeForZScore(cm.z_score),
    },
  ];
}

/** The extended view's FULL KPI strip — 9 cells matching
 *  mockups/Extended.png ordering. */
export function extendedKPIs(
  data: BondFuturesPriceLevelOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  const meta = contractMetaFor(cm.contract_code);
  const units = quoteUnitsLabel(cm.quote_units, meta);
  const u = units.toUpperCase();
  return [
    {
      label: `PRICE (${u})`,
      value: formatPrice(cm.current_price, meta),
      tone: 'neutral',
      subtext:
        meta?.notation === 'thirty_seconds'
          ? format32ndsSubtext(cm.current_price)
          : undefined,
    },
    {
      label: `1D CHANGE (${u})`,
      value: formatPriceChange(cm.daily_change_price, meta),
      tone: toneForChange(
        cm.daily_change_price != null ? -cm.daily_change_price : null,
      ),
      subtext: priceChangePercentSubtext(
        cm.daily_change_price,
        cm.current_price,
        2,
      ),
    },
    {
      label: `5D CHANGE (${u})`,
      value: formatPriceChange(cm.weekly_change_price, meta),
      tone: toneForChange(
        cm.weekly_change_price != null ? -cm.weekly_change_price : null,
      ),
      subtext: priceChangePercentSubtext(
        cm.weekly_change_price,
        cm.current_price,
        2,
      ),
    },
    {
      label: `1M CHANGE (${u})`,
      value: formatPriceChange(cm.monthly_change_price, meta),
      tone: toneForChange(
        cm.monthly_change_price != null ? -cm.monthly_change_price : null,
      ),
      subtext: priceChangePercentSubtext(
        cm.monthly_change_price,
        cm.current_price,
        2,
      ),
    },
    {
      label: 'Z-SCORE (252D)',
      value: signedFixed(cm.z_score, 2),
      unit: 'σ',
      tone: toneForZScore(cm.z_score),
      caption: regimeForZScore(cm.z_score),
    },
    {
      label: 'PERCENTILE (252D)',
      value:
        cm.percentile_252d != null
          ? `${Math.round(cm.percentile_252d)}`
          : '—',
      unit: 'th',
      caption: bucketForPercentile(cm.percentile_252d),
    },
    {
      label: `252D HIGH (${u})`,
      value: formatPrice(cm.high_252d_price, meta),
      tone: 'neutral',
    },
    {
      label: `252D LOW (${u})`,
      value: formatPrice(cm.low_252d_price, meta),
      tone: 'neutral',
    },
    {
      label: 'OBSERVATIONS',
      value: `${cm.observation_count}`,
      tone: 'neutral',
    },
  ];
}

// ---------------------------------------------------------------------------
// Chart series + reference-band builder — ±2σ / ±1.5σ envelope on the
// PRICE series (in quote_units).
// ---------------------------------------------------------------------------

/** Sanity bounds on the bond-futures price series.  Defensive frontend
 *  envelope — quote_units vary, so the bounds are intentionally wide
 *  (covers 'points' UST highs in the 170s; '% of par' EUR contracts in
 *  the 100-200s; 'GBP' gilts; '100 - yield' AU in the 90s).  A single
 *  bad row outside this envelope is null-rendered. */
const PRICE_SANITY_MIN = 0;
const PRICE_SANITY_MAX = 500;

export function sanitisePriceSeries(
  rows: ReadonlyArray<{ date: string; price: number }>,
): Array<{ date: string; value: number | null }> {
  return rows.map((r) => ({
    date: r.date,
    value:
      r.price == null ||
      Number.isNaN(r.price) ||
      r.price < PRICE_SANITY_MIN ||
      r.price > PRICE_SANITY_MAX
        ? null
        : r.price,
  }));
}

export function buildReferenceBands(
  data: BondFuturesPriceLevelOutput,
): ReadonlyArray<ReferenceBand> {
  const sanitised = sanitisePriceSeries(data.time_series ?? []);
  const values = sanitised
    .map((r) => r.value)
    .filter((v): v is number => v != null && !Number.isNaN(v));
  if (values.length < 10) return [];

  const mean = values.reduce((a, b) => a + b, 0) / values.length;
  const variance =
    values.reduce((acc, v) => acc + (v - mean) ** 2, 0) / (values.length - 1);
  const std = Math.sqrt(variance);
  if (!Number.isFinite(std) || std === 0) return [];

  return [
    { value: mean + 2 * std, label: '+2σ', tone: 'extreme', style: 'dashed' },
    { value: mean + 1.5 * std, label: '+1.5σ', tone: 'elevated', style: 'dashed' },
    { value: mean, label: 'Mean', tone: 'neutral', style: 'dashed' },
    { value: mean - 1.5 * std, label: '-1.5σ', tone: 'elevated', style: 'dashed' },
    { value: mean - 2 * std, label: '-2σ', tone: 'positive', style: 'dashed' },
  ];
}

// ---------------------------------------------------------------------------
// Stretch-context + methodology builders for the extended view.
// ---------------------------------------------------------------------------

export function buildStretchContext(
  data: BondFuturesPriceLevelOutput,
): StretchContext | undefined {
  const cm = data.current_metrics;
  if (cm.z_score == null && cm.percentile_252d == null) return undefined;

  const regime = regimeForZScore(cm.z_score);
  const bucket = bucketForPercentile(cm.percentile_252d);
  const interp = interpretationFor(regime, cm.z_score, bucket);

  return {
    percentile:
      cm.percentile_252d != null
        ? { value: cm.percentile_252d, bucket }
        : undefined,
    zScoreRegime:
      cm.z_score != null
        ? {
            value: cm.z_score,
            regime,
            bands: { amber: 1.5, coral: 2.0 },
          }
        : undefined,
    interpretation: interp,
  };
}

function interpretationFor(
  regime: 'Normal' | 'Elevated' | 'Extreme',
  z: number | null | undefined,
  bucket: 'Low' | 'Normal' | 'High',
): string {
  if (z == null) return 'Insufficient data to characterise stretch.';
  const direction = z > 0 ? 'above' : 'below';
  // Price up = yield down = rally (consistent with easier monetary stance
  // or risk-off bid).  Price down = yield up = sell-off.
  const tilt =
    z > 0
      ? 'consistent with a recent rally / lower-yield bid'
      : 'consistent with a recent sell-off / higher-yield move';

  if (regime === 'Extreme') {
    return (
      `Price is ${regime.toLowerCase()} ${direction} its trailing-year mean. ` +
      `The current observation sits in the ${bucket.toLowerCase()}-end of the 252d range ` +
      `and is ${tilt}.`
    );
  }
  if (regime === 'Elevated') {
    return (
      `Price is elevated vs. its trailing-year history. ` +
      `Recent move is ${tilt}.`
    );
  }
  return (
    `Price is within its trailing-year norm; ` +
    `no extreme stretch in either direction.`
  );
}

export function buildMethodologyRows(
  data: BondFuturesPriceLevelOutput,
  effectiveFieldName: string,
): ReadonlyArray<MethodologyRow> {
  const cm = data.current_metrics;
  const meta = contractMetaFor(cm.contract_code);
  const units = quoteUnitsLabel(cm.quote_units, meta);
  return [
    {
      label: 'Contract',
      value: `${cm.contract_code} · ${cm.tenor}${meta ? ` · ${meta.longLabel}` : ''}`,
    },
    {
      label: 'Front underlying',
      value:
        cm.security_name != null
          ? `${cm.security_name}${cm.expiry_date ? ` · expires ${cm.expiry_date}` : ''}`
          : '—',
    },
    {
      label: 'Quote convention',
      value:
        units === '100 - yield'
          ? `${units} (yield = 100 − price; ASX convention)`
          : units,
    },
    {
      label: 'Notation',
      value:
        meta?.notation === 'thirty_seconds'
          ? "32nds (CBOT desk convention — e.g. 129'08 = 129 + 8/32)"
          : `Decimal (${meta?.decimals ?? 2} dp)`,
    },
    {
      label: 'Contract size',
      value: cm.contract_size != null ? `${cm.contract_size}` : '—',
    },
    {
      label: 'Field',
      value: `${effectiveFieldName} (price observation on the rolling-generic)`,
    },
    {
      label: 'Z-score model',
      value: '252d rolling window on the PRICE series (YAML-locked)',
    },
    {
      label: 'Trailing range',
      value: '252 trading days (high / low / percentile, PRICE axis)',
    },
    {
      label: 'Disclosure',
      value: data.methodology_disclosure || '—',
    },
  ];
}

export function buildReferenceChips(): ReadonlyArray<ReferenceChip> {
  return [
    { label: 'ADR 0013 (bond_futures V1 monitors-only)' },
    { label: 'TD#11 (rolling-generic stem disambiguator)' },
    { label: 'CBOT / Eurex / ICE / OSE / MX / ASX contract specs' },
    { label: 'Catalog v2.1 PR23' },
  ];
}
