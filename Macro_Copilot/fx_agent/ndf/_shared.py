"""Shared NDF conventions and helpers.

Closed sets (mirror fx_ndf.yml universe and the BBG ticker conventions
validated in Phase 1 discovery 2026-05-25 / 26):

  - Supported NDF families:
      CCN+ → USDCNY underlying (PBOC fix reference, fx_family=EM_SPOT_REFERENCE
              in spot_fx.yml; offshore tradable proxy is USDCNH = EM_SPOT)
      IRN+ → USDINR underlying (composite reference, EM_SPOT)
      BCN+ → USDBRL underlying (EM_SPOT)
      KWN+ → USDKRW underlying (EM_SPOT)
      IHN+ → USDIDR underlying (EM_SPOT)
      NTN+ → USDTWD underlying (EM_SPOT, added in warehouse-seed)

  - Standard tenor strip (matches fx_forwards / fx_em_forwards
    convention so cross-substrate joins by tenor work without aliasing):
      1W, 1M, 3M, 6M, 12M

  - For each (ndf_code, tenor): vendor_ticker = f"{ndf_code}+{tenor} Curncy"
    e.g. CCN+1M Curncy, NTN+12M Curncy.

  - Underlying spot vendor_ticker = f"{underlying_pair} Curncy"
    e.g. USDCNY Curncy.

Centralised here so:
  - the implied-carry SQL JOIN can resolve ndf → underlying pair without
    re-implementing the map in every callsite;
  - downstream tools (Phase D scanner, future Phase E vol-vs-carry) get
    a single source of truth;
  - adding a new NDF family is a single-file change here + fx_ndf.yml
    universe + the converter SubstrateConfig regex.
"""

from __future__ import annotations

from typing import Dict, List, Literal

# Supported NDF family code → underlying spot pair (the pair the NDF
# settles against). CCN+ settles against USDCNY (the PBOC fix); IRN+
# against USDINR (composite); etc. Some downstream tools may prefer the
# offshore tradable proxy (USDCNH for CCN+) — that's a tool-level
# decision, not a substrate decision; this map gives the "official"
# settlement reference.
SUPPORTED_NDF_CODES: Dict[str, str] = {
    "CCN+": "USDCNY",
    "IRN+": "USDINR",
    "BCN+": "USDBRL",
    "KWN+": "USDKRW",
    "IHN+": "USDIDR",
    "NTN+": "USDTWD",
}

# Reverse map: underlying pair → ndf_code. Useful when a caller knows
# the pair and wants to resolve which NDF family.
PAIR_TO_NDF_CODE: Dict[str, str] = {v: k for k, v in SUPPORTED_NDF_CODES.items()}

# Closed Literal for Pydantic. Matches SUPPORTED_NDF_CODES.keys() exactly.
NDFCode = Literal["CCN+", "IRN+", "BCN+", "KWN+", "IHN+", "NTN+"]

# Closed Literal for NDF underlying pair. Matches SUPPORTED_NDF_CODES.values() exactly.
NDFPair = Literal["USDCNY", "USDINR", "USDBRL", "USDKRW", "USDIDR", "USDTWD"]

# Standard NDF tenor strip — same convention as G10/EM forwards so
# cross-substrate join semantics are unambiguous.
NDFTenor = Literal["1W", "1M", "3M", "6M", "12M"]

SUPPORTED_NDF_TENORS: tuple[str, ...] = ("1W", "1M", "3M", "6M", "12M")


def ndf_vendor_ticker(ndf_code: str, tenor: str) -> str:
    """Build the Bloomberg vendor_ticker for an NDF outright.

    e.g. ndf_vendor_ticker("CCN+", "1M") -> "CCN+1M Curncy"
    """
    if ndf_code not in SUPPORTED_NDF_CODES:
        raise ValueError(
            f"Unsupported NDF code {ndf_code!r}. "
            f"Supported: {sorted(SUPPORTED_NDF_CODES.keys())}"
        )
    if tenor not in SUPPORTED_NDF_TENORS:
        raise ValueError(
            f"Unsupported NDF tenor {tenor!r}. "
            f"Supported: {list(SUPPORTED_NDF_TENORS)}"
        )
    return f"{ndf_code}{tenor} Curncy"


def underlying_spot_ticker(ndf_code: str) -> str:
    """Return the Bloomberg vendor_ticker for the NDF's settlement spot.

    e.g. underlying_spot_ticker("CCN+") -> "USDCNY Curncy"
    """
    if ndf_code not in SUPPORTED_NDF_CODES:
        raise ValueError(
            f"Unsupported NDF code {ndf_code!r}. "
            f"Supported: {sorted(SUPPORTED_NDF_CODES.keys())}"
        )
    return f"{SUPPORTED_NDF_CODES[ndf_code]} Curncy"


def resolve_ndf(ndf_code_or_pair: str) -> tuple[str, str]:
    """Resolve a user-supplied identifier to (ndf_code, underlying_pair).

    Accepts either an ndf_code ("CCN+") or an underlying pair
    ("USDCNY") — returns the canonical tuple.
    """
    s = ndf_code_or_pair.upper().strip()
    if s in SUPPORTED_NDF_CODES:
        return s, SUPPORTED_NDF_CODES[s]
    if s in PAIR_TO_NDF_CODE:
        return PAIR_TO_NDF_CODE[s], s
    raise ValueError(
        f"Unrecognised NDF identifier {ndf_code_or_pair!r}. "
        f"Accepted: ndf_codes {sorted(SUPPORTED_NDF_CODES.keys())} "
        f"or pairs {sorted(PAIR_TO_NDF_CODE.keys())}"
    )


def list_ndf_codes() -> List[str]:
    """Closed list, alphabetical order, useful for default scan universes."""
    return sorted(SUPPORTED_NDF_CODES.keys())


def list_ndf_pairs() -> List[str]:
    """Closed list of underlying spot pairs, alphabetical."""
    return sorted(PAIR_TO_NDF_CODE.keys())
