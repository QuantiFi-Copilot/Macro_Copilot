from __future__ import annotations

DEFAULT_FIELD_NAME = "PX_LAST"
DAYS_IN_YEAR = 252

TENOR_DAYS = {
    "1W": 5,
    "1M": 21,
    "3M": 63,
    "6M": 126,
}

G10_SPOT_PAIRS = (
    "EURUSD",
    "GBPUSD",
    "AUDUSD",
    "USDJPY",
    "USDCAD",
    "USDCHF",
)

G10_CROSS_PAIRS = (
    "EURGBP",
    "EURJPY",
    "GBPJPY",
    "AUDJPY",
    "AUDCAD",
    "EURCHF",
)

G10_FX_VOL_PAIRS = G10_SPOT_PAIRS

USD_BASE_PAIRS = frozenset({"USDJPY", "USDCAD", "USDCHF"})
USD_QUOTE_PAIRS = frozenset({"EURUSD", "GBPUSD", "AUDUSD"})

MACRO_RISK_PROXIES = (
    "DXY Curncy",
    "SPX Index",
    "VIX Index",
    "XAU Curncy",
    "MOVE Index",
    "CL1 Comdty",
)


def normalize_pair(pair: str) -> str:
    return pair.upper().replace("/", "").strip()


def normalize_tenor(tenor: str) -> str:
    return tenor.upper().strip()


def is_jpy_pair(pair: str) -> bool:
    return "JPY" in normalize_pair(pair)


def tenor_days(tenor: str, default: int = 21) -> int:
    return TENOR_DAYS.get(normalize_tenor(tenor), default)


def points_to_spot_units(pair: str, forward_points: float) -> float:
    """Convert Bloomberg FX forward points into spot units."""
    return forward_points / 100.0 if is_jpy_pair(pair) else forward_points / 10000.0

