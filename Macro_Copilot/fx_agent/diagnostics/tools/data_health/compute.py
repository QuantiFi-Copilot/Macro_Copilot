from __future__ import annotations

import pandas as pd
from sqlalchemy import text

from database.database import get_db_engine
from fx_agent.diagnostics.tools.data_health.schemas import (
    FXDataFamilyCoverage,
    FXDataHealthInput,
    FXDataHealthOutput,
    FXDataHealthPairCoverage,
    FXDataHealthRiskProxyCoverage,
)
from fx_agent.reference.conventions import (
    DEFAULT_FIELD_NAME,
    G10_CROSS_PAIRS,
    G10_SPOT_PAIRS,
    MACRO_RISK_PROXIES,
    TENOR_DAYS,
    normalize_pair,
)

FX_INSTRUMENT_TYPES = ("fx_spot", "fx_forward", "fx_vol", "risk_proxy")
EXPECTED_PAIRS = tuple(dict.fromkeys((*G10_SPOT_PAIRS, *G10_CROSS_PAIRS)))
EXPECTED_FORWARD_TENORS = tuple(TENOR_DAYS.keys())


def _date_or_none(value: object) -> str | None:
    if value is None or pd.isna(value):
        return None
    return pd.to_datetime(value).strftime("%Y-%m-%d")


def _clean_tenors(values: pd.Series) -> list[str]:
    return sorted(
        {
            str(value).upper().strip()
            for value in values.dropna().tolist()
            if str(value).strip()
        },
        key=lambda tenor: TENOR_DAYS.get(tenor, 999),
    )


def _series_label(row: pd.Series) -> str:
    pair = str(row.get("pair") or "").strip()
    tenor = str(row.get("tenor") or "").strip()
    ticker = str(row.get("vendor_ticker") or "").strip()
    instrument_type = str(row.get("instrument_type") or "").strip()
    if pair and tenor:
        return f"{instrument_type}:{pair}:{tenor}"
    if pair:
        return f"{instrument_type}:{pair}"
    return f"{instrument_type}:{ticker}"


def get_fx_data_health(params: FXDataHealthInput) -> FXDataHealthOutput:
    field_name = (params.field_name or DEFAULT_FIELD_NAME).upper().strip()

    query = text(
        """
        SELECT
            im.instrument_type,
            im.vendor_ticker,
            COALESCE(im.attributes ->> 'pair', '') AS pair,
            COALESCE(im.tenor, '') AS tenor,
            COUNT(d.field_value) AS observation_count,
            MIN(d.trade_date) AS first_date,
            MAX(d.trade_date) AS last_date
        FROM macro_data.instrument_master im
        LEFT JOIN macro_data.market_data_daily d
            ON d.instrument_id = im.instrument_id
           AND d.field_name = :field_name
           AND d.trade_date >= CURRENT_DATE - (:lookback_days || ' days')::interval
        WHERE im.instrument_type IN ('fx_spot', 'fx_forward', 'fx_vol', 'risk_proxy')
        GROUP BY im.instrument_type, im.vendor_ticker, pair, tenor
        ORDER BY im.instrument_type, pair, tenor, im.vendor_ticker
        """
    )

    engine = get_db_engine()
    with engine.connect() as conn:
        df = pd.read_sql(
            query,
            conn,
            params={"field_name": field_name, "lookback_days": params.lookback_days},
        )

    if df.empty:
        return FXDataHealthOutput(
            as_of_date=None,
            field_name=field_name,
            status="no fx instruments found",
            summary={
                "instrument_count": 0,
                "pair_count": 0,
                "missing_pair_count": len(EXPECTED_PAIRS),
                "stale_series_count": 0,
            },
            families=[],
            pairs=[],
            risk_proxies=[],
            missing_pairs=list(EXPECTED_PAIRS),
            stale_series=[],
        )

    df["observation_count"] = pd.to_numeric(df["observation_count"], errors="coerce").fillna(0).astype(int)
    df["last_date"] = pd.to_datetime(df["last_date"], errors="coerce")
    df["pair"] = df["pair"].fillna("").astype(str).map(lambda value: normalize_pair(value) if value else "")
    df["tenor"] = df["tenor"].fillna("").astype(str).map(lambda value: value.upper().strip())

    dataset_as_of = df["last_date"].max()
    as_of_date = _date_or_none(dataset_as_of)

    stale_mask = pd.Series(False, index=df.index)
    if pd.notna(dataset_as_of):
        stale_mask = (
            (df["observation_count"] == 0)
            | ((dataset_as_of - df["last_date"]).dt.days > params.stale_after_days)
        )
    df["is_stale"] = stale_mask

    family_labels = {
        "fx_spot": "Spot",
        "fx_forward": "Forwards",
        "fx_vol": "Volatility",
        "risk_proxy": "Macro risk proxies",
    }
    families: list[FXDataFamilyCoverage] = []
    for instrument_type in FX_INSTRUMENT_TYPES:
        group = df[df["instrument_type"] == instrument_type]
        if group.empty:
            families.append(
                FXDataFamilyCoverage(
                    family=family_labels[instrument_type],
                    instrument_type=instrument_type,
                    instruments=0,
                    series_with_data=0,
                    latest_date=None,
                    stale_series=0,
                )
            )
            continue
        families.append(
            FXDataFamilyCoverage(
                family=family_labels[instrument_type],
                instrument_type=instrument_type,
                instruments=int(len(group)),
                series_with_data=int((group["observation_count"] > 0).sum()),
                latest_date=_date_or_none(group["last_date"].max()),
                stale_series=int(group["is_stale"].sum()),
            )
        )

    pairs: list[FXDataHealthPairCoverage] = []
    observed_pairs = {
        pair for pair in df["pair"].tolist() if pair
    }
    pair_universe = sorted(
        set(EXPECTED_PAIRS) | observed_pairs,
        key=lambda pair: (pair not in EXPECTED_PAIRS, pair),
    )

    for pair in pair_universe:
        pair_df = df[df["pair"] == pair]
        spot = pair_df[pair_df["instrument_type"] == "fx_spot"]
        forwards = pair_df[pair_df["instrument_type"] == "fx_forward"]
        vols = pair_df[pair_df["instrument_type"] == "fx_vol"]

        has_spot = bool((spot["observation_count"] > 0).any())
        forward_tenors = _clean_tenors(forwards.loc[forwards["observation_count"] > 0, "tenor"])
        vol_tenors = _clean_tenors(vols.loc[vols["observation_count"] > 0, "tenor"])

        missing: list[str] = []
        if not has_spot:
            missing.append("spot")
        missing_forward_tenors = [
            tenor for tenor in EXPECTED_FORWARD_TENORS if tenor not in forward_tenors
        ]
        if missing_forward_tenors:
            missing.append(f"forwards:{','.join(missing_forward_tenors)}")
        if pair in G10_SPOT_PAIRS and not vol_tenors:
            missing.append("vol")

        if not has_spot:
            status = "missing spot"
        elif not missing:
            status = "complete"
        else:
            status = "partial"

        pairs.append(
            FXDataHealthPairCoverage(
                pair=pair,
                has_spot=has_spot,
                forward_tenors=forward_tenors,
                vol_tenors=vol_tenors,
                latest_spot_date=_date_or_none(spot["last_date"].max()) if not spot.empty else None,
                latest_forward_date=_date_or_none(forwards["last_date"].max()) if not forwards.empty else None,
                latest_vol_date=_date_or_none(vols["last_date"].max()) if not vols.empty else None,
                observation_count=int(pair_df["observation_count"].sum()) if not pair_df.empty else 0,
                status=status,
                missing=missing,
            )
        )

    risk_proxies: list[FXDataHealthRiskProxyCoverage] = []
    proxy_df = df[df["instrument_type"] == "risk_proxy"]
    observed_proxies = {
        str(ticker).strip()
        for ticker in proxy_df["vendor_ticker"].dropna().tolist()
        if str(ticker).strip()
    }
    for ticker in sorted(set(MACRO_RISK_PROXIES) | observed_proxies):
        rows = proxy_df[proxy_df["vendor_ticker"] == ticker]
        observation_count = int(rows["observation_count"].sum()) if not rows.empty else 0
        latest_date = _date_or_none(rows["last_date"].max()) if not rows.empty else None
        status = "available" if observation_count > 0 else "missing"
        if not rows.empty and bool(rows["is_stale"].any()):
            status = "stale"
        risk_proxies.append(
            FXDataHealthRiskProxyCoverage(
                ticker=ticker,
                latest_date=latest_date,
                observation_count=observation_count,
                status=status,
            )
        )

    missing_pairs = [row.pair for row in pairs if not row.has_spot]
    stale_series = [
        _series_label(row)
        for _, row in df[df["is_stale"]].iterrows()
    ]

    partial_or_missing = sum(1 for row in pairs if row.status != "complete")
    if missing_pairs:
        status = "missing required data"
    elif partial_or_missing:
        status = "partial coverage"
    elif stale_series:
        status = "stale series present"
    else:
        status = "healthy"

    return FXDataHealthOutput(
        as_of_date=as_of_date,
        field_name=field_name,
        status=status,
        summary={
            "instrument_count": int(len(df)),
            "pair_count": int(len([row for row in pairs if row.has_spot])),
            "missing_pair_count": int(len(missing_pairs)),
            "partial_pair_count": int(partial_or_missing),
            "risk_proxy_count": int(sum(1 for row in risk_proxies if row.status != "missing")),
            "stale_series_count": int(len(stale_series)),
        },
        families=families,
        pairs=pairs,
        risk_proxies=risk_proxies,
        missing_pairs=missing_pairs,
        stale_series=stale_series,
    )

