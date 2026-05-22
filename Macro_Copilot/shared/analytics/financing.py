"""financing.py — financing-rate compute backend
================================================

Shared compute helpers for the ``financing_rate`` primitive (lives in
``rates_agent/ois/tools/financing_rate``).  Same finance-aware /
agnostic-of-tool-surface discipline as ``rates_fetch.py`` and
``panel_assembly.py``.

Methods
-------
V1 ships two methods (the caller's YAML enum carries four; the other
two raise ``NotImplementedError`` until repo data lands):

  - ``constant_rate`` — caller supplies a fixed rate; this module
    builds the daily Series across the requested business-day
    calendar.  No data fetched.
  - ``overnight_index_proxy`` — read the overnight tenor of a
    user-specified OIS curve (USD_SOFR_OIS, EUR_ESTR_OIS, etc.) from
    the existing ``market_data_daily`` and use that as the financing
    rate proxy.  Defensible default for sovereign repo when actual
    GC / term-repo data is not ingested.
  - ``term_repo_curve`` — NotImplementedError; waits on real repo
    ingestion.
  - ``gc_special_blend`` — NotImplementedError; waits on GC-special
    data.

Day-count conventions
---------------------
The caller picks ``act_360`` (default — sovereign repo + USD money
market), ``act_365`` (Gilt convention), or ``act_act_isda`` (rare for
financing but supported for completeness).  Day-count selection is
config-driven; this module just applies the factor.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import List, Optional

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine


# ============================================================================
# CONSTANTS
# ============================================================================


# OIS curve_families recognised as overnight-index proxies.  Each maps
# to the SHORTEST tenor in instrument_master that we treat as the
# overnight rate.  All OIS curves in our universe quote a 1W shortest
# tenor (per Macro_Copilot/rates_agent/playbooks/ois.yml); we use that
# as the overnight proxy in V1 because true O/N OIS quotes aren't
# uniformly ingested.  Documented in ``financing_rate``'s YAML
# methodology block.
_OIS_OVERNIGHT_TENOR_BY_FAMILY = {
    "USD_SOFR_OIS": "1W",
    "EUR_ESTR_OIS": "1W",
    "GBP_SONIA_OIS": "1W",
    "JPY_TONA_OIS": "1W",
    "AUD_AONIA_OIS": "1W",
    "CAD_CORRA_OIS": "1W",
}


_DAY_COUNT_BASIS = {
    "act_360": 360.0,
    "act_365": 365.0,
    # ACT/ACT ISDA is calendar-year aware; for daily accrual the
    # effective basis is the actual days in the current year (365 or
    # 366).  We approximate with 365.0 in V1 and document the choice
    # on the methodology card.
    "act_act_isda": 365.0,
}


# ============================================================================
# CONSTANT-RATE METHOD
# ============================================================================


def constant_rate_series(
    start_date: date,
    end_date: date,
    constant_rate_pct: float,
    calendar: str = "business_days",
) -> pd.Series:
    """Build a daily Series of a fixed rate over a date range.

    Parameters
    ----------
    start_date, end_date :
        Inclusive bounds.
    constant_rate_pct :
        The rate in PERCENT (e.g. 5.30 for 5.30%).  Caller-supplied;
        this module does NOT default it (the YAML enforces caller
        responsibility via Pydantic).
    calendar :
        ``business_days`` (Mon-Fri only, no holiday awareness in V1)
        or ``calendar_days`` (every day).  Holiday-aware calendars
        are deferred to when we ingest holiday tables.
    """
    if end_date < start_date:
        raise ValueError(
            f"constant_rate_series: end_date {end_date} < start_date "
            f"{start_date}."
        )

    if calendar == "business_days":
        idx = pd.bdate_range(start=start_date, end=end_date)
    elif calendar == "calendar_days":
        idx = pd.date_range(start=start_date, end=end_date, freq="D")
    else:
        raise ValueError(
            f"Unknown calendar {calendar!r}; expected one of "
            "['business_days', 'calendar_days']."
        )

    return pd.Series(
        data=float(constant_rate_pct),
        index=pd.DatetimeIndex(idx),
        name="financing_rate_pct",
        dtype=float,
    )


# ============================================================================
# OVERNIGHT-INDEX-PROXY METHOD
# ============================================================================


def fetch_overnight_index_series(
    engine: Engine,
    proxy_curve: str,
    start_date: date,
    end_date: Optional[date] = None,
) -> pd.Series:
    """Fetch the overnight-tenor rate of an OIS curve over a date range.

    Used as a defensible financing-rate proxy when actual repo data is
    not ingested.  Reads ``PX_LAST`` (the canonical OIS rate field) at
    the proxy curve's shortest available tenor (currently 1W per
    ``rates_agent/playbooks/ois.yml`` — V1 documents this on the
    methodology card).

    Parameters
    ----------
    engine :
        Live SQLAlchemy engine.
    proxy_curve :
        OIS curve_family identifier (e.g. ``USD_SOFR_OIS``,
        ``EUR_ESTR_OIS``).  Must be a key in
        ``_OIS_OVERNIGHT_TENOR_BY_FAMILY``.
    start_date, end_date :
        Inclusive date bounds.

    Returns
    -------
    pd.Series
        Indexed by trade_date (DatetimeIndex), values are rates in
        PERCENT (e.g. 5.30 = 5.30%).  Empty Series if no data found.
    """
    if proxy_curve not in _OIS_OVERNIGHT_TENOR_BY_FAMILY:
        raise ValueError(
            f"proxy_curve {proxy_curve!r} is not a recognised OIS "
            f"family.  Expected one of "
            f"{sorted(_OIS_OVERNIGHT_TENOR_BY_FAMILY)}."
        )
    overnight_tenor = _OIS_OVERNIGHT_TENOR_BY_FAMILY[proxy_curve]

    params = {
        "curve_family": proxy_curve,
        "tenor": overnight_tenor,
        "field_name": "PX_LAST",
        "start_date": start_date.isoformat(),
    }
    end_date_filter = ""
    if end_date is not None:
        params["end_date"] = end_date.isoformat()
        end_date_filter = " AND trade_date <= :end_date"

    sql = text(
        f"""
        SELECT trade_date, field_value
        FROM macro_data.v_market_data_daily_enriched
        WHERE curve_family = :curve_family
          AND tenor = :tenor
          AND field_name = :field_name
          AND trade_date >= :start_date{end_date_filter}
        ORDER BY trade_date
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(sql, params).fetchall()

    if not rows:
        return pd.Series(dtype=float, name="financing_rate_pct")

    df = pd.DataFrame(rows, columns=["trade_date", "field_value"])
    s = pd.Series(
        data=df["field_value"].astype(float).values,
        index=pd.DatetimeIndex(pd.to_datetime(df["trade_date"].values)),
        name="financing_rate_pct",
        dtype=float,
    )
    return s


# ============================================================================
# DAY-COUNT CONVERSION
# ============================================================================


def annual_rate_to_daily_fraction(
    rate_pct: pd.Series,
    day_count_basis: str,
) -> pd.Series:
    """Convert a Series of annualised percent rates into per-day
    accrual fractions for use in P&L computation.

    Daily accrual fraction = (rate_pct / 100) × (1 / basis_days)

    The downstream consumer multiplies this by the leg notional and
    weight to get the per-day financing carry.
    """
    if day_count_basis not in _DAY_COUNT_BASIS:
        raise ValueError(
            f"Unknown day_count_basis {day_count_basis!r}; expected "
            f"one of {sorted(_DAY_COUNT_BASIS)}."
        )
    basis = _DAY_COUNT_BASIS[day_count_basis]
    return rate_pct.astype(float) / 100.0 / basis


__all__ = [
    "constant_rate_series",
    "fetch_overnight_index_series",
    "annual_rate_to_daily_fraction",
]
