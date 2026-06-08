"""
detail.py — Rates Workspace Detail Endpoints
===============================================

Full-detail endpoints that power the "See more in workspace" flow:
    /detail/yield, /detail/spread, /detail/cross-market,
    /detail/butterfly, /detail/regime

Each returns the complete tool output including time_series for charts.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import List, Literal, Optional

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.engine import Engine

from api.dependencies import get_engine
from rates_agent.sovereign_bonds.tools.schemas import (
    CurveSpreadInput,
    CurveSpreadOutput,
    CrossMarketSpreadInput,
    CrossMarketSpreadOutput,
    CurveMoveInput,
    CurveMoveOutput,
    ButterflyInput,
    ButterflyOutput,
    YieldLevelInput,
    YieldLevelOutput,
)
from rates_agent.sovereign_bonds.tools.curve_spread import (
    CONFIG_PATH as CURVE_SPREAD_CONFIG_PATH,
    calculate_curve_spread,
)
from rates_agent.sovereign_bonds.tools.curve_move_classifier import (
    CONFIG_PATH as CURVE_MOVE_CONFIG_PATH,
    classify_curve_move_compute,
)
from rates_agent.sovereign_bonds.tools.cross_market_spread import (
    CONFIG_PATH as CROSS_MARKET_CONFIG_PATH,
    calculate_cross_market_spread,
)
from rates_agent.sovereign_bonds.tools.butterfly import (
    CONFIG_PATH as BUTTERFLY_CONFIG_PATH,
    calculate_butterfly,
)
from rates_agent.sovereign_bonds.tools.yield_levels import (
    CONFIG_PATH as YIELD_LEVELS_CONFIG_PATH,
    get_yield_levels,
)
# Phase-1 pilot: standalone-bridge endpoint for the linker
# real_yield_level primitive.  Per
# ``docs_revamped/03_standards/methodology_exposure.md §5`` every new
# tool ships its OWN typed-detail endpoint; this is the first one.
from rates_agent.inflation_indexed_bonds.tools.real_yield_level import (
    CONFIG_PATH as REAL_YIELD_LEVEL_CONFIG_PATH,
    RealYieldLevelInput,
    RealYieldLevelOutput,
    get_real_yield_level,
)
# Phase-1 pilot Stage B/C: standalone-bridge endpoints for the linker
# breakeven_inflation_simple + real_yield_curve_spread primitives.  Per
# ``docs_revamped/03_standards/methodology_exposure.md §5`` every new
# tool ships its OWN typed-detail endpoint consumed by both the
# extended and compact Build views (rendering_density dual-view).
from rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple import (
    CONFIG_PATH as BREAKEVEN_INFLATION_SIMPLE_CONFIG_PATH,
    BreakevenInflationSimpleInput,
    BreakevenInflationSimpleOutput,
    calculate_breakeven_inflation_simple,
)
from rates_agent.inflation_indexed_bonds.tools.real_yield_curve_spread import (
    CONFIG_PATH as REAL_YIELD_CURVE_SPREAD_CONFIG_PATH,
    RealYieldCurveSpreadInput,
    RealYieldCurveSpreadOutput,
    calculate_real_yield_curve_spread,
)
# Standalone-bridge endpoint for the same-country bond-implied breakeven
# butterfly primitive (3-point breakeven curvature).  Per
# ``docs_revamped/03_standards/methodology_exposure.md §5`` every new tool
# ships its OWN typed-detail endpoint consumed by both the extended and
# compact Build views (rendering_density dual-view) + the Monitor tile.
from rates_agent.inflation_indexed_bonds.tools.breakeven_butterfly import (
    CONFIG_PATH as BREAKEVEN_BUTTERFLY_CONFIG_PATH,
    BreakevenButterflyInput,
    BreakevenButterflyOutput,
    calculate_breakeven_butterfly,
)
# Standalone-bridge endpoint for the same-country bond-implied breakeven
# curve spread primitive (2-point tenor spread on a single nominal/linker
# pair — the inflation-compensation term-structure object).  Per
# ``docs_revamped/03_standards/methodology_exposure.md §5`` every new tool
# ships its OWN typed-detail endpoint consumed by both the extended and
# compact Build views (rendering_density dual-view) + the Monitor tile.
# Same-country invariant inherited transitively from the spot breakeven
# primitive's ``_enforce_same_country_invariant`` guard.
from rates_agent.inflation_indexed_bonds.tools.breakeven_curve_spread import (
    CONFIG_PATH as BREAKEVEN_CURVE_SPREAD_CONFIG_PATH,
    BreakevenCurveSpreadInput,
    BreakevenCurveSpreadOutput,
    calculate_breakeven_curve_spread,
)
# Standalone-bridge endpoint for the same-country FORWARD bond-implied
# breakeven inflation primitive (year-weighted linear forward between two
# spot breakeven pillars — e.g. UST/USD_TIPS 5Y5Y, FR_OAT/EUR_FR_LINKER
# 5Y10Y).  Per ``docs_revamped/03_standards/methodology_exposure.md §5``
# every new tool ships its OWN typed-detail endpoint consumed by both the
# extended and compact Build views (rendering_density dual-view) + the
# Monitor tile.  Same-country invariant inherited transitively from the spot
# breakeven primitive's ``_enforce_same_country_invariant`` guard.  Rolling-
# z-score conventions are YAML-locked on this primitive — only
# ``lookback_days`` + ``field_name`` are exposed at the API layer (mirrors
# the sibling breakeven-curve-spread / breakeven-butterfly bridges).
from rates_agent.inflation_indexed_bonds.tools.forward_breakeven_simple import (
    CONFIG_PATH as FORWARD_BREAKEVEN_SIMPLE_CONFIG_PATH,
    ForwardBreakevenSimpleInput,
    ForwardBreakevenSimpleOutput,
    calculate_forward_breakeven_simple,
)
# Standalone-bridge endpoint for the same-country linker real-yield
# butterfly primitive (3-point curvature on a SINGLE linker curve — no
# nominal pair).  Per ``docs_revamped/03_standards/methodology_exposure.md
# §5`` every new tool ships its OWN typed-detail endpoint consumed by
# both the extended and compact Build views (rendering_density dual-view)
# + the Monitor tile.
from rates_agent.inflation_indexed_bonds.tools.real_yield_butterfly import (
    CONFIG_PATH as REAL_YIELD_BUTTERFLY_CONFIG_PATH,
    RealYieldButterflyInput,
    RealYieldButterflyOutput,
    calculate_real_yield_butterfly,
)
# Standalone-bridge endpoint for the same-tenor cross-country bond-implied
# breakeven spread primitive (e.g. UK 10Y BE minus US 10Y BE, FR 10Y BE
# minus US 10Y BE, CA 10Y BE minus US 10Y BE).  Per
# ``docs_revamped/03_standards/methodology_exposure.md §5`` every new tool
# ships its OWN typed-detail endpoint consumed by both the extended and
# compact Build views (rendering_density dual-view) + the Monitor tile.
# Cross-country invariant (country_a vs country_b are different sovereign
# issuers) enforced by the Pydantic schema layer; per-leg same-country
# invariant inherited transitively from each inner
# breakeven_inflation_simple leg.  Output is a SPREAD — ships in BPS to
# mirror the sibling spread primitives.  Sign convention POSITIVE =
# country_a > country_b breakeven; wire-locked.  Surfaces the LOAD-BEARING
# index-family mismatch caveat (CPI-U / RPI / HICPxT / Canada CPI are NOT
# fungible inflation measures) via ``current_metrics.methodology_label``
# (sourced from YAML at runtime, NOT a hardcoded Python literal).
# Rolling-z-score conventions are YAML-locked — only ``lookback_days`` +
# ``field_name`` are exposed at the API layer.
from rates_agent.inflation_indexed_bonds.tools.cross_country_breakeven_spread_simple import (
    CONFIG_PATH as CROSS_COUNTRY_BREAKEVEN_SPREAD_SIMPLE_CONFIG_PATH,
    CrossCountryBreakevenSpreadSimpleInput,
    CrossCountryBreakevenSpreadSimpleOutput,
    calculate_cross_country_breakeven_spread_simple,
)
# Standalone-bridge endpoint for the same-tenor cross-market ZCIS spread
# primitive (e.g. USD_ZCIS 5Y minus EUR_ZCIS 5Y).  First inflation_swaps tool
# under the standalone-bridge contract — per
# ``docs_revamped/03_standards/methodology_exposure.md §5`` every new tool
# ships its OWN typed-detail endpoint consumed by both the extended and
# compact Build views (rendering_density dual-view) + the Monitor tile.
# Surfaces the load-bearing index-family caveat (USD_ZCIS / EUR_ZCIS /
# GBP_ZCIS reference different indices — NOT a clean expected-inflation
# divergence) via the wire's per-leg metadata + ``index_family_caveat``.
from rates_agent.inflation_swaps.tools.cross_market_inflation_swap_spread import (
    CONFIG_PATH as CROSS_MARKET_INFLATION_SWAP_SPREAD_CONFIG_PATH,
    CrossMarketInflationSwapSpreadInput,
    CrossMarketInflationSwapSpreadOutput,
    calculate_cross_market_inflation_swap_spread,
)
# Standalone-bridge endpoint for the same-curve zero-coupon inflation swap
# (ZCIS) butterfly primitive (3-point curvature on ONE ZCIS curve family).
# Per ``docs_revamped/03_standards/methodology_exposure.md §5`` every new tool
# ships its OWN typed-detail endpoint consumed by both the extended and
# compact Build views (rendering_density dual-view) + the Monitor tile.
# Surfaces the load-bearing index-family caveat (CPI-U / HICPxT / RPI are
# distinct inflation measures) via the same-curve invariant — all three legs
# share inflation_index_family / index_lag / interpolation / underlying_index.
from rates_agent.inflation_swaps.tools.inflation_swap_butterfly import (
    CONFIG_PATH as INFLATION_SWAP_BUTTERFLY_CONFIG_PATH,
    InflationSwapButterflyInput,
    InflationSwapButterflyOutput,
    calculate_inflation_swap_butterfly,
)
# Standalone-bridge endpoint for the universe-wide ZCIS rate-extremes scanner.
# First SCANNER-shape primitive under the dual-view contract — the wire
# returns a ranked LIST of (curve_family, tenor) extremes rather than a single
# time series, so this endpoint feeds the per-tool BuildCompact (top-N table)
# and BuildExtended (universe scan + ranked detail) per
# ``docs_revamped/03_standards/rendering_density.md §10`` + the standalone-
# bridge contract (``methodology_exposure.md §5``).
from rates_agent.inflation_swaps.tools.scan_inflation_swaps_extremes import (
    CONFIG_PATH as SCAN_INFLATION_SWAPS_EXTREMES_CONFIG_PATH,
    ScanInflationSwapsExtremesInput,
    ScanInflationSwapsExtremesOutput,
    calculate_scan_inflation_swaps_extremes,
)
# Standalone-bridge endpoint for the universe-wide linker REAL-YIELD extremes
# scanner.  SCANNER-shape primitive under the dual-view contract — the wire
# returns a ranked LIST of (curve_family, tenor) extremes rather than a
# single time series, so this endpoint feeds the per-tool BuildCompact
# (top-N table) + BuildExtended (universe scan + ranked detail) + Monitor
# tile per ``docs_revamped/03_standards/rendering_density.md §10`` + the
# standalone-bridge contract (``methodology_exposure.md §5``).
from rates_agent.inflation_indexed_bonds.tools.scan_inflation_linkers_extremes import (
    CONFIG_PATH as SCAN_INFLATION_LINKERS_EXTREMES_CONFIG_PATH,
    ScanInflationLinkersExtremesInput,
    ScanInflationLinkersExtremesOutput,
    calculate_scan_inflation_linkers_extremes,
)
# Standalone-bridge endpoint for the universe-wide bond-futures extremes
# scanner.  SCANNER-shape primitive under the dual-view contract — the wire
# returns a ranked LIST of (curve_family, contract_code) extremes across
# four metrics (price LEVEL, 1-day price CHANGE, volume LEVEL, open-interest
# LEVEL) rather than a single time series, so this endpoint feeds the per-
# tool BuildCompact (top-N table) + BuildExtended (universe scan + ranked
# detail) + Monitor tile per ``rendering_density.md §10`` + the standalone-
# bridge contract (``methodology_exposure.md §5``).
from rates_agent.bond_futures.tools.scan_bond_futures_extremes import (
    CONFIG_PATH as SCAN_BOND_FUTURES_EXTREMES_CONFIG_PATH,
    ScanBondFuturesExtremesInput,
    ScanBondFuturesExtremesOutput,
    calculate_scan_bond_futures_extremes,
)
# Standalone-bridge endpoint for the universe-wide policy-futures (STIR)
# extremes scanner.  SCANNER-shape primitive under the dual-view contract —
# the wire returns a ranked LIST of (curve_family, strip_position,
# contract_code) extremes across four metrics (implied-rate LEVEL, 1-day
# implied-rate CHANGE in bps, volume LEVEL, open-interest LEVEL) rather
# than a single time series, so this endpoint feeds the per-tool
# BuildCompact (top-N table) + BuildExtended (universe scan + ranked
# detail) + Monitor tile per ``rendering_density.md §10`` + the standalone-
# bridge contract (``methodology_exposure.md §5``).
from rates_agent.policy_futures.tools.scan_policy_futures_extremes import (
    CONFIG_PATH as SCAN_POLICY_FUTURES_EXTREMES_CONFIG_PATH,
    ScanPolicyFuturesExtremesInput,
    ScanPolicyFuturesExtremesOutput,
    calculate_scan_policy_futures_extremes,
)
# Standalone-bridge endpoint for the same-curve OIS butterfly primitive (3-point
# curvature on ONE OIS par-swap curve family — e.g. USD_SOFR_OIS 2s5s10s).
# Per ``docs_revamped/03_standards/methodology_exposure.md §5`` every new tool
# ships its OWN typed-detail endpoint consumed by both the extended and
# compact Build views (rendering_density dual-view) + the Monitor tile.
# Single-curve, raw OIS par-rate-space curvature — POSITIVE = belly cheap,
# NEGATIVE = belly rich.  Risk-neutral policy-pricing caveat surfaces on the
# methodology card (OIS prices the expected policy path, not realised outcomes).
from rates_agent.ois.tools.calculate_ois_butterfly import (
    CONFIG_PATH as OIS_BUTTERFLY_CONFIG_PATH,
    OISButterflyInput,
    OISButterflyOutput,
    calculate_ois_butterfly,
)
# Standalone-bridge endpoint for the same-curve OIS curve-spread primitive
# (2-point tenor spread on ONE OIS par-swap curve family — e.g. USD_SOFR_OIS
# 2s10s).  Same standalone-bridge contract as the OIS butterfly bridge: own
# typed-detail endpoint consumed by both the extended and compact Build views
# (rendering_density dual-view) + the Monitor tile.  Single-curve, raw OIS
# par-rate-space spread (long − short, in BPS).  Risk-neutral policy-pricing
# caveat surfaces on the methodology card (OIS prices the expected policy
# path, not realised outcomes).  Rolling-z-score conventions are YAML-locked
# on this primitive — only ``lookback_days`` + ``field_name`` are exposed.
from rates_agent.ois.tools.curve_spread import (
    CONFIG_PATH as OIS_CURVE_SPREAD_CONFIG_PATH,
    OISCurveSpreadInput,
    OISCurveSpreadOutput,
    calculate_ois_curve_spread,
)
# Standalone-bridge endpoint for the same-tenor cross-market OIS spread
# primitive (e.g. SOFR 2Y minus ESTR 2Y — the G4 policy-path-divergence
# read).  Per ``docs_revamped/03_standards/methodology_exposure.md §5`` every
# new tool ships its OWN typed-detail endpoint consumed by both the extended
# and compact Build views (rendering_density dual-view) + the Monitor tile.
# Two distinct OIS curve families at a shared pillar; the schema layer
# rejects ``curve_family_1 == curve_family_2`` (same-curve tenor spreads
# belong to ``/detail/ois-curve-spread``).  Risk-neutral policy-pricing
# caveat (each currency's curve prices its own central bank's expected
# policy path under the risk-neutral measure — SOFR / ESTR / SONIA / TONA /
# AONIA / CORRA are NOT fungible) surfaces on the methodology card.
# Rolling-z-score conventions are YAML-locked on this primitive — only
# ``lookback_days`` + ``field_name`` are exposed at the API layer.
from rates_agent.ois.tools.cross_market_spread import (
    CONFIG_PATH as OIS_CROSS_MARKET_SPREAD_CONFIG_PATH,
    OISCrossMarketSpreadInput,
    OISCrossMarketSpreadOutput,
    calculate_ois_cross_market_spread,
)
# Standalone-bridge endpoint for the single-tenor OIS par-swap-rate-level
# primitive (e.g. USD_SOFR_OIS 2Y, EUR_ESTR_OIS 10Y, GBP_SONIA_OIS 5Y).
# Per ``docs_revamped/03_standards/methodology_exposure.md §5`` every new tool
# ships its OWN typed-detail endpoint consumed by both the extended and
# compact Build views (rendering_density dual-view) + the Monitor tile.
# Risk-neutral implied policy path caveat (SOFR / EFFR / SONIA / ESTR
# family is the desk-canonical OIS implied policy expectation) surfaces on
# the methodology card.  Rolling-z-score conventions are YAML-locked on
# this primitive — only ``lookback_days`` + ``field_name`` are exposed at
# the input layer (mirrors the OIS curve_spread / butterfly siblings).
from rates_agent.ois.tools.rate_level import (
    CONFIG_PATH as OIS_RATE_LEVEL_CONFIG_PATH,
    OISRateLevelInput,
    OISRateLevelOutput,
    get_ois_rate_level,
)
# Standalone-bridge endpoint for the implied OIS forward-rate primitive
# (e.g. SOFR 1Y1Y, 5Y5Y ESTR, 2Y1Y SONIA, ad-hoc date-window forwards).
# Per ``docs_revamped/03_standards/methodology_exposure.md §5`` every new tool
# ships its OWN typed-detail endpoint consumed by both the extended and
# compact Build views (rendering_density dual-view) + the Monitor tile.
# Risk-neutral implied policy path caveat (forwards on OIS curves price the
# expected policy path, not realised central-bank decisions) surfaces on
# the methodology card.  Two equivalent input modes — tenor-pair OR
# date-pair — supply exactly ONE; the schema layer rejects partial /
# both modes at construction time.  Rolling-z-score conventions are
# YAML-locked on this primitive — only ``lookback_days`` + ``field_name``
# are exposed at the input layer (mirrors the OIS rate_level /
# curve_spread / butterfly siblings).
from rates_agent.ois.tools.forward_rate import (
    CONFIG_PATH as OIS_FORWARD_RATE_CONFIG_PATH,
    OISForwardRateInput,
    OISForwardRateOutput,
    calculate_ois_forward_rate,
)
# Standalone-bridge endpoint for the OIS-implied financing rate primitive
# (e.g. SOFR-proxied UST 10Y financing, ESTR-proxied Bund financing).
# ARCHITECTURAL DEVIATION — ROUTE-SIDE SYNTHESIS.  Unlike sibling snapshot
# tools, the backend ``FinancingRateOutput`` does NOT carry the standard
# snapshot ``current_metrics`` + ``time_series`` fields (it ships
# ``mean_rate_pct`` + ``methodology_disclosures`` + a ``Panel`` artifact
# instead — the Panel-based shape needed by the ``evaluate_trades``
# workflow consumer; the MCP layer drops the panel before LLM serialisation).
# This route ACCESSES the panel directly via ``result['panel'].payload``
# and SYNTHESIZES the snapshot-shape response (FinancingRateDetailResponse)
# on the fly — backend Output preserved, frontend consumes a shape-
# equivalent payload identical in structure to every other snapshot tool.
# See human_required resolution Option (a) — 2026-06-08.
from rates_agent.ois.tools.financing_rate import (
    CONFIG_PATH as FINANCING_RATE_CONFIG_PATH,
    FinancingRateInput,
    compute_financing_rate,
)
# Standalone-bridge endpoint for the single-pillar zero-coupon inflation swap
# (ZCIS) rate-level primitive (e.g. USD_ZCIS 5Y, EUR_ZCIS 10Y, GBP_ZCIS 2Y).
# Per ``docs_revamped/03_standards/methodology_exposure.md §5`` every new tool
# ships its OWN typed-detail endpoint consumed by both the extended and
# compact Build views (rendering_density dual-view) + the Monitor tile.
# Surfaces the load-bearing reference metadata (``inflation_index_family``,
# ``index_lag``, ``interpolation``, ``underlying_index``) so the desk can
# interpret the level honestly — USD_ZCIS / EUR_ZCIS / GBP_ZCIS reference
# distinct inflation indices (CPI-U / HICPxT / RPI) with different lags +
# interpolation conventions.  Rolling-z-score conventions are YAML-locked
# on this primitive — only ``lookback_days`` + ``field_name`` are exposed.
from rates_agent.inflation_swaps.tools.inflation_swap_rate_level import (
    CONFIG_PATH as INFLATION_SWAP_RATE_LEVEL_CONFIG_PATH,
    InflationSwapRateLevelInput,
    InflationSwapRateLevelOutput,
    calculate_inflation_swap_rate_level,
)
# Standalone-bridge endpoint for the same-currency swap-vs-bond inflation
# basis primitive (USD_ZCIS 10Y minus UST/USD_TIPS 10Y bond-implied
# breakeven; EUR_ZCIS 10Y minus FR_OAT/EUR_FR_LINKER 10Y breakeven;
# GBP_ZCIS 10Y minus UK_GILT/GBP_LINKER 10Y breakeven).  Per
# ``docs_revamped/03_standards/methodology_exposure.md §5`` every new tool
# ships its OWN typed-detail endpoint consumed by both the extended and
# compact Build views (rendering_density dual-view) + the Monitor tile.
# Output is a SPREAD — the basis ships in BPS (not PERCENT) to mirror
# sibling spread primitives.  Sign convention POSITIVE = ZCIS rich vs
# bond breakeven; wire-locked at ``zcis_minus_breakeven``.  NOT a clean
# liquidity-premium read — the LOAD-BEARING caveat (index-lag differences,
# linker on-the-run effects, structural ZCIS basis) surfaces verbatim via
# ``current_metrics.methodology_label`` (sourced from YAML at runtime, NOT
# a hardcoded Python literal) PLUS the per-leg index-family metadata +
# derived ``index_families_match`` / ``index_family_caveat`` pair.
# Rolling-z-score conventions are YAML-locked — only ``lookback_days`` +
# ``field_name`` are exposed at the API layer.
from rates_agent.inflation_swaps.tools.swap_breakeven_basis_simple import (
    CONFIG_PATH as SWAP_BREAKEVEN_BASIS_SIMPLE_CONFIG_PATH,
    SwapBreakevenBasisSimpleInput,
    SwapBreakevenBasisSimpleOutput,
    calculate_swap_breakeven_basis_simple,
)
# Standalone-bridge endpoint for the same-curve ZCIS forward-rate primitive
# (USD_ZCIS 5Y5Y, EUR_ZCIS 5Y5Y, GBP_ZCIS 2Y3Y).  Per
# ``docs_revamped/03_standards/methodology_exposure.md §5`` every new tool
# ships its OWN typed-detail endpoint consumed by both the extended and
# compact Build views (rendering_density dual-view) + the Monitor tile.
# Output is FORWARD INFLATION COMPENSATION — not a clean forward expected-
# inflation read; the wire-honesty caveat lives in ``methodology_label`` and
# is sourced from the YAML at runtime (NOT a hardcoded Python literal).
# Surfaces the load-bearing reference-metadata (``inflation_index_family``,
# ``index_lag``, ``interpolation``, ``underlying_index``) on the wire so the
# desk can interpret the forward honestly under the per-curve index-family
# quirks (US CPI-U NSA, EU HICPxT, UK RPI).  Same-curve invariant enforced
# by the input layer — cross-curve forwards belong to a separate primitive.
# Rolling-z-score conventions are YAML-locked — only ``lookback_days`` +
# ``field_name`` are exposed (mirrors the OIS forward_rate / ZCIS rate_level
# siblings).
from rates_agent.inflation_swaps.tools.inflation_swap_forward import (
    CONFIG_PATH as INFLATION_SWAP_FORWARD_CONFIG_PATH,
    InflationSwapForwardInput,
    InflationSwapForwardOutput,
    calculate_inflation_swap_forward,
)
# Standalone-bridge endpoint for the same-curve ZCIS tenor-spread primitive
# (USD_ZCIS 5s10s, EUR_ZCIS 5s30s, GBP_ZCIS 2s10s).  Same payload feeds the
# dual-view Build surfaces + the Monitor tile per the rendering_density
# dual-view contract.  Same-curve, two-tenor primitive: one ``curve_family`` +
# two strictly-ordered tenors; cross-curve combinations belong to the separate
# cross_market_inflation_swap_spread primitive.  Rolling-z-score conventions
# YAML-locked.
from rates_agent.inflation_swaps.tools.inflation_swap_curve_spread import (
    CONFIG_PATH as INFLATION_SWAP_CURVE_SPREAD_CONFIG_PATH,
    InflationSwapCurveSpreadInput,
    InflationSwapCurveSpreadOutput,
    calculate_inflation_swap_curve_spread,
)
# Standalone-bridge endpoint for the policy_futures strip-position price level
# primitive (SFR1 / SFR2 / ER1 / SFI1 / ... — STIR strip slots on
# SOFR_FUT / EUR_SHORT_RATE_FUT / SONIA_FUT).  Keyed by
# ``(curve_family, strip_position)`` per ADR 0013.  Same standalone-bridge
# contract as the other rates primitives: own typed-detail endpoint consumed
# by both the extended and compact Build views (rendering_density dual-view)
# + the Monitor tile.  Methodology disclosure flows verbatim from
# compute()'s ``methodology_disclosure`` string (NOT a hardcoded TS literal).
from rates_agent.policy_futures.tools.futures_price_level import (
    CONFIG_PATH as POLICY_FUTURES_PRICE_LEVEL_CONFIG_PATH,
    FuturesPriceLevelInput,
    FuturesPriceLevelOutput,
    calculate_futures_price_level,
)
# Catalog tool-23 — bond_futures front-month rolling-generic price level
# (TY1 / UXY1 / RX1 / G1 / JB1 / OAT1 / IK1 / KOA1 / CN1 / YM1 / XM1 / ...).
# Same MCP function NAME as the policy_futures sibling
# (``get_futures_price_level_tool``) but registered in a DIFFERENT MCP server
# (``rates_agent.bond_futures.mcp_server``) and backed by a DIFFERENT
# sub-package + a DIFFERENT Pydantic schema (price space, not implied-rate
# space; bespoke ``{date, price}`` time_series rows in per-contract
# ``quote_units`` rather than implied-rate PERCENT).  Disambiguated here
# via an import alias so the standalone-bridge response_model name does
# not collide with the policy_futures cousin.
from rates_agent.bond_futures.tools.futures_price_level import (
    CONFIG_PATH as BOND_FUTURES_PRICE_LEVEL_CONFIG_PATH,
    FuturesPriceLevelInput as BondFuturesPriceLevelInput,
    FuturesPriceLevelOutput as BondFuturesPriceLevelOutput,
    calculate_futures_price_level as calculate_bond_futures_price_level,
)
# Standalone-bridge endpoint for the policy_futures same-curve simple-butterfly
# primitive (e.g. SOFR_FUT SFR1-SFR2-SFR3 front-pack curvature,
# EUR_SHORT_RATE_FUT ER1-ER2-ER4 whites/reds curvature).  Keyed by
# ``(curve_family, strip_position_wing_short, strip_position_body,
# strip_position_wing_long)`` per ADR 0013 — same strip-position-keying as
# the futures_price_level sibling.  Same standalone-bridge contract as the
# other rates primitives: own typed-detail endpoint consumed by both the
# extended and compact Build views (rendering_density dual-view) + the
# Monitor tile.  Methodology disclosure flows verbatim from compute()'s
# ``methodology_disclosure`` string (NOT a hardcoded TS literal).  The
# schema layer rejects unordered / duplicate orderings via the
# ``_strip_positions_must_be_ordered`` validator.
from rates_agent.policy_futures.tools.futures_butterfly_simple import (
    CONFIG_PATH as POLICY_FUTURES_BUTTERFLY_SIMPLE_CONFIG_PATH,
    FuturesButterflySimpleInput,
    FuturesButterflySimpleOutput,
    calculate_futures_butterfly_simple,
)
# Catalog tool-20 — same-curve calendar spread between two strip-position
# slots on one ``curve_family`` (e.g. SOFR_FUT SFR1-SFR2, EUR_SHORT_RATE_FUT
# ER1-ER4).  Same standalone-bridge contract as the other rates primitives:
# own typed-detail endpoint consumed by both Build views + the Monitor tile.
# Methodology disclosure flows verbatim from compute()'s
# ``methodology_disclosure`` string (NOT a hardcoded TS literal).
from rates_agent.policy_futures.tools.futures_calendar_spread import (
    CONFIG_PATH as POLICY_FUTURES_CALENDAR_SPREAD_CONFIG_PATH,
    FuturesCalendarSpreadInput,
    FuturesCalendarSpreadOutput,
    calculate_futures_calendar_spread,
)
# Catalog tool-21 — matched-strip cross-market implied-rate differential
# between two ``curve_family`` values at one strip position (e.g.
# SOFR_FUT vs SONIA_FUT strip 1 = SFR1 − SFI1, SOFR_FUT vs
# EUR_SHORT_RATE_FUT strip 4 = SFR4 − ER4).  Same standalone-bridge
# contract as the other rates primitives: own typed-detail endpoint
# consumed by both Build views + the Monitor tile.  Methodology
# disclosure flows verbatim from compute()'s ``methodology_disclosure``
# string (NOT a hardcoded TS literal), including per-leg short-rate
# regime labels (RFR vs IBOR) and explicit mixed-regime call-out per
# the catalog guardrail (NO pack-average collapse).
from rates_agent.policy_futures.tools.futures_cross_market_spread import (
    CONFIG_PATH as POLICY_FUTURES_CROSS_MARKET_SPREAD_CONFIG_PATH,
    FuturesCrossMarketSpreadInput,
    FuturesCrossMarketSpreadOutput,
    calculate_futures_cross_market_spread,
)
# Catalog tool-22 — same-curve pack-average implied rate (arithmetic mean
# across 4 consecutive quarterly STIR contracts) on a SINGLE
# ``curve_family`` (whites = SFR1..SFR4 / SFI5..SFI8 / etc; reds = SFR5..
# SFR8 / SFI5..SFI8).  Same standalone-bridge contract as the other rates
# primitives: own typed-detail endpoint consumed by both Build views + the
# Monitor tile.  Methodology disclosure flows verbatim from compute()'s
# ``methodology_disclosure`` string (NOT a hardcoded TS literal); includes
# the arithmetic-mean weighting, per-curve_family regime label (RFR vs
# IBOR), inverse-pricing rule, z-score lookback window, strip-position
# keying, and explicit refusal of duration-weighted / meeting-by-meeting
# pack variants (PR11 planned-extension territory).
from rates_agent.policy_futures.tools.futures_pack_average_simple import (
    CONFIG_PATH as POLICY_FUTURES_PACK_AVERAGE_SIMPLE_CONFIG_PATH,
    FuturesPackAverageSimpleInput,
    FuturesPackAverageSimpleOutput,
    calculate_futures_pack_average_simple,
)
from rates_agent.sovereign_bonds.tools.zscore_custom import (
    CONFIG_PATH as ZSCORE_CUSTOM_CONFIG_PATH,
    ZscoreCustomInput,
    ZscoreCustomOutput,
    calculate_zscore_custom,
)
from rates_agent.sovereign_bonds.tools.beta_adjusted_spread import (
    CONFIG_PATH as BETA_ADJUSTED_SPREAD_CONFIG_PATH,
    BetaAdjustedSpreadInput,
    BetaAdjustedSpreadOutput,
    calculate_beta_adjusted_spread,
)
from rates_agent.sovereign_bonds.tools.pca_yield_curve import (
    CONFIG_PATH as PCA_YIELD_CURVE_CONFIG_PATH,
    PcaYieldCurveInput,
    PcaYieldCurveOutput,
    calculate_pca_yield_curve,
)
from shared.config import load_tool_config

logger = logging.getLogger("api.routes.rates.detail")

router = APIRouter()


# ============================================================================
# HELPERS
# ============================================================================

def _tool_result_or_raise(result: dict, context: str) -> dict:
    """Check a tool result dict for an error key and raise the
    correct HTTP status.

    Status ladder:
      404 — data does not exist for the requested instrument / window.
      503 — database / infrastructure is unavailable.
      422 — caller's input is syntactically valid but semantically
            rejected by a tool-level cross-layer contract (e.g.,
            zscore_custom's z_score_window_days < the YAML's
            z_score_min_periods).  These are USER-INPUT errors, not
            server bugs, so the client gets a 422 rather than a 500.
      500 — unclassified.  Real server bug; should be rare.
    """
    if "error" not in result:
        return result

    error_msg = result["error"]
    lower = error_msg.lower()

    not_found_phrases = [
        "no data found", "missing tenor", "missing curve",
        "no overlapping observations", "no observations within",
        "all values were null", "insufficient data",
        "no instruments found",
    ]
    if any(phrase in lower for phrase in not_found_phrases):
        raise HTTPException(status_code=404, detail=f"{context}: {error_msg}")

    infra_phrases = [
        "database connection failed", "connection refused",
        "timeout", "could not connect", "operational error",
    ]
    if any(phrase in lower for phrase in infra_phrases):
        raise HTTPException(status_code=503, detail=f"{context}: {error_msg}")

    # User-input mismatches against tool-level cross-layer contracts.
    # Today's only producer is zscore_custom's small-window guard
    # ("z_score_window_days=N is smaller than the YAML's
    # z_score_min_periods=M"); future tools that surface similar
    # input-vs-config errors should reuse this phrase shape so the
    # client gets a 422 rather than a 500.
    user_input_phrases = [
        "is smaller than the yaml",
        "is larger than the yaml",
        "conflicts with the yaml",
        "duplicate tenor",
    ]
    if any(phrase in lower for phrase in user_input_phrases):
        raise HTTPException(status_code=422, detail=f"{context}: {error_msg}")

    raise HTTPException(status_code=500, detail=f"{context}: {error_msg}")


# ============================================================================
# WORKSPACE ENDPOINTS
# ============================================================================

@router.get("/detail/yield", response_model=YieldLevelOutput, summary="Yield Level Detail (workspace)")
def yield_detail(
    engine: Engine = Depends(get_engine),
    curve_family: str = Query(..., description="e.g. 'UST'"),
    tenor: str = Query(..., description="e.g. '10Y'"),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    field_name: Optional[str] = Query(
        default=None,
        description=(
            "Bloomberg field mnemonic.  Omit to use the tool's "
            "bundled ``default_field_name`` convention from "
            "yield_levels/config.yaml (currently 'YLD_YTM_MID').  "
            "Pass explicitly to override per request."
        ),
    ),
):
    """``field_name`` defaults to None at the query layer so the tool's
    compute() can resolve it against the YAML's ``default_field_name``
    convention.  A previous version hardcoded
    ``Query(default="YLD_YTM_MID")`` which silently shadowed the YAML
    default — same fix as the curve_move_classifier wrapper-shadowing
    cleanup (commit b2605ee)."""
    try:
        params = YieldLevelInput(
            curve_family=curve_family, tenor=tenor,
            lookback_days=lookback_days, field_name=field_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    # Pass yield_levels' bundled config explicitly so the config
    # dependency is observable at the endpoint.  load_tool_config is
    # process-cached, so this is a free lookup after the first call.
    try:
        yl_config = load_tool_config(YIELD_LEVELS_CONFIG_PATH)
        result = get_yield_levels(
            engine=engine, params=params, config=yl_config,
        )
    except Exception as exc:
        logger.exception("detail/yield: tool failed for %s %s", curve_family, tenor)
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    _tool_result_or_raise(result, f"Yield level for {curve_family} {tenor}")
    return result


# ============================================================================
# /detail/real_yield  — Phase-1 pilot standalone-bridge endpoint
# ----------------------------------------------------------------------------
# Per ``docs_revamped/03_standards/methodology_exposure.md §5`` every new
# primitive ships its OWN typed-detail endpoint (no shared shell reuse).
# This route mirrors ``/detail/yield``'s shape but operates on the linker
# real-yield primitive AND exposes the three Phase-1 methodology overrides
# (z_score_window_days / z_score_min_periods / z_score_ddof) per the
# rendering_density standard so both the extended and compact Build
# surfaces can pass them through to compute().
#
# The same payload feeds BOTH the extended Build view (mounted in
# single-tool queries) and the compact Build view (mounted in multi-
# tool query DAG nodes); per rendering_density.md §10 the compact view
# just renders less of the payload.  No separate "summary" endpoint.
# ============================================================================
@router.get(
    "/detail/real_yield",
    response_model=RealYieldLevelOutput,
    summary="Real Yield Level Detail (linker, standalone bridge)",
)
def real_yield_detail(
    engine: Engine = Depends(get_engine),
    curve_family: str = Query(..., description="Linker curve family — USD_TIPS / GBP_LINKER / EUR_FR_LINKER / CAD_RRB"),
    tenor: str = Query(..., description="Tenor point on the linker curve, e.g. '10Y'"),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    field_name: Optional[str] = Query(
        default=None,
        description=(
            "Bloomberg field mnemonic.  Omit (None) to use the tool's "
            "bundled ``default_field_name`` convention from "
            "real_yield_level/config.yaml (currently 'YLD_YTM_MID' — "
            "the linker real-yield-to-maturity mnemonic).  Per the "
            "Phase-1 exposure decision in config.yaml:default_field_name.exposure."
        ),
    ),
    z_score_window_days: Optional[int] = Query(
        default=None,
        ge=60,
        le=1260,
        description=(
            "Trading-day window for the rolling z-score.  Omit (None) "
            "to use the YAML default (currently 252).  Pass 60 / 126 "
            "for tactical framing or 504 for structural-regime work.  "
            "Per config.yaml:z_score_window_days.exposure."
        ),
    ),
    z_score_min_periods: Optional[int] = Query(
        default=None,
        ge=20,
        le=252,
        description=(
            "Minimum observations before the rolling z-score is "
            "emitted.  Omit (None) to use the YAML default (60).  "
            "Scale with ``z_score_window_days`` when overriding."
        ),
    ),
    z_score_ddof: Optional[int] = Query(
        default=None,
        ge=0,
        le=1,
        description=(
            "Standard-deviation degrees of freedom.  Omit (None) to "
            "use the YAML default (1 — sample std).  0 = population std."
        ),
    ),
):
    """Same payload + override semantics as the MCP wrapper; consumed by
    the frontend module's ``surfaces/BuildExtended.tsx`` AND
    ``surfaces/BuildCompact.tsx`` per the rendering-density dual-view
    contract.  None-sentinels on the four exposed Phase-1 conventions
    fall through to the YAML defaults via compute._conventions_from_config.
    """
    try:
        params = RealYieldLevelInput(
            curve_family=curve_family,
            tenor=tenor,
            lookback_days=lookback_days,
            field_name=field_name,
            z_score_window_days=z_score_window_days,
            z_score_min_periods=z_score_min_periods,
            z_score_ddof=z_score_ddof,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    try:
        ryl_config = load_tool_config(REAL_YIELD_LEVEL_CONFIG_PATH)
        result = get_real_yield_level(
            engine=engine, params=params, config=ryl_config,
        )
    except Exception as exc:
        logger.exception(
            "detail/real_yield: tool failed for %s %s", curve_family, tenor,
        )
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    _tool_result_or_raise(
        result, f"Real yield level for {curve_family} {tenor}",
    )
    return result


# ----------------------------------------------------------------------------
# /detail/breakeven  — Stage-B standalone-bridge endpoint
# ----------------------------------------------------------------------------
# Per ``docs_revamped/03_standards/methodology_exposure.md §5`` the
# breakeven_inflation_simple primitive ships its OWN typed-detail
# endpoint (no shared shell reuse).  Exposes the same four Phase-1
# methodology overrides as real_yield (z_score_window_days /
# z_score_min_periods / z_score_ddof / field_name) so both the
# extended and compact Build surfaces pass them through to compute().
# The same payload feeds BOTH views (rendering_density.md §10).
# ============================================================================
@router.get(
    "/detail/breakeven",
    response_model=BreakevenInflationSimpleOutput,
    summary="Bond-Implied Breakeven Inflation Detail (linker, standalone bridge)",
)
def breakeven_detail(
    engine: Engine = Depends(get_engine),
    nominal_curve_family: str = Query(..., description="Nominal sovereign curve family — UST / UK_GILT / FR_OAT / CANADA_GOVT"),
    linker_curve_family: str = Query(..., description="Linker curve family — USD_TIPS / GBP_LINKER / EUR_FR_LINKER / CAD_RRB"),
    tenor: str = Query(..., description="Tenor point on both curves, e.g. '10Y'"),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    field_name: Optional[str] = Query(
        default=None,
        description=(
            "Bloomberg field mnemonic for BOTH legs.  Omit (None) to use "
            "the tool's bundled ``default_field_name`` convention "
            "(currently 'YLD_YTM_MID').  Per "
            "config.yaml:default_field_name.exposure."
        ),
    ),
    z_score_window_days: Optional[int] = Query(
        default=None,
        ge=60,
        le=1260,
        description=(
            "Trading-day window for the rolling z-score of the breakeven "
            "(bps) series.  Omit (None) to use the YAML default "
            "(currently 252).  Pass 60 / 126 for tactical framing or 504 "
            "for structural-regime work.  Per "
            "config.yaml:z_score_window_days.exposure."
        ),
    ),
    z_score_min_periods: Optional[int] = Query(
        default=None,
        ge=20,
        le=252,
        description=(
            "Minimum observations before the rolling z-score is emitted.  "
            "Omit (None) to use the YAML default (60).  Scale with "
            "``z_score_window_days`` when overriding."
        ),
    ),
    z_score_ddof: Optional[int] = Query(
        default=None,
        ge=0,
        le=1,
        description=(
            "Standard-deviation degrees of freedom.  Omit (None) to use "
            "the YAML default (1 — sample std).  0 = population std."
        ),
    ),
):
    """Same payload + override semantics as the MCP wrapper; consumed by
    the frontend module's ``surfaces/BuildExtended.tsx`` AND
    ``surfaces/BuildCompact.tsx`` per the rendering-density dual-view
    contract.  None-sentinels on the four exposed Phase-1 conventions
    fall through to the YAML defaults via compute._conventions_from_config.
    """
    try:
        params = BreakevenInflationSimpleInput(
            nominal_curve_family=nominal_curve_family,
            linker_curve_family=linker_curve_family,
            tenor=tenor,
            lookback_days=lookback_days,
            field_name=field_name,
            z_score_window_days=z_score_window_days,
            z_score_min_periods=z_score_min_periods,
            z_score_ddof=z_score_ddof,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    try:
        bei_config = load_tool_config(BREAKEVEN_INFLATION_SIMPLE_CONFIG_PATH)
        result = calculate_breakeven_inflation_simple(
            engine=engine, params=params, config=bei_config,
        )
    except Exception as exc:
        logger.exception(
            "detail/breakeven: tool failed for %s vs %s @ %s",
            nominal_curve_family, linker_curve_family, tenor,
        )
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    _tool_result_or_raise(
        result,
        f"Breakeven inflation for {nominal_curve_family} vs "
        f"{linker_curve_family} {tenor}",
    )
    return result


# ----------------------------------------------------------------------------
# /detail/real_yield_curve_spread  — Stage-C standalone-bridge endpoint
# ----------------------------------------------------------------------------
# Per ``docs_revamped/03_standards/methodology_exposure.md §5`` the
# real_yield_curve_spread primitive ships its OWN typed-detail endpoint.
# The three rolling-z-score overrides apply to the spread's own z-score +
# the fetch-window buffer; field_name flows to both endpoint level calls.
# The same payload feeds BOTH the extended and compact Build views.
# ============================================================================
@router.get(
    "/detail/real_yield_curve_spread",
    response_model=RealYieldCurveSpreadOutput,
    summary="Real-Yield Curve Spread Detail (linker, standalone bridge)",
)
def real_yield_curve_spread_detail(
    engine: Engine = Depends(get_engine),
    curve_family: str = Query(..., description="Linker curve family — USD_TIPS / GBP_LINKER / EUR_FR_LINKER / CAD_RRB"),
    short_tenor: str = Query(..., description="Short tenor of the spread, e.g. '5Y' for 5s10s"),
    long_tenor: str = Query(..., description="Long tenor of the spread, e.g. '10Y' for 5s10s — must be strictly longer than short_tenor"),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    field_name: Optional[str] = Query(
        default=None,
        description=(
            "Bloomberg field mnemonic for BOTH endpoint real-yield "
            "series.  Omit (None) to use the tool's bundled "
            "``default_field_name`` convention (currently 'YLD_YTM_MID'). "
            "Per config.yaml:default_field_name.exposure."
        ),
    ),
    z_score_window_days: Optional[int] = Query(
        default=None,
        ge=60,
        le=1260,
        description=(
            "Trading-day window for the rolling z-score of the real-yield "
            "curve spread (percent) series.  Omit (None) to use the YAML "
            "default (currently 252).  Applies to the spread's own "
            "z-score AND the fetch-window buffer; the inner endpoint "
            "level calls use the YAML default.  Per "
            "config.yaml:z_score_window_days.exposure."
        ),
    ),
    z_score_min_periods: Optional[int] = Query(
        default=None,
        ge=20,
        le=252,
        description=(
            "Minimum observations before the rolling z-score is emitted.  "
            "Omit (None) to use the YAML default (60).  Scale with "
            "``z_score_window_days`` when overriding."
        ),
    ),
    z_score_ddof: Optional[int] = Query(
        default=None,
        ge=0,
        le=1,
        description=(
            "Standard-deviation degrees of freedom.  Omit (None) to use "
            "the YAML default (1 — sample std).  0 = population std."
        ),
    ),
):
    """Same payload + override semantics as the MCP wrapper; consumed by
    the frontend module's ``surfaces/BuildExtended.tsx`` AND
    ``surfaces/BuildCompact.tsx`` per the rendering-density dual-view
    contract.  None-sentinels on the four exposed Phase-1 conventions
    fall through to the YAML defaults via compute._conventions_from_config.
    """
    try:
        params = RealYieldCurveSpreadInput(
            curve_family=curve_family,
            short_tenor=short_tenor,
            long_tenor=long_tenor,
            lookback_days=lookback_days,
            field_name=field_name,
            z_score_window_days=z_score_window_days,
            z_score_min_periods=z_score_min_periods,
            z_score_ddof=z_score_ddof,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    try:
        rycs_config = load_tool_config(REAL_YIELD_CURVE_SPREAD_CONFIG_PATH)
        result = calculate_real_yield_curve_spread(
            engine=engine, params=params, config=rycs_config,
        )
    except Exception as exc:
        logger.exception(
            "detail/real_yield_curve_spread: tool failed for %s %s%s",
            curve_family, short_tenor, long_tenor,
        )
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    _tool_result_or_raise(
        result,
        f"Real-yield curve spread for {curve_family} "
        f"{short_tenor}/{long_tenor}",
    )
    return result


# ----------------------------------------------------------------------------
# /detail/breakeven-butterfly  — same-country breakeven butterfly bridge
# ----------------------------------------------------------------------------
# Per ``docs_revamped/03_standards/methodology_exposure.md §5`` the
# breakeven_butterfly primitive ships its OWN typed-detail endpoint.
# The same payload feeds BOTH the extended and compact Build views and
# the Monitor tile (rendering_density.md §10).  Same four Phase-1
# methodology overrides as the spot breakeven primitive (the inner
# composed calls share the same z-score window / min-periods / ddof
# / field_name semantics).
# ============================================================================
@router.get(
    "/detail/breakeven-butterfly",
    response_model=BreakevenButterflyOutput,
    summary="Bond-Implied Breakeven Butterfly Detail (standalone bridge)",
)
def breakeven_butterfly_detail(
    engine: Engine = Depends(get_engine),
    nominal_curve_family: str = Query(..., description="Nominal sovereign curve family — UST / UK_GILT / FR_OAT / CANADA_GOVT"),
    linker_curve_family: str = Query(..., description="Linker curve family — USD_TIPS / GBP_LINKER / EUR_FR_LINKER / CAD_RRB"),
    short_tenor: str = Query(..., description="Short wing tenor (e.g. '2Y' for 2s5s10s)"),
    belly_tenor: str = Query(..., description="Belly tenor (e.g. '5Y' for 2s5s10s)"),
    long_tenor: str = Query(..., description="Long wing tenor (e.g. '10Y' for 2s5s10s) — must satisfy short < belly < long"),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    field_name: Optional[str] = Query(
        default=None,
        description=(
            "Bloomberg field mnemonic for ALL SIX underlying series "
            "(nominal + linker at each endpoint tenor).  Omit (None) to "
            "use the tool's bundled ``default_field_name`` convention "
            "(currently 'YLD_YTM_MID')."
        ),
    ),
):
    """Same payload semantics as the MCP wrapper; consumed by the
    frontend module's ``surfaces/BuildExtended.tsx``,
    ``surfaces/BuildCompact.tsx``, AND the Monitor widget per the
    rendering-density dual-view + monitor contract.

    The breakeven-butterfly primitive intentionally does NOT expose the
    Phase-1 z-score overrides at its Input layer — its rolling-z-score
    conventions are sourced from the YAML at compute() time only.
    """
    try:
        params = BreakevenButterflyInput(
            nominal_curve_family=nominal_curve_family,
            linker_curve_family=linker_curve_family,
            short_tenor=short_tenor,
            belly_tenor=belly_tenor,
            long_tenor=long_tenor,
            lookback_days=lookback_days,
            field_name=field_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    try:
        bbf_config = load_tool_config(BREAKEVEN_BUTTERFLY_CONFIG_PATH)
        result = calculate_breakeven_butterfly(
            engine=engine, params=params, config=bbf_config,
        )
    except Exception as exc:
        logger.exception(
            "detail/breakeven-butterfly: tool failed for %s vs %s %s/%s/%s",
            nominal_curve_family, linker_curve_family,
            short_tenor, belly_tenor, long_tenor,
        )
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    _tool_result_or_raise(
        result,
        f"Breakeven butterfly for {nominal_curve_family} vs "
        f"{linker_curve_family} {short_tenor}/{belly_tenor}/{long_tenor}",
    )
    return result


# ----------------------------------------------------------------------------
# /detail/breakeven-curve-spread  — same-country breakeven curve spread bridge
# ----------------------------------------------------------------------------
# Per ``docs_revamped/03_standards/methodology_exposure.md §5`` the
# breakeven_curve_spread primitive ships its OWN typed-detail endpoint.
# Same-country 2-point tenor spread on a single nominal/linker pair (e.g.
# UST/USD_TIPS 2s10s breakeven, UK_GILT/GBP_LINKER 5s30s breakeven).  The
# same payload feeds BOTH the extended and compact Build views and the
# Monitor tile (rendering_density.md §10).  The rolling-z-score conventions
# are YAML-locked on this primitive — no input-layer overrides for window /
# min-periods / ddof (mirrors the sibling breakeven-butterfly bridge).
# ============================================================================
@router.get(
    "/detail/breakeven-curve-spread",
    response_model=BreakevenCurveSpreadOutput,
    summary="Bond-Implied Breakeven Curve Spread Detail (standalone bridge)",
)
def breakeven_curve_spread_detail(
    engine: Engine = Depends(get_engine),
    nominal_curve_family: str = Query(..., description="Nominal sovereign curve family — UST / UK_GILT / FR_OAT / CANADA_GOVT"),
    linker_curve_family: str = Query(..., description="Linker curve family — USD_TIPS / GBP_LINKER / EUR_FR_LINKER / CAD_RRB"),
    short_tenor: str = Query(..., description="Short tenor of the spread (e.g. '2Y' for 2s10s)"),
    long_tenor: str = Query(..., description="Long tenor of the spread (e.g. '10Y' for 2s10s) — must be strictly longer than short_tenor"),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    field_name: Optional[str] = Query(
        default=None,
        description=(
            "Bloomberg field mnemonic for ALL FOUR underlying series "
            "(nominal + linker at each endpoint tenor).  Omit (None) to "
            "use the tool's bundled ``default_field_name`` convention "
            "(currently 'YLD_YTM_MID')."
        ),
    ),
):
    """Same payload semantics as the MCP wrapper; consumed by the
    frontend module's ``surfaces/BuildExtended.tsx``,
    ``surfaces/BuildCompact.tsx``, AND the Monitor widget per the
    rendering-density dual-view + monitor contract.

    The breakeven-curve-spread primitive intentionally does NOT expose
    the Phase-1 z-score overrides at its Input layer — its rolling-z-
    score conventions are sourced from the YAML at compute() time only
    (mirrors the sibling breakeven-butterfly primitive).
    """
    try:
        params = BreakevenCurveSpreadInput(
            nominal_curve_family=nominal_curve_family,
            linker_curve_family=linker_curve_family,
            short_tenor=short_tenor,
            long_tenor=long_tenor,
            lookback_days=lookback_days,
            field_name=field_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    try:
        bcs_config = load_tool_config(BREAKEVEN_CURVE_SPREAD_CONFIG_PATH)
        result = calculate_breakeven_curve_spread(
            engine=engine, params=params, config=bcs_config,
        )
    except Exception as exc:
        logger.exception(
            "detail/breakeven-curve-spread: tool failed for %s vs %s %s/%s",
            nominal_curve_family, linker_curve_family,
            short_tenor, long_tenor,
        )
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    _tool_result_or_raise(
        result,
        f"Breakeven curve spread for {nominal_curve_family} vs "
        f"{linker_curve_family} {short_tenor}/{long_tenor}",
    )
    return result


# ----------------------------------------------------------------------------
# /detail/forward-breakeven  — same-country forward bond-implied breakeven bridge
# ----------------------------------------------------------------------------
# Per ``docs_revamped/03_standards/methodology_exposure.md §5`` the
# forward_breakeven_simple primitive ships its OWN typed-detail endpoint.
# Year-weighted linear forward between two spot breakeven pillars on a
# single same-country nominal/linker pair (e.g. UST/USD_TIPS 5Y5Y,
# FR_OAT/EUR_FR_LINKER 5Y10Y).  The same payload feeds BOTH the extended
# and compact Build views and the Monitor tile (rendering_density.md §10).
# The rolling-z-score conventions are YAML-locked on this primitive — no
# input-layer overrides for window / min-periods / ddof (mirrors the
# sibling breakeven-curve-spread / breakeven-butterfly bridges).  Same-
# country invariant inherited transitively from the spot breakeven
# primitive's ``_enforce_same_country_invariant`` guard (fires BEFORE any
# market-data SELECT on either endpoint).
# ============================================================================
@router.get(
    "/detail/forward-breakeven",
    response_model=ForwardBreakevenSimpleOutput,
    summary="Forward Bond-Implied Breakeven Inflation Detail (standalone bridge)",
)
def forward_breakeven_detail(
    engine: Engine = Depends(get_engine),
    nominal_curve_family: str = Query(..., description="Nominal sovereign curve family — UST / UK_GILT / FR_OAT / CANADA_GOVT"),
    linker_curve_family: str = Query(..., description="Linker curve family — USD_TIPS / GBP_LINKER / EUR_FR_LINKER / CAD_RRB"),
    start_tenor: str = Query(..., description="Start tenor of the forward window (e.g. '5Y' for 5Y5Y)"),
    end_tenor: str = Query(..., description="End tenor of the forward window (e.g. '10Y' for 5Y5Y) — must be strictly longer than start_tenor"),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    field_name: Optional[str] = Query(
        default=None,
        description=(
            "Bloomberg field mnemonic for ALL FOUR underlying series "
            "(nominal + linker at each endpoint tenor).  Omit (None) to "
            "use the tool's bundled ``default_field_name`` convention "
            "(currently 'YLD_YTM_MID')."
        ),
    ),
):
    """Same payload semantics as the MCP wrapper; consumed by the
    frontend module's ``surfaces/BuildExtended.tsx``,
    ``surfaces/BuildCompact.tsx``, AND the Monitor widget per the
    rendering-density dual-view + monitor contract.

    The forward-breakeven primitive intentionally does NOT expose the
    Phase-1 z-score overrides at its Input layer — its rolling-z-score
    conventions are sourced from the YAML at compute() time only
    (mirrors the sibling breakeven-curve-spread / breakeven-butterfly
    primitives).
    """
    try:
        params = ForwardBreakevenSimpleInput(
            nominal_curve_family=nominal_curve_family,
            linker_curve_family=linker_curve_family,
            start_tenor=start_tenor,
            end_tenor=end_tenor,
            lookback_days=lookback_days,
            field_name=field_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    try:
        fbs_config = load_tool_config(FORWARD_BREAKEVEN_SIMPLE_CONFIG_PATH)
        result = calculate_forward_breakeven_simple(
            engine=engine, params=params, config=fbs_config,
        )
    except Exception as exc:
        logger.exception(
            "detail/forward-breakeven: tool failed for %s vs %s %s/%s",
            nominal_curve_family, linker_curve_family,
            start_tenor, end_tenor,
        )
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    _tool_result_or_raise(
        result,
        f"Forward breakeven inflation for {nominal_curve_family} vs "
        f"{linker_curve_family} {start_tenor}/{end_tenor}",
    )
    return result


# ----------------------------------------------------------------------------
# /detail/real-yield-butterfly  — same-country linker real-yield butterfly bridge
# ----------------------------------------------------------------------------
# Per ``docs_revamped/03_standards/methodology_exposure.md §5`` the
# real_yield_butterfly primitive ships its OWN typed-detail endpoint.
# Single-curve primitive — one linker ``curve_family`` + three strictly-
# ordered tenors; no nominal counterparty (distinct from breakeven-butterfly).
# The same payload feeds BOTH the extended and compact Build views and the
# Monitor tile (rendering_density.md §10).  The rolling-z-score conventions
# are YAML-locked on this primitive — no input-layer overrides for window /
# min-periods / ddof.
# ============================================================================
@router.get(
    "/detail/real-yield-butterfly",
    response_model=RealYieldButterflyOutput,
    summary="Linker Real-Yield Butterfly Detail (standalone bridge)",
)
def real_yield_butterfly_detail(
    engine: Engine = Depends(get_engine),
    curve_family: str = Query(..., description="Linker curve family — USD_TIPS / GBP_LINKER / EUR_FR_LINKER / CAD_RRB"),
    short_tenor: str = Query(..., description="Short wing tenor (e.g. '5Y' for 5s10s30s)"),
    belly_tenor: str = Query(..., description="Belly tenor (e.g. '10Y' for 5s10s30s)"),
    long_tenor: str = Query(..., description="Long wing tenor (e.g. '30Y' for 5s10s30s) — must satisfy short < belly < long"),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    field_name: Optional[str] = Query(
        default=None,
        description=(
            "Bloomberg field mnemonic for ALL THREE endpoint real-yield "
            "series.  Omit (None) to use the tool's bundled "
            "``default_field_name`` convention (currently 'YLD_YTM_MID')."
        ),
    ),
):
    """Same payload semantics as the MCP wrapper; consumed by the
    frontend module's ``surfaces/BuildExtended.tsx``,
    ``surfaces/BuildCompact.tsx``, AND the Monitor widget per the
    rendering-density dual-view + monitor contract.

    The real-yield-butterfly primitive intentionally does NOT expose the
    Phase-1 z-score overrides at its Input layer — its rolling-z-score
    conventions are sourced from the YAML at compute() time only.  This
    mirrors the sibling breakeven-butterfly bridge.
    """
    try:
        params = RealYieldButterflyInput(
            curve_family=curve_family,
            short_tenor=short_tenor,
            belly_tenor=belly_tenor,
            long_tenor=long_tenor,
            lookback_days=lookback_days,
            field_name=field_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    try:
        ryb_config = load_tool_config(REAL_YIELD_BUTTERFLY_CONFIG_PATH)
        result = calculate_real_yield_butterfly(
            engine=engine, params=params, config=ryb_config,
        )
    except Exception as exc:
        logger.exception(
            "detail/real-yield-butterfly: tool failed for %s %s/%s/%s",
            curve_family, short_tenor, belly_tenor, long_tenor,
        )
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    _tool_result_or_raise(
        result,
        f"Real-yield butterfly for {curve_family} "
        f"{short_tenor}/{belly_tenor}/{long_tenor}",
    )
    return result


# ----------------------------------------------------------------------------
# /detail/cross-market-zcis  — same-tenor cross-market ZCIS spread bridge
# ----------------------------------------------------------------------------
# Per ``docs_revamped/03_standards/methodology_exposure.md §5`` the
# cross_market_inflation_swap_spread primitive ships its OWN typed-detail
# endpoint.  Two-curve, single-tenor primitive — two ZCIS curve families
# (e.g. USD_ZCIS, EUR_ZCIS, GBP_ZCIS) at a shared pillar (e.g. 5Y).
# Schema layer rejects ``leg_a_curve_family == leg_b_curve_family`` (same-
# curve, two-tenor spreads belong to ``inflation_swap_curve_spread``).
# The same payload feeds BOTH the extended and compact Build views and the
# Monitor tile (rendering_density.md §10).  Rolling-z-score conventions
# are YAML-locked on this primitive — no input-layer overrides for window /
# min-periods / ddof; only ``lookback_days`` + ``field_name`` are exposed.
# ============================================================================
@router.get(
    "/detail/cross-market-zcis",
    response_model=CrossMarketInflationSwapSpreadOutput,
    summary="Cross-Market ZCIS Spread Detail (standalone bridge)",
)
def cross_market_zcis_detail(
    engine: Engine = Depends(get_engine),
    leg_a_curve_family: str = Query(..., description="Left (numerator) ZCIS curve family — USD_ZCIS / EUR_ZCIS / GBP_ZCIS"),
    leg_b_curve_family: str = Query(..., description="Right (denominator) ZCIS curve family.  Must differ from leg_a_curve_family"),
    tenor: str = Query(..., description="Single tenor pillar shared by both legs (e.g. '5Y', '10Y')"),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    field_name: Optional[str] = Query(
        default=None,
        description=(
            "Bloomberg field mnemonic threaded into BOTH endpoint ZCIS "
            "level series.  Omit (None) to use the tool's bundled "
            "``default_zcis_rate_field`` convention (currently 'PX_MID')."
        ),
    ),
):
    """Same payload semantics as the MCP wrapper; consumed by the frontend
    module's ``surfaces/BuildExtended.tsx``, ``surfaces/BuildCompact.tsx``,
    AND the Monitor widget per the rendering-density dual-view + monitor
    contract.

    The cross-market ZCIS spread primitive intentionally does NOT expose
    the z-score conventions at its Input layer — its rolling-z-score
    conventions are sourced from the YAML at compute() time only.  This
    mirrors the sibling breakeven-butterfly / real-yield-butterfly bridges.
    """
    try:
        params = CrossMarketInflationSwapSpreadInput(
            leg_a_curve_family=leg_a_curve_family,
            leg_b_curve_family=leg_b_curve_family,
            tenor=tenor,
            lookback_days=lookback_days,
            field_name=field_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    try:
        cmzcis_config = load_tool_config(CROSS_MARKET_INFLATION_SWAP_SPREAD_CONFIG_PATH)
        result = calculate_cross_market_inflation_swap_spread(
            engine=engine, params=params, config=cmzcis_config,
        )
    except Exception as exc:
        logger.exception(
            "detail/cross-market-zcis: tool failed for %s - %s %s",
            leg_a_curve_family, leg_b_curve_family, tenor,
        )
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    _tool_result_or_raise(
        result,
        f"Cross-market ZCIS spread for {leg_a_curve_family} - "
        f"{leg_b_curve_family} {tenor}",
    )
    return result


# ----------------------------------------------------------------------------
# /detail/zcis-butterfly  — same-curve ZCIS butterfly bridge
# ----------------------------------------------------------------------------
# Per ``docs_revamped/03_standards/methodology_exposure.md §5`` the
# inflation_swap_butterfly primitive ships its OWN typed-detail endpoint.
# Single-curve, three-tenor primitive — one ZCIS ``curve_family`` (e.g.
# USD_ZCIS, EUR_ZCIS, GBP_ZCIS) plus three strictly-ordered tenors; cross-
# curve butterflies are forbidden by the schema layer.  The same payload
# feeds BOTH the extended and compact Build views and the Monitor tile
# (rendering_density.md §10).  Rolling-z-score conventions are YAML-locked
# on this primitive (no input-layer overrides — mirrors the breakeven-
# butterfly / real-yield-butterfly siblings).
# ============================================================================
@router.get(
    "/detail/zcis-butterfly",
    response_model=InflationSwapButterflyOutput,
    summary="Same-Curve ZCIS Butterfly Detail (standalone bridge)",
)
def zcis_butterfly_detail(
    engine: Engine = Depends(get_engine),
    curve_family: str = Query(..., description="Inflation-swap curve family — USD_ZCIS / EUR_ZCIS / GBP_ZCIS"),
    short_tenor: str = Query(..., description="Short wing tenor (e.g. '2Y' for 2s5s10s, '5Y' for 5s10s30s)"),
    belly_tenor: str = Query(..., description="Belly tenor (e.g. '5Y' for 2s5s10s, '10Y' for 5s10s30s)"),
    long_tenor: str = Query(..., description="Long wing tenor (e.g. '10Y' for 2s5s10s, '30Y' for 5s10s30s) — must satisfy short < belly < long"),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    field_name: Optional[str] = Query(
        default=None,
        description=(
            "Bloomberg field mnemonic for ALL THREE endpoint ZCIS rate "
            "series.  Omit (None) to use the tool's bundled "
            "``default_zcis_rate_field`` convention (currently 'PX_MID')."
        ),
    ),
):
    """Same payload semantics as the MCP wrapper; consumed by the frontend
    module's ``surfaces/BuildExtended.tsx``, ``surfaces/BuildCompact.tsx``,
    AND the Monitor widget per the rendering-density dual-view + monitor
    contract.

    The inflation_swap_butterfly primitive intentionally does NOT expose the
    z-score conventions at its Input layer — its rolling-z-score
    conventions are sourced from the YAML at compute() time only.  Mirrors
    the sibling breakeven-butterfly / real-yield-butterfly bridges.
    """
    try:
        params = InflationSwapButterflyInput(
            curve_family=curve_family,
            short_tenor=short_tenor,
            belly_tenor=belly_tenor,
            long_tenor=long_tenor,
            lookback_days=lookback_days,
            field_name=field_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    try:
        zcisfly_config = load_tool_config(INFLATION_SWAP_BUTTERFLY_CONFIG_PATH)
        result = calculate_inflation_swap_butterfly(
            engine=engine, params=params, config=zcisfly_config,
        )
    except Exception as exc:
        logger.exception(
            "detail/zcis-butterfly: tool failed for %s %s/%s/%s",
            curve_family, short_tenor, belly_tenor, long_tenor,
        )
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    _tool_result_or_raise(
        result,
        f"ZCIS butterfly for {curve_family} "
        f"{short_tenor}/{belly_tenor}/{long_tenor}",
    )
    return result


# ----------------------------------------------------------------------------
# /detail/ois-butterfly  — same-curve OIS butterfly bridge
# ----------------------------------------------------------------------------
# Per ``docs_revamped/03_standards/methodology_exposure.md §5`` the
# calculate_ois_butterfly primitive ships its OWN typed-detail endpoint.
# Single-curve, three-tenor primitive — one OIS ``curve_family`` (closed enum
# sourced from rates_agent/playbooks/ois.yml: USD_SOFR_OIS / EUR_ESTR_OIS /
# GBP_SONIA_OIS / JPY_OIS / AUD_OIS / CAD_OIS) plus three distinct tenors;
# the schema layer rejects duplicate tenors at construction time.  The same
# payload feeds BOTH the extended and compact Build views and the Monitor
# tile (rendering_density.md §10).  Rolling-z-score conventions are YAML-
# locked on this primitive (mirrors the sibling sovereign / linker / ZCIS
# butterflies — no input-layer overrides for window / min-periods / ddof);
# only ``lookback_days`` + ``field_name`` are exposed at the API layer.
# ============================================================================
@router.get(
    "/detail/ois-butterfly",
    response_model=OISButterflyOutput,
    summary="Same-Curve OIS Butterfly Detail (standalone bridge)",
)
def ois_butterfly_detail(
    engine: Engine = Depends(get_engine),
    curve_family: str = Query(
        ...,
        description=(
            "OIS curve family — closed enum sourced from "
            "rates_agent/playbooks/ois.yml: USD_SOFR_OIS / EUR_ESTR_OIS / "
            "GBP_SONIA_OIS / JPY_OIS / AUD_OIS / CAD_OIS."
        ),
    ),
    short_tenor: str = Query(..., description="Short wing tenor (e.g. '2Y' for 2s5s10s)"),
    belly_tenor: str = Query(..., description="Belly tenor (e.g. '5Y' for 2s5s10s)"),
    long_tenor: str = Query(..., description="Long wing tenor (e.g. '10Y' for 2s5s10s) — must satisfy short < belly < long"),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    field_name: Optional[str] = Query(
        default=None,
        description=(
            "Bloomberg field mnemonic for ALL THREE endpoint OIS par-swap "
            "rate series.  Omit (None) to use the tool's bundled "
            "``default_swap_rate_field`` convention (currently 'PX_LAST' — "
            "the OIS Bloomberg mid-rate field, NOT the sovereign "
            "'YLD_YTM_MID' yield-to-maturity field)."
        ),
    ),
):
    """Same payload semantics as the MCP wrapper; consumed by the frontend
    module's ``surfaces/BuildExtended.tsx``, ``surfaces/BuildCompact.tsx``,
    AND the Monitor widget per the rendering-density dual-view + monitor
    contract.

    The OIS butterfly primitive intentionally does NOT expose the z-score
    conventions at its Input layer — its rolling-z-score conventions are
    sourced from the YAML at compute() time only.  Mirrors the sibling
    sovereign / linker / ZCIS butterfly bridges.
    """
    try:
        params = OISButterflyInput(
            curve_family=curve_family,
            short_tenor=short_tenor,
            belly_tenor=belly_tenor,
            long_tenor=long_tenor,
            lookback_days=lookback_days,
            field_name=field_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    try:
        oisfly_config = load_tool_config(OIS_BUTTERFLY_CONFIG_PATH)
        result = calculate_ois_butterfly(
            engine=engine, params=params, config=oisfly_config,
        )
    except Exception as exc:
        logger.exception(
            "detail/ois-butterfly: tool failed for %s %s/%s/%s",
            curve_family, short_tenor, belly_tenor, long_tenor,
        )
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    _tool_result_or_raise(
        result,
        f"OIS butterfly for {curve_family} "
        f"{short_tenor}/{belly_tenor}/{long_tenor}",
    )
    return result


# ----------------------------------------------------------------------------
# /detail/ois-curve-spread  — same-curve OIS tenor spread bridge
# ----------------------------------------------------------------------------
# Per ``docs_revamped/03_standards/methodology_exposure.md §5`` the
# calculate_ois_curve_spread primitive ships its OWN typed-detail endpoint.
# Single-curve, two-tenor primitive — one OIS ``curve_family`` (USD_SOFR_OIS
# / EUR_ESTR_OIS / GBP_SONIA_OIS / JPY_OIS / AUD_OIS / CAD_OIS) plus a
# (short_tenor, long_tenor) pair; the schema layer rejects identical tenors
# at construction time.  The same payload feeds BOTH the extended and compact
# Build views and the Monitor tile (rendering_density.md §10).  Rolling-
# z-score conventions are YAML-locked on this primitive (mirrors the sibling
# OIS butterfly bridge — no input-layer overrides for window / min-periods /
# ddof); only ``lookback_days`` + ``field_name`` are exposed at the API layer.
# ============================================================================
@router.get(
    "/detail/ois-curve-spread",
    response_model=OISCurveSpreadOutput,
    summary="Same-Curve OIS Tenor Spread Detail (standalone bridge)",
)
def ois_curve_spread_detail(
    engine: Engine = Depends(get_engine),
    curve_family: str = Query(
        ...,
        description=(
            "OIS curve family identifier.  Examples: 'USD_SOFR_OIS', "
            "'EUR_ESTR_OIS', 'GBP_SONIA_OIS', 'JPY_OIS', 'AUD_OIS', "
            "'CAD_OIS'."
        ),
    ),
    short_tenor: str = Query(
        ...,
        description=(
            "Short leg of the spread.  OIS curves have a dense short-end "
            "grid: '1W', '1M', '2M', '3M', '6M', '9M', '1Y', '2Y', '3Y'."
        ),
    ),
    long_tenor: str = Query(
        ...,
        description=(
            "Long leg of the spread.  Examples: '2Y', '5Y', '10Y', '20Y', "
            "'30Y'.  Must differ from short_tenor."
        ),
    ),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    field_name: Optional[str] = Query(
        default=None,
        description=(
            "Bloomberg field mnemonic for both endpoint OIS par-swap rate "
            "series.  Omit (None) to use the tool's bundled "
            "``default_swap_rate_field`` convention (currently 'PX_LAST' — "
            "the OIS Bloomberg mid-rate field, NOT the sovereign "
            "'YLD_YTM_MID' yield-to-maturity field)."
        ),
    ),
):
    """Same payload semantics as the MCP wrapper; consumed by the frontend
    module's ``surfaces/BuildExtended.tsx``, ``surfaces/BuildCompact.tsx``,
    AND the Monitor widget per the rendering-density dual-view + monitor
    contract.

    The OIS curve-spread primitive intentionally does NOT expose the z-score
    conventions at its Input layer — its rolling-z-score conventions are
    sourced from the YAML at compute() time only.  Mirrors the sibling OIS
    butterfly bridge.
    """
    try:
        params = OISCurveSpreadInput(
            curve_family=curve_family,
            short_tenor=short_tenor,
            long_tenor=long_tenor,
            lookback_days=lookback_days,
            field_name=field_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    try:
        cs_config = load_tool_config(OIS_CURVE_SPREAD_CONFIG_PATH)
        result = calculate_ois_curve_spread(
            engine=engine, params=params, config=cs_config,
        )
    except Exception as exc:
        logger.exception(
            "detail/ois-curve-spread: tool failed for %s %s/%s",
            curve_family, short_tenor, long_tenor,
        )
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    _tool_result_or_raise(
        result,
        f"OIS curve spread for {curve_family} {short_tenor}/{long_tenor}",
    )
    return result


# ----------------------------------------------------------------------------
# /detail/ois-cross-market-spread  — cross-market OIS spread bridge
# ----------------------------------------------------------------------------
# Per ``docs_revamped/03_standards/methodology_exposure.md §5`` the
# calculate_ois_cross_market_spread primitive ships its OWN typed-detail
# endpoint.  Two-curve, single-tenor primitive — two OIS ``curve_family``
# values (USD_SOFR_OIS / EUR_ESTR_OIS / GBP_SONIA_OIS / JPY_OIS / AUD_OIS /
# CAD_OIS) at a shared pillar (e.g. 2Y); the schema layer rejects identical
# curves at construction time (same-curve tenor spreads belong to
# ``/detail/ois-curve-spread``).  The same payload feeds BOTH the extended
# and compact Build views and the Monitor tile (rendering_density.md §10).
# Rolling-z-score conventions are YAML-locked on this primitive (mirrors the
# sibling OIS curve_spread / butterfly bridges — no input-layer overrides for
# window / min-periods / ddof); only ``lookback_days`` + ``field_name`` are
# exposed at the API layer.
# ============================================================================
@router.get(
    "/detail/ois-cross-market-spread",
    response_model=OISCrossMarketSpreadOutput,
    summary="Cross-Market OIS Spread Detail (standalone bridge)",
)
def ois_cross_market_spread_detail(
    engine: Engine = Depends(get_engine),
    curve_family_1: str = Query(
        ...,
        description=(
            "First (numerator) OIS curve family.  spread = "
            "curve_family_1 - curve_family_2.  Examples: 'USD_SOFR_OIS', "
            "'EUR_ESTR_OIS', 'GBP_SONIA_OIS', 'JPY_OIS', 'AUD_OIS', "
            "'CAD_OIS'."
        ),
    ),
    curve_family_2: str = Query(
        ...,
        description=(
            "Second (denominator) OIS curve family.  Must differ from "
            "curve_family_1."
        ),
    ),
    tenor: str = Query(
        ...,
        description=(
            "Tenor pillar shared by both legs (e.g. '1M', '3M', '6M', "
            "'1Y', '2Y', '5Y', '10Y')."
        ),
    ),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    field_name: Optional[str] = Query(
        default=None,
        description=(
            "Bloomberg field mnemonic threaded into BOTH endpoint OIS "
            "par-swap-rate series.  Omit (None) to use the tool's bundled "
            "``default_swap_rate_field`` convention (currently 'PX_LAST')."
        ),
    ),
):
    """Same payload semantics as the MCP wrapper; consumed by the frontend
    module's ``surfaces/BuildExtended.tsx``, ``surfaces/BuildCompact.tsx``,
    AND the Monitor widget per the rendering-density dual-view + monitor
    contract.

    The OIS cross-market spread primitive intentionally does NOT expose the
    z-score conventions at its Input layer — its rolling-z-score conventions
    are sourced from the YAML at compute() time only.  Mirrors the sibling
    OIS curve_spread / butterfly bridges.
    """
    try:
        params = OISCrossMarketSpreadInput(
            curve_family_1=curve_family_1,
            curve_family_2=curve_family_2,
            tenor=tenor,
            lookback_days=lookback_days,
            field_name=field_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    try:
        cms_config = load_tool_config(OIS_CROSS_MARKET_SPREAD_CONFIG_PATH)
        result = calculate_ois_cross_market_spread(
            engine=engine, params=params, config=cms_config,
        )
    except Exception as exc:
        logger.exception(
            "detail/ois-cross-market-spread: tool failed for %s - %s %s",
            curve_family_1, curve_family_2, tenor,
        )
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    _tool_result_or_raise(
        result,
        f"OIS cross-market spread for {curve_family_1} - "
        f"{curve_family_2} {tenor}",
    )
    return result


# ============================================================================
# /detail/ois-rate-level  — single-tenor OIS par-swap-rate snapshot bridge
# ----------------------------------------------------------------------------
# Per ``docs_revamped/03_standards/methodology_exposure.md §5`` the OIS
# rate-level primitive ships its OWN typed-detail endpoint.  Same payload
# feeds BOTH the extended Build view (mounted for single-tool queries) and
# the compact Build view (mounted as a node body inside multi-tool DAGs) +
# the Monitor tile per rendering_density.md §10.  Rolling-z-score conventions
# are YAML-locked on this primitive (no input-layer overrides — mirrors the
# sibling OIS curve_spread / butterfly bridges); only ``lookback_days`` +
# ``field_name`` are exposed at the API layer.  Risk-neutral implied policy
# path caveat is the desk-canonical methodology disclosure (SOFR / EFFR /
# SONIA / ESTR / TONA / AONIA / CORRA family is the OIS implied policy
# expectation).
# ============================================================================
@router.get(
    "/detail/ois-rate-level",
    response_model=OISRateLevelOutput,
    summary="OIS Rate Level Detail (standalone bridge)",
)
def ois_rate_level_detail(
    engine: Engine = Depends(get_engine),
    curve_family: str = Query(
        ...,
        description=(
            "OIS curve family identifier.  Examples: 'USD_SOFR_OIS', "
            "'EUR_ESTR_OIS', 'GBP_SONIA_OIS', 'JPY_OIS', 'AUD_OIS', "
            "'CAD_OIS'."
        ),
    ),
    tenor: str = Query(
        ...,
        description=(
            "Tenor point on the OIS curve.  OIS curves have a dense "
            "short-end grid: '1W', '1M', '2M', '3M', '6M', '9M', '1Y', "
            "'2Y', '3Y', '5Y', '10Y', '20Y', '30Y'."
        ),
    ),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    field_name: Optional[str] = Query(
        default=None,
        description=(
            "Bloomberg field mnemonic.  Omit (None) to use the tool's "
            "bundled ``default_swap_rate_field`` convention from "
            "rate_level/config.yaml (currently 'PX_LAST' — the OIS "
            "Bloomberg mid-rate field, NOT the sovereign 'YLD_YTM_MID' "
            "yield-to-maturity field)."
        ),
    ),
):
    """Same payload semantics as the MCP wrapper; consumed by the frontend
    module's ``surfaces/BuildExtended.tsx``, ``surfaces/BuildCompact.tsx``,
    AND the Monitor widget per the rendering-density dual-view + monitor
    contract.

    The OIS rate-level primitive intentionally does NOT expose the z-score
    conventions at its Input layer — its rolling-z-score conventions are
    sourced from the YAML at compute() time only.  Mirrors the sibling OIS
    curve_spread / butterfly bridges.
    """
    try:
        params = OISRateLevelInput(
            curve_family=curve_family,
            tenor=tenor,
            lookback_days=lookback_days,
            field_name=field_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    try:
        rl_config = load_tool_config(OIS_RATE_LEVEL_CONFIG_PATH)
        result = get_ois_rate_level(
            engine=engine, params=params, config=rl_config,
        )
    except Exception as exc:
        logger.exception(
            "detail/ois-rate-level: tool failed for %s %s",
            curve_family, tenor,
        )
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    _tool_result_or_raise(
        result,
        f"OIS rate level for {curve_family} {tenor}",
    )
    return result


# ============================================================================
# /detail/ois-forward-rate  — implied OIS forward-rate snapshot bridge
# ----------------------------------------------------------------------------
# Per ``docs_revamped/03_standards/methodology_exposure.md §5`` the OIS
# forward-rate primitive ships its OWN typed-detail endpoint.  Same payload
# feeds BOTH the extended Build view (mounted for single-tool queries) and
# the compact Build view (mounted as a node body inside multi-tool DAGs) +
# the Monitor tile per rendering_density.md §10.  Two equivalent input
# modes — tenor-pair (start_tenor + end_tenor) OR date-pair (start_date +
# end_date); supply exactly ONE.  Rolling-z-score conventions are
# YAML-locked on this primitive (no input-layer overrides — mirrors the
# OIS rate_level / curve_spread / butterfly siblings); only
# ``lookback_days`` + ``field_name`` are exposed at the API layer.  Sign
# convention surfaced on the methodology card: forward_rate_pct is the
# absolute implied forward rate; daily_change_bps POSITIVE = the forward
# repriced HIGHER (hawkish implied-policy-path stretch).  Risk-neutral
# implied policy path caveat (OIS forwards price the EXPECTED policy
# path, not realised central-bank decisions) is the desk-canonical
# methodology disclosure.
# ============================================================================
@router.get(
    "/detail/ois-forward-rate",
    response_model=OISForwardRateOutput,
    summary="OIS Forward Rate Detail (standalone bridge)",
)
def ois_forward_rate_detail(
    engine: Engine = Depends(get_engine),
    curve_family: str = Query(
        ...,
        description=(
            "OIS curve family identifier.  Examples: 'USD_SOFR_OIS', "
            "'EUR_ESTR_OIS', 'GBP_SONIA_OIS', 'JPY_OIS', 'AUD_OIS', "
            "'CAD_OIS'."
        ),
    ),
    start_tenor: Optional[str] = Query(
        default=None,
        description=(
            "Start tenor of the forward window.  For '1Y1Y' use '1Y'; "
            "for '5Y5Y' use '5Y'; for '2Y1Y' use '2Y'.  Must be present "
            "on the curve.  Mutually exclusive with start_date."
        ),
    ),
    end_tenor: Optional[str] = Query(
        default=None,
        description=(
            "End tenor of the forward window.  For '1Y1Y' use '2Y' "
            "(start=1Y + forward=1Y); for '5Y5Y' use '10Y'; for '2Y1Y' "
            "use '3Y'.  Must be present on the curve.  Mutually "
            "exclusive with end_date."
        ),
    ),
    start_date: Optional[str] = Query(
        default=None,
        description=(
            "Start date of the forward window (YYYY-MM-DD).  Mutually "
            "exclusive with start_tenor.  Must be on or after the "
            "curve's as-of date."
        ),
    ),
    end_date: Optional[str] = Query(
        default=None,
        description=(
            "End date of the forward window (YYYY-MM-DD).  Must be "
            "strictly after start_date.  Mutually exclusive with "
            "end_tenor."
        ),
    ),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    field_name: Optional[str] = Query(
        default=None,
        description=(
            "Bloomberg field mnemonic.  Omit (None) to use the tool's "
            "bundled ``default_swap_rate_field`` convention from "
            "forward_rate/config.yaml (currently 'PX_LAST' — the OIS "
            "Bloomberg mid-rate field, NOT the sovereign 'YLD_YTM_MID' "
            "yield-to-maturity field)."
        ),
    ),
):
    """Same payload semantics as the MCP wrapper; consumed by the frontend
    module's ``surfaces/BuildExtended.tsx``, ``surfaces/BuildCompact.tsx``,
    AND the Monitor widget per the rendering-density dual-view + monitor
    contract.

    Supply exactly ONE of (start_tenor + end_tenor) or (start_date +
    end_date) — the schema layer rejects partial / both modes at
    construction time.  The OIS forward-rate primitive intentionally
    does NOT expose the z-score conventions at its Input layer — its
    rolling-z-score conventions are sourced from the YAML at compute()
    time only.  Mirrors the sibling OIS rate_level / curve_spread /
    butterfly bridges.
    """
    try:
        params = OISForwardRateInput(
            curve_family=curve_family,
            start_tenor=start_tenor,
            end_tenor=end_tenor,
            start_date=start_date,
            end_date=end_date,
            lookback_days=lookback_days,
            field_name=field_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    try:
        fr_config = load_tool_config(OIS_FORWARD_RATE_CONFIG_PATH)
        result = calculate_ois_forward_rate(
            engine=engine, params=params, config=fr_config,
        )
    except Exception as exc:
        logger.exception(
            "detail/ois-forward-rate: tool failed for %s tenor=(%s,%s) date=(%s,%s)",
            curve_family, start_tenor, end_tenor, start_date, end_date,
        )
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    window_label = (
        f"{start_tenor}/{end_tenor}" if start_tenor and end_tenor
        else f"{start_date} to {end_date}"
    )
    _tool_result_or_raise(
        result,
        f"OIS forward rate for {curve_family} {window_label}",
    )
    return result


# ============================================================================
# /detail/inflation-swap-forward  — same-curve ZCIS forward rate bridge
# ----------------------------------------------------------------------------
# Forward inflation-swap rate between two pillars on the SAME ZCIS curve
# family (e.g. USD_ZCIS 5Y5Y, EUR_ZCIS 5Y5Y, GBP_ZCIS 2Y3Y).  Per
# ``docs_revamped/03_standards/methodology_exposure.md §5`` the ZCIS
# forward-rate primitive ships its OWN typed-detail endpoint.  Same payload
# feeds BOTH the extended Build view + the compact Build view + the Monitor
# tile per rendering_density.md §10.  Output is FORWARD INFLATION
# COMPENSATION — not a clean forward expected-inflation read; the wire-
# honesty caveat lives in ``current_metrics.methodology_label`` and is
# sourced from the YAML at runtime (NOT a hardcoded Python literal).
# Rolling-z-score conventions are YAML-locked on this primitive (no input-
# layer overrides — mirrors the OIS forward_rate / ZCIS rate_level /
# curve_spread siblings); only ``lookback_days`` + ``field_name`` are
# exposed at the API layer.  Same-curve invariant enforced by the input
# layer — cross-curve forward combinations are NOT in scope and belong to
# a separate primitive.
# ============================================================================
@router.get(
    "/detail/inflation-swap-forward",
    response_model=InflationSwapForwardOutput,
    summary="ZCIS Forward Rate Detail (standalone bridge)",
)
def inflation_swap_forward_detail(
    engine: Engine = Depends(get_engine),
    curve_family: str = Query(
        ...,
        description=(
            "Inflation-swap curve family identifier shared by BOTH legs "
            "(same-curve invariant).  Examples: 'USD_ZCIS' (US CPI-U), "
            "'EUR_ZCIS' (Eurozone HICPxT), 'GBP_ZCIS' (UK RPI).  See "
            "rates_agent/playbooks/inflation_swaps.yml for the ingested "
            "universe."
        ),
    ),
    start_tenor: str = Query(
        ...,
        description=(
            "Start tenor of the forward window.  For 5Y5Y use '5Y'; for "
            "5Y10Y use '5Y'; for 2Y3Y use '2Y'.  Must be a supported "
            "pillar on this ``curve_family`` (current ingested grid is "
            "1Y / 2Y / 3Y / 5Y / 10Y / 20Y / 30Y on each of USD_ZCIS / "
            "EUR_ZCIS / GBP_ZCIS).  Must map to a strictly smaller year "
            "fraction than ``end_tenor``."
        ),
    ),
    end_tenor: str = Query(
        ...,
        description=(
            "End tenor of the forward window.  For 5Y5Y use '10Y' "
            "(start=5Y + forward=5Y); for 5Y10Y use '15Y'; for 2Y3Y use "
            "'5Y'.  Must be a supported pillar on this ``curve_family`` "
            "and must map to a strictly larger year fraction than "
            "``start_tenor``."
        ),
    ),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    field_name: Optional[str] = Query(
        default=None,
        description=(
            "Bloomberg field mnemonic used for BOTH endpoint ZCIS rate "
            "series.  Omit (None) to use the tool's bundled "
            "``default_zcis_rate_field`` convention from "
            "inflation_swap_forward/config.yaml (currently 'PX_MID')."
        ),
    ),
):
    """Same payload semantics as the MCP wrapper; consumed by the frontend
    module's ``surfaces/BuildExtended.tsx``, ``surfaces/BuildCompact.tsx``,
    AND the Monitor widget per the rendering-density dual-view + monitor
    contract.

    The ZCIS forward-rate primitive intentionally does NOT expose the
    z-score conventions at its Input layer — they're sourced from the YAML
    at compute() time only.  Mirrors the OIS forward_rate + sibling ZCIS
    tools.
    """
    try:
        params = InflationSwapForwardInput(
            curve_family=curve_family,
            start_tenor=start_tenor,
            end_tenor=end_tenor,
            lookback_days=lookback_days,
            field_name=field_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    try:
        isf_config = load_tool_config(INFLATION_SWAP_FORWARD_CONFIG_PATH)
        result = calculate_inflation_swap_forward(
            engine=engine, params=params, config=isf_config,
        )
    except Exception as exc:
        logger.exception(
            "detail/inflation-swap-forward: tool failed for %s %s/%s",
            curve_family, start_tenor, end_tenor,
        )
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    _tool_result_or_raise(
        result,
        f"ZCIS forward rate for {curve_family} {start_tenor}/{end_tenor}",
    )
    return result


# ============================================================================
# /detail/inflation-swap-rate-level — single-pillar ZCIS rate snapshot bridge
# ----------------------------------------------------------------------------
# First level-shape inflation_swaps primitive under the standalone-bridge
# contract.  Per ``docs_revamped/03_standards/methodology_exposure.md §5`` the
# ZCIS rate-level primitive ships its OWN typed-detail endpoint.  Same payload
# feeds BOTH the extended Build view (mounted for single-tool queries) and
# the compact Build view (mounted as a node body inside multi-tool DAGs) +
# the Monitor tile per rendering_density.md §10.  Rolling-z-score conventions
# are YAML-locked on this primitive (no input-layer overrides — mirrors the
# OIS rate_level sibling); only ``lookback_days`` + ``field_name`` are exposed
# at the API layer.  Surfaces the LOAD-BEARING reference metadata
# (``inflation_index_family`` / ``index_lag`` / ``interpolation`` /
# ``underlying_index``) on the wire so the desk can interpret the level
# honestly — USD_ZCIS references CPI-U with 3M lag + Daily interpolation,
# EUR_ZCIS references HICPxT with 3M lag + Monthly interpolation, GBP_ZCIS
# references RPI with 2M lag + Monthly interpolation.  ``methodology_label``
# is threaded from config.yaml's ``methodology.what_it_does`` (NOT hardcoded).
# ============================================================================
@router.get(
    "/detail/inflation-swap-rate-level",
    response_model=InflationSwapRateLevelOutput,
    summary="ZCIS Rate Level Detail (standalone bridge)",
)
def inflation_swap_rate_level_detail(
    engine: Engine = Depends(get_engine),
    curve_family: str = Query(
        ...,
        description=(
            "Inflation-swap curve family identifier.  Examples: "
            "'USD_ZCIS' (US CPI-U), 'EUR_ZCIS' (Eurozone HICPxT), "
            "'GBP_ZCIS' (UK RPI).  See rates_agent/playbooks/"
            "inflation_swaps.yml for the ingested universe."
        ),
    ),
    tenor: str = Query(
        ...,
        description=(
            "Tenor point on the ZCIS curve.  Current ingested grid is "
            "'1Y', '2Y', '3Y', '5Y', '10Y', '20Y', '30Y' on each of "
            "USD_ZCIS / EUR_ZCIS / GBP_ZCIS."
        ),
    ),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    field_name: Optional[str] = Query(
        default=None,
        description=(
            "Bloomberg field mnemonic.  Omit (None) to use the tool's "
            "bundled ``default_zcis_rate_field`` convention from "
            "inflation_swap_rate_level/config.yaml (currently 'PX_MID' "
            "— the canonical mid quoted ZCIS rate Bloomberg publishes "
            "for inflation swaps)."
        ),
    ),
):
    """Same payload semantics as the MCP wrapper; consumed by the frontend
    module's ``surfaces/BuildExtended.tsx``, ``surfaces/BuildCompact.tsx``,
    AND the Monitor widget per the rendering-density dual-view + monitor
    contract.

    The ZCIS rate-level primitive intentionally does NOT expose the z-score
    conventions at its Input layer — they're sourced from the YAML at
    compute() time only.  Mirrors the OIS rate_level + sibling level tools.
    """
    try:
        params = InflationSwapRateLevelInput(
            curve_family=curve_family,
            tenor=tenor,
            lookback_days=lookback_days,
            field_name=field_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    try:
        isrl_config = load_tool_config(INFLATION_SWAP_RATE_LEVEL_CONFIG_PATH)
        result = calculate_inflation_swap_rate_level(
            engine=engine, params=params, config=isrl_config,
        )
    except Exception as exc:
        logger.exception(
            "detail/inflation-swap-rate-level: tool failed for %s %s",
            curve_family, tenor,
        )
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    _tool_result_or_raise(
        result,
        f"ZCIS rate level for {curve_family} {tenor}",
    )
    return result


# ============================================================================
# /detail/inflation-swap-curve-spread — same-curve ZCIS tenor-spread bridge
# ----------------------------------------------------------------------------
# Same-curve, two-tenor primitive (e.g. USD_ZCIS 5s10s, EUR_ZCIS 5s30s).  Per
# ``docs_revamped/03_standards/methodology_exposure.md §5`` ships its OWN
# typed-detail endpoint feeding BOTH the extended + compact Build views and
# the Monitor tile per rendering_density.md §10.  Rolling-z-score conventions
# are YAML-locked on this primitive — only ``lookback_days`` + ``field_name``
# are exposed at the API layer (mirrors the sibling breakeven-curve-spread +
# ZCIS rate_level tools).  Surfaces the load-bearing reference-metadata triple
# (``inflation_index_family`` / ``index_lag`` / ``interpolation`` /
# ``underlying_index``) on the wire so the desk can interpret the spread
# under the correct ZCIS convention without a second tool call.  Cross-curve
# combinations belong to ``/detail/cross-market-zcis``.
# ============================================================================
@router.get(
    "/detail/inflation-swap-curve-spread",
    response_model=InflationSwapCurveSpreadOutput,
    summary="ZCIS Curve Spread Detail (standalone bridge)",
)
def inflation_swap_curve_spread_detail(
    engine: Engine = Depends(get_engine),
    curve_family: str = Query(
        ...,
        description=(
            "Inflation-swap curve family identifier shared by BOTH legs "
            "(same-curve invariant).  Examples: 'USD_ZCIS' (US CPI-U), "
            "'EUR_ZCIS' (Eurozone HICPxT), 'GBP_ZCIS' (UK RPI)."
        ),
    ),
    short_tenor: str = Query(
        ...,
        description=(
            "Short tenor of the ZCIS curve spread (e.g. '2Y' for 2s10s, "
            "'5Y' for 5s30s).  Must be a supported pillar on this "
            "curve_family and strictly shorter than ``long_tenor``."
        ),
    ),
    long_tenor: str = Query(
        ...,
        description=(
            "Long tenor of the ZCIS curve spread (e.g. '10Y' for 2s10s, "
            "'30Y' for 5s30s).  Must be a supported pillar on this "
            "curve_family and strictly longer than ``short_tenor``."
        ),
    ),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    field_name: Optional[str] = Query(
        default=None,
        description=(
            "Bloomberg field mnemonic used for BOTH endpoint ZCIS rate "
            "series.  Omit (None) to use the tool's bundled "
            "``default_zcis_rate_field`` convention from "
            "inflation_swap_curve_spread/config.yaml (currently 'PX_MID')."
        ),
    ),
):
    """Same payload semantics as the MCP wrapper; consumed by the
    frontend module's ``surfaces/BuildExtended.tsx``,
    ``surfaces/BuildCompact.tsx``, AND the Monitor widget per the
    rendering-density dual-view + monitor contract.

    The inflation-swap-curve-spread primitive intentionally does NOT
    expose the z-score conventions at its Input layer — they're sourced
    from the YAML at compute() time only (mirrors the sibling
    breakeven-curve-spread + ZCIS rate_level tools).
    """
    try:
        params = InflationSwapCurveSpreadInput(
            curve_family=curve_family,
            short_tenor=short_tenor,
            long_tenor=long_tenor,
            lookback_days=lookback_days,
            field_name=field_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    try:
        iscs_config = load_tool_config(INFLATION_SWAP_CURVE_SPREAD_CONFIG_PATH)
        result = calculate_inflation_swap_curve_spread(
            engine=engine, params=params, config=iscs_config,
        )
    except Exception as exc:
        logger.exception(
            "detail/inflation-swap-curve-spread: tool failed for %s %s/%s",
            curve_family, short_tenor, long_tenor,
        )
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    _tool_result_or_raise(
        result,
        f"ZCIS curve spread for {curve_family} {short_tenor}/{long_tenor}",
    )
    return result


# ============================================================================
# /detail/swap-breakeven-basis  — same-currency ZCIS-vs-bond-breakeven bridge
# ----------------------------------------------------------------------------
# Same-currency, single-tenor object composing the inner ZCIS rate-level +
# bond-implied breakeven primitives at one pillar (e.g. USD_ZCIS 10Y minus
# UST/USD_TIPS 10Y bond-implied breakeven).  Per
# ``docs_revamped/03_standards/methodology_exposure.md §5`` ships its OWN
# typed-detail endpoint consumed by both Build views + the Monitor tile per
# rendering_density.md §10.  Output is a SPREAD object — the basis ships in
# BPS (not PERCENT) to mirror the sibling spread primitives.  Sign convention
# POSITIVE = ZCIS rich vs bond breakeven; wire-locked at ``zcis_minus_breakeven``
# (compute layer raises NotImplementedError on any other value).  NOT a clean
# liquidity-premium read — the LOAD-BEARING caveat (index-lag differences
# between ZCIS conventions and the linker's CPI accrual, linker on-the-run
# liquidity premium, structural ZCIS basis) flows verbatim via
# ``current_metrics.methodology_label`` (sourced from YAML at runtime, NOT
# a hardcoded Python literal) PLUS the per-leg index-family triple
# (``zcis_inflation_index_family`` / ``zcis_index_lag`` / ``zcis_interpolation``
# / ``zcis_underlying_index``) and the derived ``index_families_match`` /
# ``index_family_caveat`` pair.  Rolling-z-score conventions are YAML-locked
# on this primitive — only ``lookback_days`` + ``field_name`` are exposed at
# the API layer (mirrors the sibling ZCIS rate_level / curve_spread /
# forward / cross-market bridges).
# ============================================================================
@router.get(
    "/detail/swap-breakeven-basis",
    response_model=SwapBreakevenBasisSimpleOutput,
    summary="Swap-Breakeven Basis Detail (standalone bridge)",
)
def swap_breakeven_basis_detail(
    engine: Engine = Depends(get_engine),
    zcis_curve_family: str = Query(
        ...,
        description=(
            "Inflation-swap curve family for the ZCIS leg.  Examples: "
            "'USD_ZCIS' (US CPI-U), 'EUR_ZCIS' (Eurozone HICPxT), "
            "'GBP_ZCIS' (UK RPI).  See rates_agent/playbooks/"
            "inflation_swaps.yml for the ingested universe."
        ),
    ),
    nominal_curve_family: str = Query(
        ...,
        description=(
            "Nominal sovereign curve family feeding the breakeven leg "
            "(e.g. 'UST' for USD basis, 'FR_OAT' for EUR/FR basis, "
            "'UK_GILT' for GBP basis).  Must pair to the same currency "
            "as ``zcis_curve_family``; the same-country invariant on "
            "the breakeven leg is inherited transitively from the inner "
            "breakeven primitive."
        ),
    ),
    linker_curve_family: str = Query(
        ...,
        description=(
            "Sovereign linker curve family feeding the breakeven leg "
            "(e.g. 'USD_TIPS', 'EUR_FR_LINKER', 'GBP_LINKER').  Must "
            "differ from ``nominal_curve_family`` (the schema layer "
            "rejects identical legs).  Same-country invariant inherited "
            "from the inner breakeven primitive."
        ),
    ),
    tenor: str = Query(
        ...,
        description=(
            "Single tenor pillar shared by the ZCIS leg and both "
            "breakeven legs (e.g. '5Y', '10Y', '30Y').  Available "
            "tenors are country-specific intersections of the ZCIS grid "
            "(1Y / 2Y / 3Y / 5Y / 10Y / 20Y / 30Y) and the linker / "
            "nominal grids."
        ),
    ),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    field_name: Optional[str] = Query(
        default=None,
        description=(
            "Bloomberg field mnemonic threaded into BOTH inner calls "
            "(the ZCIS level call AND the breakeven call).  Omit "
            "(None) so each inner primitive's YAML default resolves "
            "(ZCIS: PX_MID; breakeven: YLD_YTM_MID).  Pass an explicit "
            "field name to override per query; the same value is "
            "threaded into both inner calls."
        ),
    ),
):
    """Same payload semantics as the MCP wrapper; consumed by the frontend
    module's ``surfaces/BuildExtended.tsx``, ``surfaces/BuildCompact.tsx``,
    AND the Monitor widget per the rendering-density dual-view + monitor
    contract.

    The swap-breakeven basis primitive intentionally does NOT expose the
    z-score conventions at its Input layer — they're sourced from the YAML
    at compute() time only.  Mirrors the sibling ZCIS rate_level /
    curve_spread / forward / cross-market tools.
    """
    try:
        params = SwapBreakevenBasisSimpleInput(
            zcis_curve_family=zcis_curve_family,
            nominal_curve_family=nominal_curve_family,
            linker_curve_family=linker_curve_family,
            tenor=tenor,
            lookback_days=lookback_days,
            field_name=field_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    try:
        sbb_config = load_tool_config(SWAP_BREAKEVEN_BASIS_SIMPLE_CONFIG_PATH)
        result = calculate_swap_breakeven_basis_simple(
            engine=engine, params=params, config=sbb_config,
        )
    except Exception as exc:
        logger.exception(
            "detail/swap-breakeven-basis: tool failed for %s - %s/%s %s",
            zcis_curve_family, nominal_curve_family, linker_curve_family, tenor,
        )
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    _tool_result_or_raise(
        result,
        f"Swap-breakeven basis for {zcis_curve_family} - "
        f"{nominal_curve_family}/{linker_curve_family} {tenor}",
    )
    return result


# ----------------------------------------------------------------------------
# /detail/zcis-scanner  — universe-wide ZCIS rate-extremes scanner bridge
# ----------------------------------------------------------------------------
# First SCANNER-shape primitive under the standalone-bridge contract.  Wire
# shape is a ranked LIST (top-N rows by |z| of the 252d-rolling ZCIS rate
# level z-score) rather than a single time series — the BuildCompact view
# renders this as a top-N table (NOT a sparkline) and the BuildExtended view
# renders the same payload as a universe scan + full ranked detail.  The
# rolling-z-score conventions are YAML-locked on this primitive (no input-
# layer overrides — mirrors the sibling linker / bond_futures scanners);
# ``curve_families`` / ``top_n`` / ``min_abs_z_score`` / ``as_of_date``
# remain exposed.
# ============================================================================
@router.get(
    "/detail/zcis-scanner",
    response_model=ScanInflationSwapsExtremesOutput,
    summary="ZCIS Universe Extremes Scan (standalone bridge)",
)
def zcis_scanner_detail(
    engine: Engine = Depends(get_engine),
    curve_families: Optional[str] = Query(
        default=None,
        description=(
            "Comma-separated list of ZCIS curve families to scan.  Omit "
            "(None) for the full universe (USD_ZCIS / EUR_ZCIS / "
            "GBP_ZCIS).  Pass a CSV to narrow (e.g. 'USD_ZCIS,EUR_ZCIS')."
            "  Non-ZCIS families are refused at schema-validation time."
        ),
    ),
    top_n: Optional[int] = Query(
        default=None,
        ge=1,
        le=50,
        description=(
            "Number of extreme stems to return.  Omit (None) to fall "
            "through to the YAML default (currently 5)."
        ),
    ),
    min_abs_z_score: Optional[float] = Query(
        default=None,
        ge=0.0,
        description=(
            "Minimum absolute z-score threshold for inclusion.  Omit "
            "(None) to fall through to the YAML default (currently 1.5)."
        ),
    ),
    as_of_date: Optional[str] = Query(
        default=None,
        description=(
            "ISO-format date (YYYY-MM-DD) anchoring the scan.  Omit "
            "(None) to anchor to the most-recent shared trading day in "
            "the DB across the universe."
        ),
    ),
):
    """Same payload semantics as the MCP wrapper; consumed by the frontend
    module's ``surfaces/BuildExtended.tsx`` (universe scan + ranked detail)
    AND ``surfaces/BuildCompact.tsx`` (top-N table) per the rendering-
    density dual-view contract + the Monitor widget per the standalone-
    bridge contract.

    The rolling-z-score conventions are YAML-locked on this primitive —
    only scope / threshold / anchor inputs are exposed at the API layer.
    """
    parsed_families: Optional[List[str]] = None
    if curve_families and curve_families.strip():
        parsed_families = [
            cf.strip() for cf in curve_families.split(",") if cf.strip()
        ]

    as_of_arg: Optional[date]
    if as_of_date and as_of_date.strip():
        try:
            as_of_arg = date.fromisoformat(as_of_date.strip())
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Invalid as_of_date {as_of_date!r}: must be ISO "
                    f"YYYY-MM-DD (e.g. '2026-04-08'). Detail: {exc}"
                ),
            )
    else:
        as_of_arg = None

    try:
        params = ScanInflationSwapsExtremesInput(
            curve_families=parsed_families,
            top_n=top_n,
            min_abs_z_score=min_abs_z_score,
            as_of_date=as_of_arg,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    try:
        scan_config = load_tool_config(
            SCAN_INFLATION_SWAPS_EXTREMES_CONFIG_PATH,
        )
        result = calculate_scan_inflation_swaps_extremes(
            engine=engine, params=params, config=scan_config,
        )
    except Exception as exc:
        logger.exception(
            "detail/zcis-scanner: tool failed for curve_families=%s",
            parsed_families,
        )
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    _tool_result_or_raise(
        result,
        f"ZCIS universe scan ({', '.join(parsed_families) if parsed_families else 'full universe'})",
    )
    return result


# ----------------------------------------------------------------------------
# /detail/linkers-scanner — universe-wide linker REAL-YIELD extremes bridge
# ----------------------------------------------------------------------------
# SCANNER-shape primitive under the standalone-bridge contract.  Wire shape
# is a ranked LIST (top-N rows by |z| of the 252d-rolling real-yield LEVEL
# z-score) rather than a single time series — the BuildCompact view renders
# this as a top-N table (NOT a sparkline) and the BuildExtended view
# renders the same payload as a universe scan + full ranked detail.  The
# rolling-z-score conventions are YAML-locked on this primitive (no input-
# layer overrides — mirrors the sibling ZCIS scanner);
# ``curve_families`` / ``top_n`` / ``min_abs_z_score`` / ``as_of_date``
# remain exposed.
# ============================================================================
@router.get(
    "/detail/linkers-scanner",
    response_model=ScanInflationLinkersExtremesOutput,
    summary="Linker Universe Extremes Scan (standalone bridge)",
)
def linkers_scanner_detail(
    engine: Engine = Depends(get_engine),
    curve_families: Optional[str] = Query(
        default=None,
        description=(
            "Comma-separated list of linker curve families to scan.  Omit "
            "(None) for the full universe (USD_TIPS / GBP_LINKER / "
            "EUR_FR_LINKER / CAD_RRB).  Pass a CSV to narrow (e.g. "
            "'USD_TIPS,GBP_LINKER').  Non-linker families are refused at "
            "schema-validation time."
        ),
    ),
    top_n: Optional[int] = Query(
        default=None,
        ge=1,
        le=50,
        description=(
            "Number of extreme stems to return.  Omit (None) to fall "
            "through to the YAML default (currently 5)."
        ),
    ),
    min_abs_z_score: Optional[float] = Query(
        default=None,
        ge=0.0,
        description=(
            "Minimum absolute z-score threshold for inclusion.  Omit "
            "(None) to fall through to the YAML default (currently 1.5)."
        ),
    ),
    as_of_date: Optional[str] = Query(
        default=None,
        description=(
            "ISO-format date (YYYY-MM-DD) anchoring the scan.  Omit "
            "(None) to anchor to the most-recent shared trading day in "
            "the DB across the universe."
        ),
    ),
):
    """Same payload semantics as the MCP wrapper; consumed by the frontend
    module's ``surfaces/BuildExtended.tsx`` (universe scan + ranked detail)
    AND ``surfaces/BuildCompact.tsx`` (top-N table) per the rendering-
    density dual-view contract + the Monitor widget per the standalone-
    bridge contract.

    The rolling-z-score conventions are YAML-locked on this primitive —
    only scope / threshold / anchor inputs are exposed at the API layer.
    """
    parsed_families: Optional[List[str]] = None
    if curve_families and curve_families.strip():
        parsed_families = [
            cf.strip() for cf in curve_families.split(",") if cf.strip()
        ]

    as_of_arg: Optional[date]
    if as_of_date and as_of_date.strip():
        try:
            as_of_arg = date.fromisoformat(as_of_date.strip())
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Invalid as_of_date {as_of_date!r}: must be ISO "
                    f"YYYY-MM-DD (e.g. '2026-04-08'). Detail: {exc}"
                ),
            )
    else:
        as_of_arg = None

    try:
        params = ScanInflationLinkersExtremesInput(
            curve_families=parsed_families,
            top_n=top_n,
            min_abs_z_score=min_abs_z_score,
            as_of_date=as_of_arg,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    try:
        scan_config = load_tool_config(
            SCAN_INFLATION_LINKERS_EXTREMES_CONFIG_PATH,
        )
        result = calculate_scan_inflation_linkers_extremes(
            engine=engine, params=params, config=scan_config,
        )
    except Exception as exc:
        logger.exception(
            "detail/linkers-scanner: tool failed for curve_families=%s",
            parsed_families,
        )
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    _tool_result_or_raise(
        result,
        f"Linker universe scan ({', '.join(parsed_families) if parsed_families else 'full universe'})",
    )
    return result


# ----------------------------------------------------------------------------
# /detail/bond-futures-scanner — universe-wide bond-futures extremes bridge
# ----------------------------------------------------------------------------
# SCANNER-shape primitive under the standalone-bridge contract.  Wire shape
# is a MULTI-METRIC ranked LIST (top-N rows per metric across price LEVEL,
# 1-day price CHANGE, volume LEVEL, open-interest LEVEL — each ranked by
# absolute 252d-rolling z-score) rather than a single time series.  The
# BuildCompact view renders this as a top-N table (NOT a sparkline) and the
# BuildExtended view renders the same payload as a universe scan + full
# multi-metric ranked detail.  The rolling-z-score conventions are YAML-
# locked on this primitive (mirrors the sibling ZCIS / linker scanners);
# ``curve_families`` / ``top_n`` / ``min_abs_z_score`` / ``as_of_date``
# remain exposed.
# ============================================================================
@router.get(
    "/detail/bond-futures-scanner",
    response_model=ScanBondFuturesExtremesOutput,
    summary="Bond Futures Universe Extremes Scan (standalone bridge)",
)
def bond_futures_scanner_detail(
    engine: Engine = Depends(get_engine),
    curve_families: Optional[str] = Query(
        default=None,
        description=(
            "Comma-separated list of bond-futures curve families to scan. "
            "Omit (None) for the full universe (UST_FUT / DE_FUT / UK_FUT / "
            "JP_FUT / FR_FUT / IT_FUT / ES_FUT / CA_FUT / AU_FUT).  Pass a "
            "CSV to narrow (e.g. 'UST_FUT,DE_FUT').  Policy-futures families "
            "(SOFR_FUT / SONIA_FUT / EUR_SHORT_RATE_FUT) are refused at "
            "schema-validation time."
        ),
    ),
    top_n: Optional[int] = Query(
        default=None,
        ge=1,
        le=50,
        description=(
            "Number of extreme stems to return PER METRIC.  Omit (None) to "
            "fall through to the YAML default (currently 5)."
        ),
    ),
    min_abs_z_score: Optional[float] = Query(
        default=None,
        ge=0.0,
        description=(
            "Minimum absolute z-score threshold for inclusion.  Omit (None) "
            "to fall through to the YAML default (currently 1.5)."
        ),
    ),
    as_of_date: Optional[str] = Query(
        default=None,
        description=(
            "ISO-format date (YYYY-MM-DD) anchoring the scan.  Omit (None) "
            "to anchor to the most-recent shared trading day in the DB "
            "across the universe."
        ),
    ),
):
    """Same payload semantics as the MCP wrapper; consumed by the frontend
    module's ``surfaces/BuildExtended.tsx`` (universe scan + ranked detail)
    AND ``surfaces/BuildCompact.tsx`` (top-N table) per the rendering-
    density dual-view contract + the Monitor widget per the standalone-
    bridge contract.

    The rolling-z-score conventions are YAML-locked on this primitive —
    only scope / threshold / anchor inputs are exposed at the API layer.
    """
    parsed_families: Optional[List[str]] = None
    if curve_families and curve_families.strip():
        parsed_families = [
            cf.strip() for cf in curve_families.split(",") if cf.strip()
        ]

    as_of_arg: Optional[date]
    if as_of_date and as_of_date.strip():
        try:
            as_of_arg = date.fromisoformat(as_of_date.strip())
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Invalid as_of_date {as_of_date!r}: must be ISO "
                    f"YYYY-MM-DD (e.g. '2026-04-08'). Detail: {exc}"
                ),
            )
    else:
        as_of_arg = None

    try:
        params = ScanBondFuturesExtremesInput(
            curve_families=parsed_families,
            top_n=top_n,
            min_abs_z_score=min_abs_z_score,
            as_of_date=as_of_arg,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    try:
        scan_config = load_tool_config(
            SCAN_BOND_FUTURES_EXTREMES_CONFIG_PATH,
        )
        result = calculate_scan_bond_futures_extremes(
            engine=engine, params=params, config=scan_config,
        )
    except Exception as exc:
        logger.exception(
            "detail/bond-futures-scanner: tool failed for curve_families=%s",
            parsed_families,
        )
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    _tool_result_or_raise(
        result,
        f"Bond futures universe scan ({', '.join(parsed_families) if parsed_families else 'full universe'})",
    )
    return result


# ============================================================================
# /detail/policy-futures-scanner — universe-wide STIR extremes bridge
# ----------------------------------------------------------------------------
# SCANNER-shape primitive under the standalone-bridge contract.  Wire shape
# is a MULTI-METRIC ranked LIST (top-N rows per metric across implied-rate
# LEVEL, 1-day implied-rate CHANGE in bps, volume LEVEL, open-interest
# LEVEL — each ranked by absolute 252d-rolling z-score) rather than a single
# time series.  The BuildCompact view renders this as a top-N table (NOT a
# sparkline) and the BuildExtended view renders the same payload as a
# universe scan + full multi-metric ranked detail.  Rolling-z-score
# conventions are YAML-locked on this primitive (mirrors the sibling
# bond_futures / ZCIS / linker scanners); ``curve_families`` / ``top_n`` /
# ``min_abs_z_score`` / ``metrics`` / ``as_of_date`` remain exposed.
# ============================================================================
@router.get(
    "/detail/policy-futures-scanner",
    response_model=ScanPolicyFuturesExtremesOutput,
    summary="Policy Futures (STIR) Universe Extremes Scan (standalone bridge)",
)
def policy_futures_scanner_detail(
    engine: Engine = Depends(get_engine),
    curve_families: Optional[str] = Query(
        default=None,
        description=(
            "Comma-separated list of policy-futures curve families to scan. "
            "Omit (None) for the full universe (SOFR_FUT / EUR_SHORT_RATE_FUT "
            "/ SONIA_FUT).  Pass a CSV to narrow (e.g. "
            "'SOFR_FUT,EUR_SHORT_RATE_FUT').  Bond-futures families (UST_FUT "
            "/ DE_FUT / ...) are refused at schema-validation time."
        ),
    ),
    top_n: Optional[int] = Query(
        default=None,
        ge=1,
        le=50,
        description=(
            "Number of extreme stems to return PER METRIC.  Omit (None) to "
            "fall through to the YAML default (currently 5)."
        ),
    ),
    min_abs_z_score: Optional[float] = Query(
        default=None,
        ge=0.0,
        description=(
            "Minimum absolute z-score threshold for inclusion.  Omit (None) "
            "to fall through to the YAML default (currently 1.5)."
        ),
    ),
    metrics: Optional[str] = Query(
        default=None,
        description=(
            "Comma-separated subset of the four metrics to rank (e.g. "
            "'implied_rate_level,volume_level').  Omit (None) to rank the "
            "full ScanMetric set per the YAML default."
        ),
    ),
    as_of_date: Optional[str] = Query(
        default=None,
        description=(
            "ISO-format date (YYYY-MM-DD) anchoring the scan.  Omit (None) "
            "to anchor to the most-recent shared trading day in the DB "
            "across the universe."
        ),
    ),
):
    """Same payload semantics as the MCP wrapper; consumed by the frontend
    module's ``surfaces/BuildExtended.tsx`` (universe scan + ranked detail)
    AND ``surfaces/BuildCompact.tsx`` (top-N table) per the rendering-
    density dual-view contract + the Monitor widget per the standalone-
    bridge contract.

    The rolling-z-score conventions are YAML-locked on this primitive —
    only scope / threshold / metric-subset / anchor inputs are exposed at
    the API layer.
    """
    parsed_families: Optional[List[str]] = None
    if curve_families and curve_families.strip():
        parsed_families = [
            cf.strip() for cf in curve_families.split(",") if cf.strip()
        ]

    parsed_metrics: Optional[List[str]] = None
    if metrics and metrics.strip():
        parsed_metrics = [
            m.strip() for m in metrics.split(",") if m.strip()
        ]

    as_of_arg: Optional[date]
    if as_of_date and as_of_date.strip():
        try:
            as_of_arg = date.fromisoformat(as_of_date.strip())
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Invalid as_of_date {as_of_date!r}: must be ISO "
                    f"YYYY-MM-DD (e.g. '2026-04-08'). Detail: {exc}"
                ),
            )
    else:
        as_of_arg = None

    try:
        params = ScanPolicyFuturesExtremesInput(
            curve_families=parsed_families,
            top_n=top_n,
            min_abs_z_score=min_abs_z_score,
            metrics=parsed_metrics,  # type: ignore[arg-type]
            as_of_date=as_of_arg,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    try:
        scan_config = load_tool_config(
            SCAN_POLICY_FUTURES_EXTREMES_CONFIG_PATH,
        )
        result = calculate_scan_policy_futures_extremes(
            engine=engine, params=params, config=scan_config,
        )
    except Exception as exc:
        logger.exception(
            "detail/policy-futures-scanner: tool failed for curve_families=%s",
            parsed_families,
        )
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    _tool_result_or_raise(
        result,
        f"Policy futures universe scan ({', '.join(parsed_families) if parsed_families else 'full universe'})",
    )
    return result


@router.get("/detail/spread", response_model=CurveSpreadOutput, summary="Curve Spread Detail (workspace)")
def spread_detail(
    engine: Engine = Depends(get_engine),
    curve_family: str = Query(..., description="e.g. 'UST'"),
    short_tenor: str = Query(default="2Y"),
    long_tenor: str = Query(default="10Y"),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    field_name: str = Query(default="YLD_YTM_MID"),
):
    try:
        params = CurveSpreadInput(
            curve_family=curve_family, short_tenor=short_tenor,
            long_tenor=long_tenor, lookback_days=lookback_days,
            field_name=field_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    # Pass the curve_spread tool's bundled config explicitly so the
    # config dependency is observable at the endpoint.  load_tool_config
    # is process-cached, so this is a free lookup after the first call.
    try:
        cs_config = load_tool_config(CURVE_SPREAD_CONFIG_PATH)
        result = calculate_curve_spread(
            engine=engine, params=params, config=cs_config,
        )
    except Exception as exc:
        logger.exception("detail/spread: tool failed for %s %s/%s", curve_family, short_tenor, long_tenor)
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    _tool_result_or_raise(result, f"Spread for {curve_family} {short_tenor}/{long_tenor}")
    return result


@router.get("/detail/cross-market", response_model=CrossMarketSpreadOutput, summary="Cross-Market Spread Detail (workspace)")
def cross_market_detail(
    engine: Engine = Depends(get_engine),
    curve_family_1: str = Query(..., description="e.g. 'IT_BTP'"),
    curve_family_2: str = Query(..., description="e.g. 'DE_BUND'"),
    tenor: str = Query(default="10Y"),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    field_name: Optional[str] = Query(
        default=None,
        description=(
            "Bloomberg field mnemonic.  Omit to use the tool's bundled "
            "``default_field_name`` convention from "
            "cross_market_spread/config.yaml (currently 'YLD_YTM_MID').  "
            "Pass explicitly to override per request.  Same wrapper-"
            "shadowing fix applied as the other migrated detail endpoints "
            "(commit b2605ee)."
        ),
    ),
):
    """``field_name`` defaults to None at the query layer so the tool's
    compute() can resolve it against the YAML's ``default_field_name``
    convention.  A previous version hardcoded
    ``Query(default="YLD_YTM_MID")`` which silently shadowed the YAML
    default — same fix as the other migrated tools."""
    try:
        params = CrossMarketSpreadInput(
            curve_family_1=curve_family_1, curve_family_2=curve_family_2,
            tenor=tenor, lookback_days=lookback_days, field_name=field_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    # Pass cross_market_spread's bundled config explicitly so the
    # config dependency is observable at the endpoint.  load_tool_config
    # is process-cached, so this is a free lookup after the first call.
    try:
        cm_config = load_tool_config(CROSS_MARKET_CONFIG_PATH)
        result = calculate_cross_market_spread(
            engine=engine, params=params, config=cm_config,
        )
    except Exception as exc:
        logger.exception("detail/cross-market: tool failed for %s-%s %s",
                         curve_family_1, curve_family_2, tenor)
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    _tool_result_or_raise(result, f"Cross-market {curve_family_1}-{curve_family_2} {tenor}")
    return result


@router.get("/detail/butterfly", response_model=ButterflyOutput, summary="Butterfly Detail (workspace)")
def butterfly_detail(
    engine: Engine = Depends(get_engine),
    curve_family: str = Query(..., description="e.g. 'UST'"),
    short_tenor: str = Query(default="2Y"),
    belly_tenor: str = Query(default="5Y"),
    long_tenor: str = Query(default="10Y"),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    field_name: Optional[str] = Query(
        default=None,
        description=(
            "Bloomberg field mnemonic.  Omit to use the tool's bundled "
            "``default_field_name`` convention from butterfly/config.yaml "
            "(currently 'YLD_YTM_MID').  Pass explicitly to override per "
            "request.  Same wrapper-shadowing fix applied as the "
            "yield_levels and curve_move_classifier endpoints (commit "
            "b2605ee)."
        ),
    ),
):
    """``field_name`` defaults to None at the query layer so the tool's
    compute() can resolve it against the YAML's ``default_field_name``
    convention.  A previous version hardcoded
    ``Query(default="YLD_YTM_MID")`` which silently shadowed the YAML
    default — same fix as yield_levels and curve_move_classifier."""
    try:
        params = ButterflyInput(
            curve_family=curve_family, short_tenor=short_tenor,
            belly_tenor=belly_tenor, long_tenor=long_tenor,
            lookback_days=lookback_days, field_name=field_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    # Pass butterfly's bundled config explicitly so the config
    # dependency is observable at the endpoint.  load_tool_config is
    # process-cached, so this is a free lookup after the first call.
    try:
        bf_config = load_tool_config(BUTTERFLY_CONFIG_PATH)
        result = calculate_butterfly(
            engine=engine, params=params, config=bf_config,
        )
    except Exception as exc:
        logger.exception("detail/butterfly: tool failed for %s %s/%s/%s",
                         curve_family, short_tenor, belly_tenor, long_tenor)
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    _tool_result_or_raise(result, f"Butterfly for {curve_family} {short_tenor}/{belly_tenor}/{long_tenor}")
    return result


@router.get("/detail/regime", summary="Curve Regime Detail (workspace)")
def regime_detail(
    engine: Engine = Depends(get_engine),
    curve_family: str = Query(..., description="e.g. 'UST'"),
    front_tenor: str = Query(default="2Y"),
    back_tenor: str = Query(default="10Y"),
    lookback_period: str = Query(default="1d", description="'1d', '5d', '22d', or '63d'"),
    field_name: Optional[str] = Query(
        default=None,
        description=(
            "Bloomberg field mnemonic.  Omit to use the tool's "
            "bundled ``default_field_name`` convention from "
            "config.yaml (currently 'YLD_YTM_MID').  Pass explicitly "
            "to override per request."
        ),
    ),
):
    """User-facing endpoint name retains "regime" because that's how
    PMs and the frontend's existing typescript types reference this
    surface (``RegimeView`` / ``RegimeOutput``).  Internally we now
    call the renamed ``classify_curve_move_compute`` and translate
    the new ``classification`` / ``description`` field names back to
    the legacy ``regime_tag`` / ``regime_description`` wire format
    so the frontend doesn't need to change.

    ``field_name`` defaults to None at the query layer (FastAPI maps
    a missing query param to None) so the tool's compute() can
    resolve it against the YAML's ``default_field_name`` convention.
    A previous version hardcoded ``Query(default="YLD_YTM_MID")``,
    which silently shadowed the YAML default — fixed alongside the
    matching MCP-wrapper fix.

    The rename rationale (single-observation classifier, not a
    persistence-state regime detector) is documented in
    docs/architecture/tool_architecture.md."""
    try:
        params = CurveMoveInput(
            curve_family=curve_family, front_tenor=front_tenor,
            back_tenor=back_tenor, lookback_period=lookback_period,
            field_name=field_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    try:
        cm_config = load_tool_config(CURVE_MOVE_CONFIG_PATH)
        result = classify_curve_move_compute(
            engine=engine, params=params, config=cm_config,
        )
    except Exception as exc:
        logger.exception("detail/regime: tool failed for %s %s/%s %s",
                         curve_family, front_tenor, back_tenor, lookback_period)
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    _tool_result_or_raise(result, f"Regime for {curve_family} {front_tenor}/{back_tenor} ({lookback_period})")

    # Translate to the legacy wire format the frontend expects:
    # ``classification`` → ``regime_tag``, ``description`` → ``regime_description``.
    metrics = result.get("current_metrics", {})
    translated_metrics = {**metrics}
    if "classification" in translated_metrics:
        translated_metrics["regime_tag"] = translated_metrics.pop("classification")
    if "description" in translated_metrics:
        translated_metrics["regime_description"] = translated_metrics.pop("description")

    return {"current_metrics": translated_metrics}


@router.get(
    "/detail/zscore-custom",
    response_model=ZscoreCustomOutput,
    summary="Custom-window rolling z-score (workspace)",
)
def zscore_custom_detail(
    engine: Engine = Depends(get_engine),
    curve_family: str = Query(..., description="e.g. 'UST'"),
    tenor: str = Query(..., description="e.g. '10Y'"),
    z_score_window_days: int = Query(
        ...,
        ge=20,
        le=1260,
        description=(
            "Rolling window length in trading days for the z-score.  "
            "This is the tool's central methodological choice — set per "
            "request.  Typical desk values: 60 (tactical), 126 "
            "(quarterly), 252 (annual), 504 (two-year)."
        ),
    ),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    field_name: Optional[str] = Query(
        default=None,
        description=(
            "Bloomberg field mnemonic.  Omit to use the tool's bundled "
            "``default_field_name`` convention from "
            "zscore_custom/config.yaml (currently 'YLD_YTM_MID').  Pass "
            "explicitly to override per request.  Same wrapper-shadowing "
            "fix applied as the yield_levels and curve_move_classifier "
            "endpoints (commit b2605ee)."
        ),
    ),
):
    """``field_name`` defaults to None at the query layer so the tool's
    compute() can resolve it against the YAML's ``default_field_name``
    convention.  All other methodology knobs (`min_periods`, `ddof`,
    `buffer_multiplier`, `ffill_limit_days`, rounding) are YAML-locked
    and not exposed at the route — see A13 in
    docs/architecture/tool_architecture.md."""
    try:
        params = ZscoreCustomInput(
            curve_family=curve_family,
            tenor=tenor,
            z_score_window_days=z_score_window_days,
            lookback_days=lookback_days,
            field_name=field_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    # Pass zscore_custom's bundled config explicitly so the dependency
    # is observable at the endpoint.  load_tool_config is process-cached,
    # so this is a free lookup after the first call.
    try:
        zc_config = load_tool_config(ZSCORE_CUSTOM_CONFIG_PATH)
        result = calculate_zscore_custom(
            engine=engine, params=params, config=zc_config,
        )
    except Exception as exc:
        logger.exception(
            "detail/zscore-custom: tool failed for %s %s window=%d",
            curve_family, tenor, z_score_window_days,
        )
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    _tool_result_or_raise(
        result,
        f"Custom z-score for {curve_family} {tenor} (window={z_score_window_days}d)",
    )
    return result


@router.get(
    "/detail/beta-adjusted-spread",
    response_model=BetaAdjustedSpreadOutput,
    summary="Beta-Adjusted Spread Detail (workspace)",
)
def beta_adjusted_spread_detail(
    engine: Engine = Depends(get_engine),
    target_curve_family: str = Query(..., description="e.g. 'IT_BTP'"),
    target_tenor: str = Query(..., description="e.g. '10Y'"),
    regressor_curve_family: str = Query(..., description="e.g. 'DE_BUND'"),
    regressor_tenor: str = Query(..., description="e.g. '10Y'"),
    regression_window_days: int = Query(
        ...,
        ge=10,
        le=2520,
        description=(
            "Trailing-window length in trading-day rows for each "
            "rolling fit.  Central methodological choice — set per "
            "request.  Typical desk values: 60 (tactical), 252 "
            "(annual), 504 (two-year)."
        ),
    ),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    field_name: Optional[str] = Query(
        default=None,
        description=(
            "Bloomberg field mnemonic — applies to BOTH legs.  Omit to "
            "use the tool's bundled ``default_field_name`` from "
            "beta_adjusted_spread/config.yaml (currently 'YLD_YTM_MID').  "
            "Same wrapper-shadowing fix applied as the zscore_custom "
            "and yield_levels endpoints (commit b2605ee)."
        ),
    ),
):
    """``field_name`` defaults to None at the query layer so the tool's
    compute() can resolve it against the YAML's ``default_field_name``
    convention.  All other methodology knobs (`min_periods`, `solver`,
    `condition`-threshold, residual-z-score window, rounding) are
    YAML-locked and not exposed at the route — see A13 in
    docs/architecture/tool_architecture.md.

    The small-window guard (regression_window_days <
    regression_min_periods) returns a controlled error envelope whose
    phrase shape maps to HTTP 422 via the helper's user_input_phrases
    list — same client-error class as zscore_custom's small-window
    guard."""
    try:
        params = BetaAdjustedSpreadInput(
            target_curve_family=target_curve_family,
            target_tenor=target_tenor,
            regressor_curve_family=regressor_curve_family,
            regressor_tenor=regressor_tenor,
            regression_window_days=regression_window_days,
            lookback_days=lookback_days,
            field_name=field_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    try:
        bas_config = load_tool_config(BETA_ADJUSTED_SPREAD_CONFIG_PATH)
        result = calculate_beta_adjusted_spread(
            engine=engine, params=params, config=bas_config,
        )
    except Exception as exc:
        logger.exception(
            "detail/beta-adjusted-spread: tool failed for %s_%s on "
            "%s_%s window=%d",
            target_curve_family, target_tenor,
            regressor_curve_family, regressor_tenor,
            regression_window_days,
        )
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    _tool_result_or_raise(
        result,
        f"Beta-adjusted {target_curve_family}-{regressor_curve_family} "
        f"{target_tenor}/{regressor_tenor} (window={regression_window_days}d)",
    )
    return result


@router.get(
    "/detail/pca-yield-curve",
    response_model=PcaYieldCurveOutput,
    summary="PCA on Yield Curve (workspace)",
)
def pca_yield_curve_detail(
    engine: Engine = Depends(get_engine),
    curve_family: str = Query(..., description="Sovereign curve, e.g. 'UST'"),
    tenors: Optional[List[str]] = Query(
        default=None,
        description=(
            "Subset of tenor labels.  Repeat the param: "
            "``?tenors=1Y&tenors=2Y&tenors=10Y``.  Omit to use all "
            "playbook-configured tenors of the curve_family.  When "
            "supplied explicitly, the fit uses exactly those tenors "
            "or returns an error."
        ),
    ),
    lookback_days: int = Query(default=1825, ge=400, le=7300),
    n_components: int = Query(default=3, ge=1, le=8),
    change_frequency: Literal["daily", "weekly"] = Query(default="daily"),
    field_name: Optional[str] = Query(
        default=None,
        description=(
            "Bloomberg field mnemonic.  Omit to use the tool's "
            "bundled ``default_field_name`` from "
            "pca_yield_curve/config.yaml (currently 'YLD_YTM_MID').  "
            "Same wrapper-shadowing fix applied as the rest of the "
            "rates roster (commit b2605ee)."
        ),
    ),
):
    """``field_name`` defaults to None at the query layer so the tool's
    compute() can resolve it against the YAML's ``default_field_name``
    convention.  All methodology knobs (``min_observations_for_pca``,
    ``sign_anchor``, ``degenerate_variance_share_threshold``,
    ``ffill_limit_days``, all rounding decimals) are YAML-locked
    and not exposed at the route — see A13 in
    docs/architecture/tool_architecture.md.

    The ``lookback_days`` lower bound is a conservative calendar-day
    floor, not a 1:1 mirror of ``min_observations_for_pca`` — the
    actual fit still depends on how many non-NaN trading-day changes
    remain after differencing.

    The cross-layer min_observations guard returns a controlled error
    envelope whose phrase shape maps to HTTP 422 via the helper's
    user_input_phrases list."""
    try:
        params = PcaYieldCurveInput(
            curve_family=curve_family,
            tenors=tenors,
            lookback_days=lookback_days,
            n_components=n_components,
            change_frequency=change_frequency,
            field_name=field_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    try:
        pca_config = load_tool_config(PCA_YIELD_CURVE_CONFIG_PATH)
        result = calculate_pca_yield_curve(
            engine=engine, params=params, config=pca_config,
        )
    except Exception as exc:
        logger.exception(
            "detail/pca-yield-curve: tool failed for %s n_components=%d",
            curve_family, n_components,
        )
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    _tool_result_or_raise(
        result,
        f"PCA for {curve_family} (n_components={n_components}, "
        f"{change_frequency}, lookback={lookback_days}d)",
    )
    return result


# ----------------------------------------------------------------------------
# /detail/policy-futures-price  — policy_futures strip-position price level
# ----------------------------------------------------------------------------
# Per ``docs_revamped/03_standards/methodology_exposure.md §5`` the
# ``policy_futures_get_futures_price_level_tool`` primitive ships its OWN
# typed-detail endpoint.  Keyed by ``(curve_family, strip_position)`` per
# ADR 0013 (strip-position-keyed monitors).  Conventions are YAML-locked
# in V1; only the structural keys plus ``lookback_days`` / ``as_of_date``
# / ``field_name`` are exposed (mirrors the MCP wrapper's input surface).
@router.get(
    "/detail/policy-futures-price",
    response_model=FuturesPriceLevelOutput,
    summary="Policy Futures Strip-Position Price Level Detail (standalone bridge)",
)
def policy_futures_price_detail(
    engine: Engine = Depends(get_engine),
    curve_family: str = Query(
        ...,
        description=(
            "Policy-futures curve family — 'SOFR_FUT' (US RFR), "
            "'EUR_SHORT_RATE_FUT' (Euribor IBOR), 'SONIA_FUT' (UK RFR)."
        ),
    ),
    strip_position: int = Query(
        ...,
        ge=1,
        le=12,
        description=(
            "1-based strip position. 1 = front contract; whites = 1-4, "
            "reds = 5-8 in the V1 universe."
        ),
    ),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    as_of_date: Optional[str] = Query(
        default=None,
        description=(
            "ISO-format date (YYYY-MM-DD) anchoring the snapshot.  Omit "
            "to anchor at the universe's last observed trade_date for "
            "the requested strip (post-fetch data-max anchor).  A date "
            "BEYOND the universe's last observed trade_date returns the "
            "documented controlled-error envelope rather than silently "
            "re-labelling an unbounded read."
        ),
    ),
    field_name: Optional[str] = Query(
        default=None,
        description=(
            "Bloomberg observation field mnemonic.  Omit (None) to use "
            "the tool's bundled ``default_price_field`` convention from "
            "futures_price_level/config.yaml (currently 'PX_LAST').  "
            "Per config.yaml:default_price_field."
        ),
    ),
):
    """Same payload + sentinel semantics as the MCP wrapper.  Consumed by
    ``surfaces/BuildExtended.tsx`` AND ``surfaces/BuildCompact.tsx`` per
    the rendering-density dual-view contract, plus the Monitor tile.
    None-sentinels on ``field_name`` / ``as_of_date`` fall through to the
    YAML default / data-max anchor via compute() — same shadowing fix
    pattern as sovereign get_yield_levels / linker real_yield_level.
    """
    parsed_as_of: Optional[date] = None
    if as_of_date and as_of_date.strip():
        try:
            parsed_as_of = date.fromisoformat(as_of_date.strip())
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid as_of_date {as_of_date!r}: {exc}",
            )

    try:
        params = FuturesPriceLevelInput(
            curve_family=curve_family,
            strip_position=strip_position,
            lookback_days=lookback_days,
            as_of_date=parsed_as_of,
            field_name=field_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    try:
        pf_config = load_tool_config(POLICY_FUTURES_PRICE_LEVEL_CONFIG_PATH)
        result = calculate_futures_price_level(
            engine=engine, params=params, config=pf_config,
        )
    except Exception as exc:
        logger.exception(
            "detail/policy-futures-price: tool failed for %s strip=%d",
            curve_family, strip_position,
        )
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    _tool_result_or_raise(
        result,
        f"Policy futures price level for {curve_family} strip={strip_position}",
    )
    return result


# ----------------------------------------------------------------------------
# /detail/policy-futures-butterfly  — policy_futures same-curve simple butterfly
# ----------------------------------------------------------------------------
# Per ``docs_revamped/03_standards/methodology_exposure.md §5`` the
# ``policy_futures_get_futures_butterfly_simple_tool`` primitive ships its OWN
# typed-detail endpoint.  Keyed by ``(curve_family, strip_position_wing_short,
# strip_position_body, strip_position_wing_long)`` per ADR 0013 — strip-
# position-keyed monitors.  Conventions are YAML-locked in V1; only the
# structural strip-position keys plus ``lookback_days`` / ``as_of_date`` /
# ``field_name`` are exposed (mirrors the MCP wrapper's input surface).
# Butterfly value lives on the implied-rate axis in PERCENT POINTS on the
# wire (the policy-futures sub-domain convention); the frontend display
# layer multiplies by 100 to render bps for the desk-recognised headline.
@router.get(
    "/detail/policy-futures-butterfly",
    response_model=FuturesButterflySimpleOutput,
    summary="Policy Futures Same-Curve Simple Butterfly Detail (standalone bridge)",
)
def policy_futures_butterfly_detail(
    engine: Engine = Depends(get_engine),
    curve_family: str = Query(
        ...,
        description=(
            "Policy-futures curve family — 'SOFR_FUT' (US RFR), "
            "'EUR_SHORT_RATE_FUT' (Euribor IBOR), 'SONIA_FUT' (UK RFR)."
        ),
    ),
    strip_position_wing_short: int = Query(
        ...,
        ge=1,
        le=12,
        description=(
            "1-based strip position of the SHORT wing (fronter wing). "
            "Must satisfy strip_position_wing_short < strip_position_body "
            "(enforced by Pydantic validator)."
        ),
    ),
    strip_position_body: int = Query(
        ...,
        ge=1,
        le=12,
        description=(
            "1-based strip position of the BODY (belly).  Must satisfy "
            "strip_position_wing_short < strip_position_body < "
            "strip_position_wing_long (enforced by Pydantic validator)."
        ),
    ),
    strip_position_wing_long: int = Query(
        ...,
        ge=1,
        le=12,
        description=(
            "1-based strip position of the LONG wing (backer wing).  Must "
            "satisfy strip_position_body < strip_position_wing_long "
            "(enforced by Pydantic validator)."
        ),
    ),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    as_of_date: Optional[str] = Query(
        default=None,
        description=(
            "ISO-format date (YYYY-MM-DD) anchoring the snapshot.  Omit "
            "to anchor at the universe's last observed ``trade_date`` "
            "for the requested legs (post-fetch data-max anchor on the "
            "intersection of all three legs).  A date BEYOND the "
            "universe's last observed ``trade_date`` for ANY leg "
            "returns the documented controlled-error envelope."
        ),
    ),
    field_name: Optional[str] = Query(
        default=None,
        description=(
            "Bloomberg observation field mnemonic.  Omit (None) to use "
            "the tool's bundled ``default_price_field`` convention from "
            "futures_butterfly_simple/config.yaml (currently 'PX_LAST')."
        ),
    ),
):
    """Same payload + sentinel semantics as the MCP wrapper.  Consumed by
    ``surfaces/BuildExtended.tsx`` AND ``surfaces/BuildCompact.tsx`` per
    the rendering-density dual-view contract, plus the Monitor tile.
    None-sentinels on ``field_name`` / ``as_of_date`` fall through to the
    YAML default / data-max anchor via compute() — same shadowing fix
    pattern as policy_futures_price_detail / sovereign get_yield_levels /
    linker real_yield_level.
    """
    parsed_as_of: Optional[date] = None
    if as_of_date and as_of_date.strip():
        try:
            parsed_as_of = date.fromisoformat(as_of_date.strip())
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid as_of_date {as_of_date!r}: {exc}",
            )

    try:
        params = FuturesButterflySimpleInput(
            curve_family=curve_family,
            strip_position_wing_short=strip_position_wing_short,
            strip_position_body=strip_position_body,
            strip_position_wing_long=strip_position_wing_long,
            lookback_days=lookback_days,
            as_of_date=parsed_as_of,
            field_name=field_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    try:
        pf_config = load_tool_config(POLICY_FUTURES_BUTTERFLY_SIMPLE_CONFIG_PATH)
        result = calculate_futures_butterfly_simple(
            engine=engine, params=params, config=pf_config,
        )
    except Exception as exc:
        logger.exception(
            "detail/policy-futures-butterfly: tool failed for %s "
            "%d-%d-%d",
            curve_family,
            strip_position_wing_short,
            strip_position_body,
            strip_position_wing_long,
        )
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    _tool_result_or_raise(
        result,
        f"Policy futures butterfly for {curve_family} "
        f"{strip_position_wing_short}-{strip_position_body}-"
        f"{strip_position_wing_long}",
    )
    return result


# ----------------------------------------------------------------------------
# /detail/policy-futures-calendar  — policy_futures same-curve calendar spread
# ----------------------------------------------------------------------------
# Per ``docs_revamped/03_standards/methodology_exposure.md §5`` the
# ``policy_futures_get_futures_calendar_spread_tool`` primitive ships its OWN
# typed-detail endpoint.  Keyed by ``(curve_family, strip_position_short,
# strip_position_long)`` per ADR 0013 — strip-position-keyed monitors.
# Conventions are YAML-locked in V1; only the structural strip-position keys
# plus ``lookback_days`` / ``as_of_date`` / ``field_name`` are exposed
# (mirrors the MCP wrapper's input surface).  Wire spread lives on the
# implied-rate axis in PERCENT POINTS in the FRONT − BACK convention (the
# policy-futures sub-domain convention); the frontend display layer flips
# the sign and multiplies by 100 to render in bps under the desk-canonical
# BACK − FRONT display convention.
@router.get(
    "/detail/policy-futures-calendar",
    response_model=FuturesCalendarSpreadOutput,
    summary="Policy Futures Same-Curve Calendar Spread Detail (standalone bridge)",
)
def policy_futures_calendar_detail(
    engine: Engine = Depends(get_engine),
    curve_family: str = Query(
        ...,
        description=(
            "Policy-futures curve family — 'SOFR_FUT' (US RFR), "
            "'EUR_SHORT_RATE_FUT' (Euribor IBOR), 'SONIA_FUT' (UK RFR)."
        ),
    ),
    strip_position_short: int = Query(
        ...,
        ge=1,
        le=12,
        description=(
            "1-based strip position of the SHORT (fronter) leg.  Must "
            "satisfy strip_position_short < strip_position_long "
            "(enforced by Pydantic validator)."
        ),
    ),
    strip_position_long: int = Query(
        ...,
        ge=1,
        le=12,
        description=(
            "1-based strip position of the LONG (backer) leg.  Must "
            "satisfy strip_position_short < strip_position_long "
            "(enforced by Pydantic validator)."
        ),
    ),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    as_of_date: Optional[str] = Query(
        default=None,
        description=(
            "ISO-format date (YYYY-MM-DD) anchoring the snapshot.  Omit "
            "to anchor at the universe's last observed ``trade_date`` "
            "for the requested legs (post-fetch data-max anchor on the "
            "intersection of both legs).  A date BEYOND the universe's "
            "last observed ``trade_date`` for EITHER leg returns the "
            "documented controlled-error envelope."
        ),
    ),
    field_name: Optional[str] = Query(
        default=None,
        description=(
            "Bloomberg observation field mnemonic.  Omit (None) to use "
            "the tool's bundled ``default_price_field`` convention from "
            "futures_calendar_spread/config.yaml (currently 'PX_LAST')."
        ),
    ),
):
    """Same payload + sentinel semantics as the MCP wrapper.  Consumed by
    ``surfaces/BuildExtended.tsx`` AND ``surfaces/BuildCompact.tsx`` per
    the rendering-density dual-view contract, plus the Monitor tile.
    None-sentinels on ``field_name`` / ``as_of_date`` fall through to the
    YAML default / data-max anchor via compute() — same shadowing fix
    pattern as policy_futures_butterfly_detail.
    """
    parsed_as_of: Optional[date] = None
    if as_of_date and as_of_date.strip():
        try:
            parsed_as_of = date.fromisoformat(as_of_date.strip())
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid as_of_date {as_of_date!r}: {exc}",
            )

    try:
        params = FuturesCalendarSpreadInput(
            curve_family=curve_family,
            strip_position_short=strip_position_short,
            strip_position_long=strip_position_long,
            lookback_days=lookback_days,
            as_of_date=parsed_as_of,
            field_name=field_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    try:
        pf_config = load_tool_config(POLICY_FUTURES_CALENDAR_SPREAD_CONFIG_PATH)
        result = calculate_futures_calendar_spread(
            engine=engine, params=params, config=pf_config,
        )
    except Exception as exc:
        logger.exception(
            "detail/policy-futures-calendar: tool failed for %s "
            "%d-%d",
            curve_family,
            strip_position_short,
            strip_position_long,
        )
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    _tool_result_or_raise(
        result,
        f"Policy futures calendar spread for {curve_family} "
        f"{strip_position_short}-{strip_position_long}",
    )
    return result


# ----------------------------------------------------------------------------
# /detail/policy-futures-cross-market — policy_futures cross-market spread
# ----------------------------------------------------------------------------
# Per ``docs_revamped/03_standards/methodology_exposure.md §5`` the
# ``policy_futures_get_futures_cross_market_spread_tool`` primitive ships its
# OWN typed-detail endpoint.  Matched-strip implied-rate differential between
# TWO different ``curve_family`` values at ONE strip position (e.g.
# SOFR_FUT vs SONIA_FUT strip_position 1 → SFR1 − SFI1, SOFR_FUT vs
# EUR_SHORT_RATE_FUT strip_position 4 → SFR4 − ER4) per ADR 0013.
# Conventions are YAML-locked in V1; only the structural pair-leg + strip-
# position keys plus ``lookback_days`` / ``as_of_date`` / ``field_name``
# are exposed (mirrors the MCP wrapper's input surface).  Wire spread lives
# on the implied-rate axis in PERCENT POINTS in the A − B convention
# (orientation-honest — swapping the inputs flips the sign by construction);
# the frontend display layer multiplies by 100 to render in bps but does
# NOT flip the sign — A − B is the desk-canonical cross-CB divergence
# direction.  The schema layer enforces ``curve_family_a !=
# curve_family_b`` so a self-spread (mathematically zero) cannot be
# dispatched.
@router.get(
    "/detail/policy-futures-cross-market",
    response_model=FuturesCrossMarketSpreadOutput,
    summary="Policy Futures Matched-Strip Cross-Market Spread Detail (standalone bridge)",
)
def policy_futures_cross_market_detail(
    engine: Engine = Depends(get_engine),
    curve_family_a: str = Query(
        ...,
        description=(
            "First (numerator / 'A') policy-futures curve family.  V1 "
            "values: 'SOFR_FUT' (US Fed SOFR strip, RFR regime), "
            "'EUR_SHORT_RATE_FUT' (ECB Euribor strip, IBOR regime), "
            "'SONIA_FUT' (BOE SONIA strip, RFR regime).  Spread is "
            "computed as ``rate_A − rate_B`` with A and B echoed back "
            "on the methodology card."
        ),
    ),
    curve_family_b: str = Query(
        ...,
        description=(
            "Second (denominator / 'B') policy-futures curve family.  "
            "Must differ from ``curve_family_a`` (enforced by Pydantic "
            "validator — a self-spread is mathematically zero and not "
            "a real desk object)."
        ),
    ),
    strip_position: int = Query(
        ...,
        ge=1,
        le=12,
        description=(
            "1-based strip position on BOTH legs (matched-strip read).  "
            "1 = front contract on each market; higher numbers = "
            "quarterly forwards down each strip."
        ),
    ),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    as_of_date: Optional[str] = Query(
        default=None,
        description=(
            "ISO-format date (YYYY-MM-DD) anchoring the snapshot.  Omit "
            "to anchor at the universe's last observed ``trade_date`` "
            "for the requested legs (post-fetch data-max anchor on the "
            "intersection of both legs).  A date BEYOND the universe's "
            "last observed ``trade_date`` for EITHER leg returns the "
            "documented controlled-error envelope."
        ),
    ),
    field_name: Optional[str] = Query(
        default=None,
        description=(
            "Bloomberg observation field mnemonic.  Omit (None) to use "
            "the tool's bundled ``default_price_field`` convention from "
            "futures_cross_market_spread/config.yaml (currently 'PX_LAST')."
        ),
    ),
):
    """Same payload + sentinel semantics as the MCP wrapper.  Consumed by
    ``surfaces/BuildExtended.tsx`` AND ``surfaces/BuildCompact.tsx`` per
    the rendering-density dual-view contract, plus the Monitor tile.
    None-sentinels on ``field_name`` / ``as_of_date`` fall through to the
    YAML default / data-max anchor via compute() — same shadowing fix
    pattern as policy_futures_calendar_detail.
    """
    parsed_as_of: Optional[date] = None
    if as_of_date and as_of_date.strip():
        try:
            parsed_as_of = date.fromisoformat(as_of_date.strip())
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid as_of_date {as_of_date!r}: {exc}",
            )

    try:
        params = FuturesCrossMarketSpreadInput(
            curve_family_a=curve_family_a,
            curve_family_b=curve_family_b,
            strip_position=strip_position,
            lookback_days=lookback_days,
            as_of_date=parsed_as_of,
            field_name=field_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    try:
        pf_config = load_tool_config(POLICY_FUTURES_CROSS_MARKET_SPREAD_CONFIG_PATH)
        result = calculate_futures_cross_market_spread(
            engine=engine, params=params, config=pf_config,
        )
    except Exception as exc:
        logger.exception(
            "detail/policy-futures-cross-market: tool failed for %s vs %s "
            "strip %d",
            curve_family_a,
            curve_family_b,
            strip_position,
        )
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    _tool_result_or_raise(
        result,
        f"Policy futures cross-market spread for {curve_family_a} vs "
        f"{curve_family_b} strip {strip_position}",
    )
    return result


# ----------------------------------------------------------------------------
# /detail/policy-futures-pack-average — policy_futures same-curve pack average
# ----------------------------------------------------------------------------
# Per ``docs_revamped/03_standards/methodology_exposure.md §5`` the
# ``policy_futures_get_futures_pack_average_simple_tool`` primitive ships its
# OWN typed-detail endpoint.  Pack-average implied rate (arithmetic mean
# across 4 consecutive quarterly STIR contracts) on a SINGLE policy-futures
# ``curve_family``: whites = strip positions 1-4 (e.g. SFR1..SFR4 for
# SOFR_FUT), reds = strip positions 5-8 (e.g. SFI5..SFI8 for SONIA_FUT).
# Pack composition (which strip positions each pack covers) is YAML-locked
# and NOT user-overridable; only the structural ``curve_family`` + ``pack``
# keys plus ``lookback_days`` / ``as_of_date`` / ``field_name`` are exposed
# (mirrors the MCP wrapper's input surface).  Wire shape: pack-average
# implied rate in PERCENT (per-strip rates derived via the per-leg
# inverse-pricing flag — SFR / ER / SFI quote 100-minus-rate).
# ``curve_family='EUR_SHORT_RATE_FUT'`` is admitted at the schema layer but
# returns a clean controlled-error envelope from compute() per ADR 0013 V1
# scope (IBOR regime, missing ``delivery_month_type`` playbook metadata).
@router.get(
    "/detail/policy-futures-pack-average",
    response_model=FuturesPackAverageSimpleOutput,
    summary="Policy Futures Same-Curve Pack Average Detail (standalone bridge)",
)
def policy_futures_pack_average_detail(
    engine: Engine = Depends(get_engine),
    curve_family: str = Query(
        ...,
        description=(
            "Policy-futures curve family — 'SOFR_FUT' (US RFR, V1-"
            "executable), 'SONIA_FUT' (UK RFR, V1-executable), "
            "'EUR_SHORT_RATE_FUT' (Euribor IBOR — admitted at the "
            "schema layer but returns a clean controlled-error "
            "envelope from compute() per ADR 0013 V1 scope; "
            "missing ``delivery_month_type`` playbook metadata)."
        ),
    ),
    pack: str = Query(
        ...,
        description=(
            "Pack identifier — 'whites' (strip positions 1-4, e.g. "
            "SFR1..SFR4 for SOFR_FUT) or 'reds' (strip positions "
            "5-8, e.g. SFI5..SFI8 for SONIA_FUT).  Pack composition "
            "is a YAML-locked desk convention; greens / blues are "
            "PR11 planned-extension territory."
        ),
    ),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    as_of_date: Optional[str] = Query(
        default=None,
        description=(
            "ISO-format date (YYYY-MM-DD) anchoring the snapshot.  "
            "Omit to anchor at the universe's last observed "
            "``trade_date`` for the requested legs (post-fetch "
            "data-max anchor on the intersection of the four legs).  "
            "A date BEYOND the universe's last observed "
            "``trade_date`` for ANY leg returns the documented "
            "controlled-error envelope."
        ),
    ),
    field_name: Optional[str] = Query(
        default=None,
        description=(
            "Bloomberg observation field mnemonic.  Omit (None) to "
            "use the tool's bundled ``default_price_field`` "
            "convention from "
            "futures_pack_average_simple/config.yaml (currently "
            "'PX_LAST')."
        ),
    ),
):
    """Same payload + sentinel semantics as the MCP wrapper.  Consumed by
    ``surfaces/BuildExtended.tsx`` AND ``surfaces/BuildCompact.tsx`` per
    the rendering-density dual-view contract, plus the Monitor tile.
    None-sentinels on ``field_name`` / ``as_of_date`` fall through to the
    YAML default / data-max anchor via compute() — same shadowing fix
    pattern as policy_futures_cross_market_detail.
    """
    parsed_as_of: Optional[date] = None
    if as_of_date and as_of_date.strip():
        try:
            parsed_as_of = date.fromisoformat(as_of_date.strip())
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid as_of_date {as_of_date!r}: {exc}",
            )

    try:
        params = FuturesPackAverageSimpleInput(
            curve_family=curve_family,
            pack=pack,
            lookback_days=lookback_days,
            as_of_date=parsed_as_of,
            field_name=field_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    try:
        pf_config = load_tool_config(POLICY_FUTURES_PACK_AVERAGE_SIMPLE_CONFIG_PATH)
        result = calculate_futures_pack_average_simple(
            engine=engine, params=params, config=pf_config,
        )
    except Exception as exc:
        logger.exception(
            "detail/policy-futures-pack-average: tool failed for %s %s",
            curve_family,
            pack,
        )
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    _tool_result_or_raise(
        result,
        f"Policy futures pack average for {curve_family} {pack}",
    )
    return result


# ----------------------------------------------------------------------------
# /detail/bond-futures-price  — bond_futures rolling-generic price level
# ----------------------------------------------------------------------------
# Per ``docs_revamped/03_standards/methodology_exposure.md §5`` the
# ``get_futures_price_level_tool`` primitive in the ``bond_futures`` sub-
# package ships its OWN typed-detail endpoint.  Keyed by
# ``(curve_family, contract_code)`` per TD#11 — the rolling-generic stem
# is the canonical disambiguator (TY1 vs UXY1 are both UST_FUT 10Y; US1
# vs WN1 both UST_FUT 30Y).  Conventions are YAML-locked in V1; only
# the structural keys plus ``lookback_days`` / ``field_name`` are
# exposed.  The bond_futures ``FuturesPriceLevelInput`` does NOT accept
# ``as_of_date`` (unlike the policy_futures sibling) — the backend
# anchors at the universe's last observed trade_date.
@router.get(
    "/detail/bond-futures-price",
    response_model=BondFuturesPriceLevelOutput,
    summary="Bond Futures Rolling-Generic Price Level Detail (standalone bridge)",
)
def bond_futures_price_detail(
    engine: Engine = Depends(get_engine),
    curve_family: str = Query(
        ...,
        description=(
            "Bond-futures curve family — 'UST_FUT' (TY1 / UXY1 / US1 / "
            "WN1 / TU1 / FV1), 'DE_FUT' (RX1 / UB1 / DU1 / OE1), "
            "'UK_FUT' (G1), 'JP_FUT' (JB1), 'FR_FUT' (OAT1), 'IT_FUT' "
            "(IK1 / BTS1), 'ES_FUT' (KOA1), 'CA_FUT' (CN1), 'AU_FUT' "
            "(YM1 / XM1).  Do NOT pass policy-futures curves "
            "(SOFR_FUT / EUR_SHORT_RATE_FUT / SONIA_FUT) — those route "
            "to the policy_futures agent's /detail/policy-futures-price."
        ),
    ),
    contract_code: str = Query(
        ...,
        description=(
            "Rolling-generic stem from the bond_futures playbook universe "
            "(TD#11 disambiguator).  Examples: 'TY1', 'UXY1', 'US1', "
            "'WN1', 'TU1', 'FV1', 'RX1', 'UB1', 'DU1', 'OE1', 'G1', "
            "'JB1', 'OAT1', 'IK1', 'BTS1', 'KOA1', 'CN1', 'YM1', 'XM1'.  "
            "Required because (curve_family, tenor) alone is ambiguous "
            "for several universes."
        ),
    ),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    field_name: Optional[str] = Query(
        default=None,
        description=(
            "Bloomberg observation field mnemonic.  Omit (None) to use "
            "the tool's bundled ``default_price_field`` convention from "
            "bond_futures/futures_price_level/config.yaml (currently "
            "'PX_LAST').  Per config.yaml:default_price_field."
        ),
    ),
):
    """Same payload + sentinel semantics as the MCP wrapper.  Consumed by
    ``surfaces/BuildExtended.tsx`` AND ``surfaces/BuildCompact.tsx`` per
    the rendering-density dual-view contract, plus the Monitor tile.
    None-sentinel on ``field_name`` falls through to the YAML default
    via compute() — same shadowing fix pattern as the policy_futures
    cousin and sovereign get_yield_levels.
    """
    try:
        params = BondFuturesPriceLevelInput(
            curve_family=curve_family,
            contract_code=contract_code,
            lookback_days=lookback_days,
            field_name=field_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    try:
        bf_config = load_tool_config(BOND_FUTURES_PRICE_LEVEL_CONFIG_PATH)
        result = calculate_bond_futures_price_level(
            engine=engine, params=params, config=bf_config,
        )
    except Exception as exc:
        logger.exception(
            "detail/bond-futures-price: tool failed for %s contract=%s",
            curve_family, contract_code,
        )
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    _tool_result_or_raise(
        result,
        f"Bond futures price level for {curve_family} {contract_code}",
    )
    return result


# ----------------------------------------------------------------------------
# /detail/cross-country-breakeven-spread — same-tenor cross-country
# bond-implied breakeven spread bridge
# ----------------------------------------------------------------------------
# Per ``docs_revamped/03_standards/methodology_exposure.md §5`` the
# cross_country_breakeven_spread_simple primitive ships its OWN typed-detail
# endpoint.  Two-country, single-tenor primitive — each country contributes
# a (nominal, linker) pair at the shared tenor (e.g. UST/USD_TIPS 10Y vs
# UK_GILT/GBP_LINKER 10Y).  Schema layer rejects
# ``country_a_nominal_pair == country_b_nominal_pair`` AND
# ``country_a_linker_pair == country_b_linker_pair``; for same-country
# breakeven curve / spot work, see /detail/breakeven-curve-spread + /detail/
# breakeven-inflation-simple.  The same payload feeds BOTH the extended and
# compact Build views and the Monitor tile (rendering_density.md §10).
# Rolling-z-score conventions are YAML-locked on this primitive (no
# input-layer overrides); only ``lookback_days`` + ``field_name`` are exposed.
# ============================================================================
@router.get(
    "/detail/cross-country-breakeven-spread",
    response_model=CrossCountryBreakevenSpreadSimpleOutput,
    summary="Cross-Country Bond Breakeven Spread Detail (standalone bridge)",
)
def cross_country_breakeven_spread_detail(
    engine: Engine = Depends(get_engine),
    country_a_nominal_pair: str = Query(
        ...,
        description=(
            "Country A nominal sovereign curve family (e.g. 'UST', "
            "'UK_GILT', 'FR_OAT', 'DE_BUND', 'CANADA_GOVT').  Must "
            "differ from country_b_nominal_pair."
        ),
    ),
    country_a_linker_pair: str = Query(
        ...,
        description=(
            "Country A sovereign linker curve family (e.g. 'USD_TIPS', "
            "'GBP_LINKER', 'EUR_FR_LINKER', 'CAD_RRB').  Must share "
            "country/currency with country_a_nominal_pair."
        ),
    ),
    country_b_nominal_pair: str = Query(
        ...,
        description=(
            "Country B nominal sovereign curve family.  Must differ "
            "from country_a_nominal_pair (cross-country invariant)."
        ),
    ),
    country_b_linker_pair: str = Query(
        ...,
        description=(
            "Country B sovereign linker curve family.  Must share "
            "country/currency with country_b_nominal_pair, and must "
            "differ from country_a_linker_pair."
        ),
    ),
    tenor: str = Query(
        ...,
        description=(
            "Single tenor pillar applied to BOTH country legs (e.g. "
            "'5Y', '10Y', '30Y').  Cross-country spread is evaluated "
            "at the same tenor on each side."
        ),
    ),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    field_name: Optional[str] = Query(
        default=None,
        description=(
            "Bloomberg observation field for ALL FOUR underlying yield "
            "series (country_a_nominal, country_a_linker, "
            "country_b_nominal, country_b_linker).  Omit (None) to use "
            "the tool's bundled ``default_field_name`` convention "
            "(currently 'YLD_YTM_MID')."
        ),
    ),
):
    """Same payload semantics as the MCP wrapper; consumed by the frontend
    module's ``surfaces/BuildExtended.tsx``, ``surfaces/BuildCompact.tsx``,
    AND the Monitor widget per the rendering-density dual-view + monitor
    contract.

    The cross-country breakeven spread primitive intentionally does NOT
    expose the z-score conventions at its Input layer — its rolling-
    z-score conventions are sourced from the YAML at compute() time only.
    Mirrors the sibling cross_market_inflation_swap_spread bridge.
    """
    try:
        params = CrossCountryBreakevenSpreadSimpleInput(
            country_a_nominal_pair=country_a_nominal_pair,
            country_a_linker_pair=country_a_linker_pair,
            country_b_nominal_pair=country_b_nominal_pair,
            country_b_linker_pair=country_b_linker_pair,
            tenor=tenor,
            lookback_days=lookback_days,
            field_name=field_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    try:
        xcbe_config = load_tool_config(
            CROSS_COUNTRY_BREAKEVEN_SPREAD_SIMPLE_CONFIG_PATH,
        )
        result = calculate_cross_country_breakeven_spread_simple(
            engine=engine, params=params, config=xcbe_config,
        )
    except Exception as exc:
        logger.exception(
            "detail/cross-country-breakeven-spread: tool failed for "
            "%s/%s vs %s/%s %s",
            country_a_nominal_pair, country_a_linker_pair,
            country_b_nominal_pair, country_b_linker_pair, tenor,
        )
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    _tool_result_or_raise(
        result,
        (
            f"Cross-country breakeven spread for "
            f"{country_a_nominal_pair}/{country_a_linker_pair} - "
            f"{country_b_nominal_pair}/{country_b_linker_pair} {tenor}"
        ),
    )
    return result


# ============================================================================
# /detail/financing-rate — Standalone-bridge endpoint with ROUTE-SIDE SYNTHESIS
# ----------------------------------------------------------------------------
# FIRST-OF-ITS-KIND architectural deviation in this factory.  The backend
# ``FinancingRateOutput`` (rates_agent/ois/tools/financing_rate/schemas.py:148-187)
# returns a Panel-shaped artifact (single-column DataFrame of daily rates
# in PERCENT) plus method / as_of_start / as_of_end / mean_rate_pct /
# methodology_disclosures (List[str], PLURAL) — NOT the standard snapshot-tool
# ``current_metrics`` + ``time_series`` shape every other Phase-1 snapshot
# bridge consumes.  The Panel-based shape is load-bearing for the
# ``evaluate_trades`` workflow consumer (carries per-day rates with units +
# lineage); the MCP layer drops the panel for the LLM but the route handler
# can access it directly via ``result['panel'].payload``.
#
# Per the 2026-06-08 human resolution (Option (a), route-side synthesis) the
# backend Output is preserved AS-IS, and this route synthesizes the standard
# snapshot-shape response (FinancingRateDetailResponse below) by reducing the
# Panel.payload column to a single-column pd.Series and computing latest,
# daily/weekly/monthly change in bps (rate-in-pct delta × 100), rolling
# mean/std → z_score, max/min → 252d high/low, percentile rank, and the
# canonical TimeSeries view.  The frontend module is structurally identical
# to other snapshot tools — the synthesis is invisible above the typed-
# detail boundary.
# ============================================================================


_VALID_FINANCING_PROXY_CURVES = frozenset({
    "USD_SOFR_OIS",
    "EUR_ESTR_OIS",
    "GBP_SONIA_OIS",
    "JPY_TONA_OIS",
    "AUD_AONIA_OIS",
    "CAD_CORRA_OIS",
})


class FinancingRateCurrentMetrics(BaseModel):
    """Synthesized snapshot-current metrics for the financing-rate bridge.

    Built in-route from ``result['panel'].payload`` (single-column daily
    rates in PERCENT) because the underlying ``FinancingRateOutput`` is
    Panel-shaped (no native current_metrics field).  Field names mirror
    the other snapshot bridges exactly so the frontend layer is
    structurally identical.
    """

    model_config = ConfigDict(extra="forbid")

    as_of_date: str
    proxy_curve: str
    method: str
    financing_rate_pct: float
    daily_change_bps: Optional[float] = None
    weekly_change_bps: Optional[float] = None
    monthly_change_bps: Optional[float] = None
    z_score: Optional[float] = None
    high_252d_pct: Optional[float] = None
    low_252d_pct: Optional[float] = None
    percentile_252d: Optional[float] = None
    observation_count: int
    n_observations: int


class FinancingRateTimeSeriesRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: str
    value: Optional[float] = None


class FinancingRateTimeSeries(BaseModel):
    """Canonical-TimeSeries-shape view of the financing-rate series.

    Mirrors ``shared.schemas.time_series.TimeSeries`` so the frontend's
    BuildExtendedShell / BuildCompactShell can consume it identically
    to every other snapshot tool's ``time_series`` field.
    """

    model_config = ConfigDict(extra="forbid")

    series_name: str
    units: str
    description: str
    rows: List[FinancingRateTimeSeriesRow] = Field(default_factory=list)


class FinancingRateDetailResponse(BaseModel):
    """Top-level synthesized response for the financing-rate bridge.

    Composition:
      - current_metrics : snapshot-current view (route-side synthesis)
      - time_series     : canonical TimeSeries view (route-side synthesis)
      - methodology_disclosure : joined from FinancingRateOutput's PLURAL
                                 ``methodology_disclosures`` field (single
                                 string consumed by the methodology card +
                                 compact caveat).
    """

    model_config = ConfigDict(extra="forbid")

    current_metrics: FinancingRateCurrentMetrics
    time_series: FinancingRateTimeSeries
    methodology_disclosure: str


@router.get(
    "/detail/financing-rate",
    response_model=FinancingRateDetailResponse,
    summary="Financing Rate Detail (OIS-implied, standalone bridge, route-side synthesis)",
)
def financing_rate_detail(
    engine: Engine = Depends(get_engine),
    method: str = Query(
        default="overnight_index_proxy",
        description=(
            "Financing-rate method.  V1 ships ``overnight_index_proxy``; "
            "``constant_rate`` / ``term_repo_curve`` / ``gc_special_blend`` "
            "are out of scope on this bridge."
        ),
    ),
    proxy_curve: str = Query(
        ...,
        description=(
            "OIS proxy curve when method=overnight_index_proxy.  One of: "
            "USD_SOFR_OIS, EUR_ESTR_OIS, GBP_SONIA_OIS, JPY_TONA_OIS, "
            "AUD_AONIA_OIS, CAD_CORRA_OIS."
        ),
    ),
    lookback_days: int = Query(
        default=252,
        ge=60,
        le=2520,
        description=(
            "Trading-day display window for the synthesized snapshot "
            "stats (z-score / 252d high+low / percentile / time_series).  "
            "Defaults to 252 (one trading year)."
        ),
    ),
):
    """Synthesize a snapshot-shape response from the financing-rate Panel.

    See file-header comment for the architectural rationale.  Frontend
    surfaces (``BuildExtended.tsx``, ``BuildCompact.tsx``, Monitor tile)
    consume this synthesized response identically to other snapshot
    tools.
    """
    if method != "overnight_index_proxy":
        raise HTTPException(
            status_code=422,
            detail=(
                f"method={method!r} is out of scope on this bridge.  V1 "
                "supports 'overnight_index_proxy' only."
            ),
        )
    if proxy_curve not in _VALID_FINANCING_PROXY_CURVES:
        raise HTTPException(
            status_code=422,
            detail=(
                f"proxy_curve={proxy_curve!r} is not a recognised OIS "
                f"family.  Allowed: {sorted(_VALID_FINANCING_PROXY_CURVES)}."
            ),
        )

    # Calendar-day buffer for rolling-stats warmup before the 252d display
    # window (per catalog template snippet).  1.4× lookback + 60 days gives
    # enough non-trading-day padding.
    end_dt = date.today()
    start_dt = end_dt - timedelta(days=int(lookback_days * 1.4 + 60))

    try:
        params = FinancingRateInput(
            method=method,
            proxy_curve=proxy_curve,
            start_date=start_dt,
            end_date=end_dt,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    try:
        fr_config = load_tool_config(FINANCING_RATE_CONFIG_PATH)
        result = compute_financing_rate(
            engine=engine, params=params, config=fr_config,
        )
    except Exception as exc:
        logger.exception(
            "detail/financing-rate: tool failed for %s", proxy_curve,
        )
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    _tool_result_or_raise(result, f"Financing rate for {proxy_curve}")

    panel = result.get("panel")
    if panel is None or getattr(panel, "payload", None) is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Financing rate for {proxy_curve}: backend returned no "
                "Panel artifact (compute succeeded but produced no rate series)."
            ),
        )

    payload_df: pd.DataFrame = panel.payload
    if payload_df.empty or payload_df.shape[1] == 0:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Financing rate for {proxy_curve}: panel payload is "
                "empty over the requested window."
            ),
        )

    series_key = str(payload_df.columns[0])
    s = payload_df.iloc[:, 0].astype(float).dropna()
    if s.empty:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Financing rate for {proxy_curve}: panel payload contains "
                "no observations after dropping nulls."
            ),
        )

    # ---- Synthesize snapshot-current metrics ----------------------------
    latest = float(s.iloc[-1])
    daily_change_bps = (
        float((s.iloc[-1] - s.iloc[-2]) * 100.0) if len(s) >= 2 else None
    )
    weekly_change_bps = (
        float((s.iloc[-1] - s.iloc[-6]) * 100.0) if len(s) >= 6 else None
    )
    monthly_change_bps = (
        float((s.iloc[-1] - s.iloc[-22]) * 100.0) if len(s) >= 22 else None
    )

    display_window = s.iloc[-min(lookback_days, len(s)):]
    n_display = int(len(display_window))

    mean_v = float(display_window.mean())
    std_v = float(display_window.std(ddof=1)) if n_display >= 2 else 0.0
    z_score: Optional[float] = (
        float((latest - mean_v) / std_v) if std_v > 0 else None
    )
    high_pct = float(display_window.max()) if n_display > 0 else None
    low_pct = float(display_window.min()) if n_display > 0 else None
    percentile_252d: Optional[float] = (
        float(round(100.0 * (display_window <= latest).sum() / n_display))
        if n_display > 0 else None
    )

    as_of = display_window.index[-1].strftime("%Y-%m-%d")

    current_metrics = FinancingRateCurrentMetrics(
        as_of_date=as_of,
        proxy_curve=proxy_curve,
        method=method,
        financing_rate_pct=latest,
        daily_change_bps=daily_change_bps,
        weekly_change_bps=weekly_change_bps,
        monthly_change_bps=monthly_change_bps,
        z_score=z_score,
        high_252d_pct=high_pct,
        low_252d_pct=low_pct,
        percentile_252d=percentile_252d,
        observation_count=n_display,
        n_observations=int(result.get("n_observations") or len(s)),
    )

    # ---- Synthesize canonical TimeSeries view ---------------------------
    rows = [
        FinancingRateTimeSeriesRow(
            date=idx.strftime("%Y-%m-%d"), value=float(val),
        )
        for idx, val in display_window.items()
    ]
    time_series = FinancingRateTimeSeries(
        series_name=series_key,
        units="percent",
        description=(
            f"OIS-implied financing rate proxied by {proxy_curve} "
            f"(daily, percent) over the trailing {n_display} trading days."
        ),
        rows=rows,
    )

    # ---- Methodology disclosure (joined PLURAL list → singular) ---------
    disclosures = result.get("methodology_disclosures") or []
    methodology_disclosure = " ".join(str(d) for d in disclosures)

    return FinancingRateDetailResponse(
        current_metrics=current_metrics,
        time_series=time_series,
        methodology_disclosure=methodology_disclosure,
    )
