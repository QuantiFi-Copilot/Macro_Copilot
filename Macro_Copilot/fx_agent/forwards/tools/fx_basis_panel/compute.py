"""compute.py — Deterministic FX CIP basis Panel assembly.

Phase F1 (2026-05-27).

Builds a closed-family ``Panel`` artifact of CIP basis (in bps)
for the V1 closed pair set ``{EURUSD, GBPUSD, USDJPY, AUDUSD,
USDCAD}`` at one tenor. Composition primitive:

  basis_bps(date, pair) = (fx_iyd(date, pair) - ois_diff(date, pair)) * 100

Per-pair math INLINED from ``cross_currency_basis`` (PR #239) for
self-containedness — duplication acknowledged in the methodology
block. Future refactor: extract per-pair basis-series helper to
``fx_agent/forwards/_shared.py`` once cross_currency_basis lands.

Sign convention HARD-LOCKED Bloomberg BCRX-style: NEGATIVE = USD
scarcity. Inherited from cross_currency_basis primitive which
validated the convention via four corroborations.

Asset-agnostic shared-compute discipline
----------------------------------------
This tool is an FX-side composition layer (FX iyd + OIS diff →
basis_bps). It does NOT compute correlation / PCA / factor
loadings / regression — those asset-agnostic operators live in
``shared/operators`` and consume the returned ``Panel`` artifact.

CROSS-DOMAIN: this primitive reads from rates_agent OIS substrate
via ``shared.analytics.rates_fetch.fetch_cross_market_pair`` —
same pattern as cross_currency_basis. No rates_agent modification.

Test seam
---------
``fetch_cross_market_pair`` is imported at module level so unit
tests can monkeypatch the OIS leg via
``patch("fx_agent.forwards.tools.fx_basis_panel.compute.fetch_cross_market_pair")``.

Operationalises: P3, P5, P11; PR1, PR4, PR5, PR7, PR10, PR12, PR13.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from fx_agent.forwards._shared import (
    points_to_spot_units,
    tenor_days_from_config,
)
from fx_agent.forwards.tools.fx_basis_panel.schemas import (
    FXBasisPanelInput,
    FXBasisPanelOutput,
)
from shared.analytics.rates_fetch import fetch_cross_market_pair
from shared.artifacts.lineage import Lineage, PrimitiveStep
from shared.artifacts.missingness import RawNoCleaning
from shared.artifacts.types import Panel
from shared.artifacts.units import TimeSeriesUnits
from shared.config import ToolConfig, load_tool_config


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"

_TOOL_NAME = "calculate_fx_basis_panel_tool"
_TOOL_VERSION = "1.0.0"

_ALLOWED_MISSING_POLICIES = frozenset({
    "raise",
    "forward_fill_only",
    "drop_rows_any_missing",
})

# Hard-locked V1 pair set (mirrors cross_currency_basis V1 scope).
_G10_BASIS_V1_PAIRS: Tuple[str, ...] = (
    "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD",
)

# Hard-locked V1 mapping (mirrors cross_currency_basis).
_LOCAL_CURRENCY_TO_OIS_CURVE: Dict[str, str] = {
    "EUR": "EUR_ESTR_OIS",
    "GBP": "GBP_SONIA_OIS",
    "JPY": "JPY_OIS",
    "AUD": "AUD_OIS",
    "CAD": "CAD_OIS",
}

# FX tenor → OIS tenor mapping (FX 12M aliased to OIS 1Y).
_FX_TO_OIS_TENOR: Dict[str, str] = {
    "1W": "1W",
    "1M": "1M",
    "3M": "3M",
    "6M": "6M",
    "12M": "1Y",
}


def _resolve_usd_leg(pair: str) -> Tuple[str, str, float]:
    """Return (usd_leg_position, local_currency, sign_for_local_minus_usd).

    Mirrors _resolve_usd_leg in cross_currency_basis.compute.
    """
    pair_clean = pair.upper().replace("/", "").strip()
    base, quote = pair_clean[:3], pair_clean[3:]
    if base == "USD":
        return ("base", quote, +1.0)
    if quote == "USD":
        return ("quote", base, -1.0)
    raise ValueError(
        f"pair {pair!r} has no USD leg — only V1 USD-leg pairs supported."
    )


def _fx_history_query():
    return text(
        """
        WITH spot_history AS (
            SELECT d.trade_date, d.field_value::float AS spot
            FROM macro_data.market_data_daily d
            JOIN macro_data.instrument_master im
                ON d.instrument_id = im.instrument_id
            WHERE im.instrument_type = 'fx_spot'
              AND im.attributes ->> 'pair' = :pair
              AND d.field_name = :field_name
              AND d.trade_date >= :start_date
        ),
        fwd_history AS (
            SELECT d.trade_date, d.field_value::float AS forward_points
            FROM macro_data.market_data_daily d
            JOIN macro_data.instrument_master im
                ON d.instrument_id = im.instrument_id
            WHERE im.instrument_type = 'fx_forward'
              AND im.attributes ->> 'pair' = :pair
              AND im.tenor = :tenor
              AND d.field_name = :field_name
              AND d.trade_date >= :start_date
        )
        SELECT s.trade_date, s.spot, f.forward_points
        FROM spot_history s
        INNER JOIN fwd_history f ON s.trade_date = f.trade_date
        ORDER BY s.trade_date ASC
        """
    )


def _compute_pair_basis_bps_series(
    engine: Engine,
    *,
    pair: str,
    fx_tenor: str,
    ois_tenor: str,
    local_ois_curve: str,
    usd_ois_curve: str,
    fx_field: str,
    ois_field: str,
    start_date: pd.Timestamp,
    sign: float,
    jpy_divisor: float,
    default_divisor: float,
    annualization_days: int,
    tenor_days: int,
    ffill_limit: int,
) -> pd.Series:
    """Return the CIP basis series in BPS for one (pair, tenor), date-indexed.

    Math IDENTICAL to cross_currency_basis primitive (PR #239) — inlined
    here for self-containedness. Returns empty Series if there is no
    FX×OIS overlap for the requested window (caller decides
    panel-level handling).
    """
    # FX leg: joined spot + forward → fx_iyd in PERCENT (LOCAL minus USD).
    with engine.connect() as conn:
        fx_df = pd.read_sql(
            _fx_history_query(), conn,
            params={
                "pair": pair, "tenor": fx_tenor,
                "field_name": fx_field,
                "start_date": start_date.date().isoformat(),
            },
        )
    if fx_df.empty:
        return pd.Series(dtype=float, name=pair)
    fx_df["trade_date"] = pd.to_datetime(fx_df["trade_date"])
    fx_df["spot"] = pd.to_numeric(fx_df["spot"], errors="coerce")
    fx_df["forward_points"] = pd.to_numeric(
        fx_df["forward_points"], errors="coerce",
    )
    fx_df = (
        fx_df.dropna(subset=["spot", "forward_points"])
        .drop_duplicates(subset=["trade_date"], keep="last")
        .sort_values("trade_date")
        .set_index("trade_date")
        .ffill(limit=ffill_limit)
        .dropna()
    )
    if fx_df.empty:
        return pd.Series(dtype=float, name=pair)

    fx_df["fp_spot_units"] = fx_df["forward_points"].apply(
        lambda fp: points_to_spot_units(
            pair, fp,
            jpy_divisor=jpy_divisor, default_divisor=default_divisor,
        )
    )
    fx_df["outright"] = fx_df["spot"] + fx_df["fp_spot_units"]
    raw_diff_pct = (
        (fx_df["outright"] / fx_df["spot"] - 1.0)
        * (annualization_days / tenor_days)
        * 100.0
    )
    fx_iyd_series = sign * raw_diff_pct

    # OIS leg: (local_ois - usd_ois) in PERCENT.
    ois_df = fetch_cross_market_pair(
        engine=engine,
        curve_family_1=local_ois_curve,
        curve_family_2=usd_ois_curve,
        tenor=ois_tenor,
        field_name=ois_field,
        start_date=start_date.date(),
    )
    if ois_df.empty:
        return pd.Series(dtype=float, name=pair)
    ois_df["trade_date"] = pd.to_datetime(ois_df["trade_date"])
    ois_df["field_value"] = pd.to_numeric(ois_df["field_value"], errors="coerce")
    ois_df = ois_df.dropna(subset=["field_value"])
    ois_wide = (
        ois_df.pivot_table(
            index="trade_date", columns="curve_family",
            values="field_value", aggfunc="last",
        )
        .sort_index()
        .ffill(limit=ffill_limit)
    )
    if (
        local_ois_curve not in ois_wide.columns
        or usd_ois_curve not in ois_wide.columns
    ):
        return pd.Series(dtype=float, name=pair)
    ois_diff_series = ois_wide[local_ois_curve] - ois_wide[usd_ois_curve]

    # Inner-join FX × OIS → basis_bps.
    joined = pd.concat(
        {"fx_iyd": fx_iyd_series, "ois_diff": ois_diff_series},
        axis=1, join="inner",
    ).dropna()
    if joined.empty:
        return pd.Series(dtype=float, name=pair)
    # Bloomberg BCRX sign convention HARD-LOCKED (NEGATIVE = USD scarcity).
    # Math derivation + validation: see cross_currency_basis/compute.py.
    basis = (joined["fx_iyd"] - joined["ois_diff"]) * 100.0
    basis.name = pair
    return basis


# ============================================================================
# PUBLIC API
# ============================================================================


def calculate_fx_basis_panel(
    engine: Engine,
    params: FXBasisPanelInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """Assemble a CIP basis Panel for the V1 closed pair set at one tenor.

    Returns ``output.model_dump(mode="python")`` preserving the typed
    ``Panel`` artifact under the ``panel`` key.

    Raises
    ------
    ValueError
        - if ``missing_data_policy`` is not in the allowed set
        - if no pair produces any basis observation (zero panel)
        - if any column is fully-NaN post-fetch (real data gap)
        - if post-policy panel is empty
    """
    if config is None:
        config = load_tool_config(CONFIG_PATH)

    fx_tenor = params.tenor
    if fx_tenor not in _FX_TO_OIS_TENOR:
        raise ValueError(
            f"Unsupported FX tenor {fx_tenor!r}. Supported: {sorted(_FX_TO_OIS_TENOR)}"
        )
    ois_tenor = _FX_TO_OIS_TENOR[fx_tenor]

    ffill_limit = int(config.convention_value("ffill_limit_days"))
    default_policy = config.convention_value("default_missing_data_policy")
    calendar_policy = config.convention_value("calendar_policy")

    missing_policy = params.missing_data_policy or default_policy
    if missing_policy not in _ALLOWED_MISSING_POLICIES:
        raise ValueError(
            f"missing_data_policy={missing_policy!r} is not in the "
            f"allowed set {sorted(_ALLOWED_MISSING_POLICIES)}. Check "
            "fx_basis_panel/config.yaml or the input override."
        )

    fx_field = (
        params.field_name or str(config.convention_value("default_fx_spot_field"))
    )
    ois_field = (
        params.field_name or str(config.convention_value("default_ois_field"))
    )
    usd_ois_curve = str(config.convention_value("usd_ois_curve"))

    tenor_days_by_tenor = tenor_days_from_config(config)
    tenor_days = tenor_days_by_tenor[fx_tenor]
    annualization_days = int(config.convention_value("annualization_days"))
    jpy_divisor = float(config.convention_value("jpy_forward_points_divisor"))
    default_divisor = float(config.convention_value("default_forward_points_divisor"))

    start_ts = pd.Timestamp(params.start_date)
    end_ts = pd.Timestamp(params.end_date) if params.end_date else None

    # Compute per-pair basis series.
    per_pair_series: Dict[str, pd.Series] = {}
    for pair in _G10_BASIS_V1_PAIRS:
        _, local_currency, sign = _resolve_usd_leg(pair)
        if local_currency not in _LOCAL_CURRENCY_TO_OIS_CURVE:
            raise ValueError(
                f"local_currency={local_currency} has no OIS curve in V1 mapping. "
                f"Supported: {sorted(_LOCAL_CURRENCY_TO_OIS_CURVE)}"
            )
        local_ois_curve = _LOCAL_CURRENCY_TO_OIS_CURVE[local_currency]
        series = _compute_pair_basis_bps_series(
            engine=engine,
            pair=pair,
            fx_tenor=fx_tenor,
            ois_tenor=ois_tenor,
            local_ois_curve=local_ois_curve,
            usd_ois_curve=usd_ois_curve,
            fx_field=fx_field,
            ois_field=ois_field,
            start_date=start_ts,
            sign=sign,
            jpy_divisor=jpy_divisor,
            default_divisor=default_divisor,
            annualization_days=annualization_days,
            tenor_days=tenor_days,
            ffill_limit=ffill_limit,
        )
        if not series.empty:
            per_pair_series[pair] = series

    if not per_pair_series:
        raise ValueError(
            f"calculate_fx_basis_panel: no basis observations produced for "
            f"market_scope={params.market_scope!r}, tenor={fx_tenor!r}, "
            f"start_date={params.start_date.isoformat()}. Verify FX and OIS "
            "substrates are both ingested over the requested window."
        )

    # Stack into wide panel, sorted alphabetically by column.
    raw_panel = pd.concat(per_pair_series, axis=1).sort_index().sort_index(axis=1)
    if end_ts is not None:
        raw_panel = raw_panel.loc[:end_ts]

    if calendar_policy == "business_days":
        raw_panel = raw_panel[raw_panel.index.dayofweek < 5]

    empty_pairs: List[str] = [
        col for col in raw_panel.columns if raw_panel[col].isna().all()
    ]
    if empty_pairs:
        raise ValueError(
            f"calculate_fx_basis_panel: pairs with NO observations "
            f"in the requested window: {empty_pairs}. Verify ingestion "
            "(load_audit + tests/test_fx_data_readiness.py)."
        )

    cleaned_panel = raw_panel.ffill(limit=ffill_limit) if ffill_limit > 0 else raw_panel.copy()
    if missing_policy == "raise":
        residual = cleaned_panel.isna().sum().sum()
        if residual > 0:
            raise ValueError(
                f"calculate_fx_basis_panel: missing_data_policy='raise' "
                f"but {int(residual)} NaN cells remain after "
                f"ffill_limit={ffill_limit}. Relax the policy or widen "
                "the date range."
            )
    elif missing_policy == "drop_rows_any_missing":
        cleaned_panel = cleaned_panel.dropna(axis=0, how="any")

    if cleaned_panel.empty:
        raise ValueError(
            f"calculate_fx_basis_panel: after applying "
            f"missing_data_policy={missing_policy!r}, the panel has "
            "zero rows. Either relax the policy or widen the start date."
        )

    # Every column is BPS-units (cross-currency basis in basis points).
    units_by_column: Dict[str, TimeSeriesUnits] = {
        col: TimeSeriesUnits.BPS for col in cleaned_panel.columns
    }

    step_params: Dict[str, Any] = {
        "market_scope": params.market_scope,
        "tenor": fx_tenor,
        "ois_tenor": ois_tenor,
        "fx_field": fx_field,
        "ois_field": ois_field,
        "usd_ois_curve": usd_ois_curve,
        "pairs": list(cleaned_panel.columns),
        "start_date": params.start_date.isoformat(),
        "end_date": params.end_date.isoformat() if params.end_date else None,
        "ffill_limit_days": ffill_limit,
        "missing_data_policy": missing_policy,
        "calendar_policy": calendar_policy,
        "n_observations": int(len(cleaned_panel)),
        "annualization_days": annualization_days,
        "tenor_days": tenor_days,
        "sign_convention": "bloomberg_bcrx_usd_scarcity_negative",
    }
    as_of_date_iso = pd.Timestamp(cleaned_panel.index[-1]).strftime("%Y-%m-%d")
    step = PrimitiveStep.build(
        name=_TOOL_NAME,
        version=_TOOL_VERSION,
        params=step_params,
        tool_config_hash=config.conventions_hash(),
        output_field="panel",
        as_of_date=as_of_date_iso,
        tool_config_path=str(CONFIG_PATH),
    )
    lineage = Lineage.from_steps([step])

    panel_artifact = Panel(
        payload=cleaned_panel,
        units_by_column=units_by_column,
        missingness_policy=RawNoCleaning(),
        lineage=lineage,
    )

    disclosures = [
        f"market_scope={params.market_scope}; tenor={fx_tenor}; "
        f"{len(cleaned_panel.columns)} pair(s) in the assembled basis panel.",
        (
            "Sign convention HARD-LOCKED 'bloomberg_bcrx_usd_scarcity_negative' "
            "— NEGATIVE basis = USD scarcity. Validated VALIDATED+FROZEN in "
            "cross_currency_basis PR #239 via four independent corroborations."
        ),
        (
            f"FX tenor → OIS tenor: {fx_tenor} → {ois_tenor} "
            "(FX 12M aliased to OIS 1Y)."
        ),
        f"calendar_policy={calendar_policy} (V1: weekends excluded).",
        f"missing_data_policy={missing_policy} "
        f"(ffill_limit_days={ffill_limit}).",
        "Units = TimeSeriesUnits.BPS (basis in basis points).",
        (
            "V1 closed pair set: EURUSD, GBPUSD, USDJPY, AUDUSD, USDCAD — "
            "only pairs with both FX-forward + local/USD OIS coverage."
        ),
        (
            "Asset-agnostic discipline: this tool does NOT compute "
            "correlation / PCA / factor loadings — pipe the returned "
            "Panel into shared/operators for those operations."
        ),
    ]

    output = FXBasisPanelOutput(
        as_of_start=pd.Timestamp(cleaned_panel.index[0]).strftime("%Y-%m-%d"),
        as_of_end=as_of_date_iso,
        market_scope=params.market_scope,
        tenor=fx_tenor,
        sign_convention="bloomberg_bcrx_usd_scarcity_negative",
        columns=list(cleaned_panel.columns),
        n_observations=int(len(cleaned_panel)),
        n_pairs=int(len(cleaned_panel.columns)),
        units_by_column={
            col: units.value for col, units in units_by_column.items()
        },
        methodology_disclosures=disclosures,
        panel=panel_artifact,
    )

    return output.model_dump(mode="python")


__all__ = [
    "CONFIG_PATH",
    "calculate_fx_basis_panel",
]
