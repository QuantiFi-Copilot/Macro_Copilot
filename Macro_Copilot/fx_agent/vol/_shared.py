"""Shared FX vol conventions and helpers.

Centralised constants for the Phase E ATM vol tools (Phase E1) and the
future smile / vol-carry tools (Phase E2/E3).

Tenor convention asymmetry note: Bloomberg quotes ATM vol 1Y as 'V1Y'
(not 'V12M'), but we store the internal tenor as '12M' in
instrument_master.tenor (matches the forwards / fx_em_forwards
convention so a cross-substrate join by tenor works without aliasing
per pair). Our tools accept '12M' externally — the converter takes
care of the BBG ticker convention via _build_em_vol_ticker.
"""

from __future__ import annotations

from typing import Dict, List, Literal

# Standard ATM vol tenor strip. Matches fx_forwards / fx_em_forwards
# convention so cross-substrate joins by tenor work without aliasing.
# Extended tenors (ON/2W/2M/9M/2Y) are ingested in DB but NOT exposed
# via the Phase E1 tools — the standard strip covers the canonical
# trader workflow; extended tenors land in a Phase E2+ if needed.
FXVolStandardTenor = Literal["1W", "1M", "3M", "6M", "12M"]

SUPPORTED_VOL_TENORS: tuple[str, ...] = ("1W", "1M", "3M", "6M", "12M")

# Closed market_scope for the vol scanner. Mirror of fx_carry /
# fx_panel scope semantics so cross-asset readers compare extremeness
# at the same horizon and across the same universe.
FXVolMarketScope = Literal["G10", "EM", "G10_CROSSES", "ALL"]

# market_scope → fx_family list (closed map). fx_vol entries currently
# carry one of:
#   - G10_FX_VOL        (6 G10 majors USD-leg standard tenors)
#   - EM_FX_VOL         (17 EM/NDF-currency standard tenors)
#   - G10_CROSSES_FX_VOL (11 G10 cross ATM vols, Tradability 2026-05-26)
# Extended-tenor rows share the same fx_family value as their standard-
# tenor counterparts — the discriminator is the tenor field, not the
# family. Phase E1 only exposes standard tenors so extended-tenor rows
# are naturally excluded by the tenor filter.
_MARKET_SCOPE_TO_FX_FAMILIES: Dict[str, List[str]] = {
    "G10": ["G10_FX_VOL"],
    "EM": ["EM_FX_VOL"],
    "G10_CROSSES": ["G10_CROSSES_FX_VOL"],
    "ALL": ["G10_FX_VOL", "EM_FX_VOL", "G10_CROSSES_FX_VOL"],
}


def fx_families_for_scope(scope: str) -> List[str]:
    """Resolve a market_scope value to its closed list of fx_family values.

    Fail-loud on unknown scope (mirror of FXVolMarketScope Literal).
    """
    if scope not in _MARKET_SCOPE_TO_FX_FAMILIES:
        raise ValueError(
            f"Unsupported vol market_scope {scope!r}. "
            f"Supported: {sorted(_MARKET_SCOPE_TO_FX_FAMILIES.keys())}"
        )
    return list(_MARKET_SCOPE_TO_FX_FAMILIES[scope])


def vol_vendor_ticker(pair: str, tenor: str) -> str:
    """Build the Bloomberg vendor_ticker for an ATM vol observation.

    Note: BBG ticker uses 'V1Y' for the 1Y tenor (not 'V12M'). Our
    internal tenor is stored as '12M' to match the forwards convention.
    The mapping is handled here so tools can call with '12M' and get
    the right BBG ticker.
    """
    if tenor not in SUPPORTED_VOL_TENORS:
        raise ValueError(
            f"Unsupported vol tenor {tenor!r}. Supported: {list(SUPPORTED_VOL_TENORS)}"
        )
    bbg_tenor = "1Y" if tenor == "12M" else tenor
    return f"{pair}V{bbg_tenor} Curncy"
