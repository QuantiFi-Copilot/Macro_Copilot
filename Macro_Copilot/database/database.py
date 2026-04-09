import os
import pandas as pd
from typing import List, Dict, Any, Optional
from datetime import datetime
from sqlalchemy import create_engine, MetaData, Table, func, select
from sqlalchemy.dialects.postgresql import insert


# ==============================================================================
# CORE DATABASE CONNECTION
# ==============================================================================
def get_db_engine():
    """
    Creates and returns a SQLAlchemy engine using environment variables.
    Defaults to the Docker Compose settings if variables are not set.
    """
    user = os.getenv("DB_USER", "quantuser")
    password = os.getenv("DB_PASSWORD", "myStrongPass")
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5433")
    db_name = os.getenv("DB_NAME", "macrodata")

    conn_str = f"postgresql://{user}:{password}@{host}:{port}/{db_name}"
    return create_engine(conn_str, pool_size=10, max_overflow=20)


# ==============================================================================
# HELPERS
# ==============================================================================
def _jsonify_if_needed(value: Any) -> Any:
    """Pass Python dict/list values through directly for JSONB columns."""
    return value


def _normalize_instrument_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize incoming instrument records to the new schema.

    Backward compatibility:
    - accepts `ticker` as an alias for `vendor_ticker`
    - fills sensible defaults for required fields not yet present in old playbooks
    """
    rec = record.copy()

    vendor_ticker = rec.get("vendor_ticker") or rec.get("ticker")
    if not vendor_ticker:
        raise ValueError("Instrument record is missing `vendor_ticker`/`ticker`.")

    normalized = {
        "vendor": rec.get("vendor", "BLOOMBERG"),
        "vendor_ticker": vendor_ticker,
        "asset_class": rec.get("asset_class") or rec.get("dataset_name"),
        "instrument_type": rec.get("instrument_type", "unknown"),
        "curve_family": rec.get("curve_family"),
        "country": rec.get("country"),
        "currency": rec.get("currency"),
        "tenor": rec.get("tenor"),
        "underlying_index": rec.get("underlying_index"),
        "contract_code": rec.get("contract_code"),
        "expiry_date": rec.get("expiry_date"),
        "maturity_date": rec.get("maturity_date"),
        "is_rolling_contract": bool(rec.get("is_rolling_contract", False)),
        "is_active": bool(rec.get("is_active", True)),
        "attributes": _jsonify_if_needed(rec.get("attributes")),
    }

    if not normalized["asset_class"]:
        raise ValueError(f"Instrument record for {vendor_ticker} is missing `asset_class`.")

    return normalized


# ==============================================================================
# STATIC REFERENCE DATA (THE 'WHO')
# ==============================================================================
def upsert_instrument_master(engine, records: List[Dict[str, Any]]) -> Dict[str, int]:
    """
    Upserts metadata into `macro_data.instrument_master` using the unique key
    on (vendor, vendor_ticker).

    Returns a mapping: {vendor_ticker: instrument_id}

    Minimum expected fields per record:
    - asset_class
    - vendor_ticker or ticker

    Strongly recommended fields:
    - instrument_type
    - country / currency / tenor / curve_family / underlying_index
    """
    if not records:
        return {}

    metadata = MetaData(schema="macro_data")
    table = Table("instrument_master", metadata, autoload_with=engine)

    clean_records = [_normalize_instrument_record(r) for r in records]

    stmt = insert(table).values(clean_records)

    update_dict = {
        "asset_class": stmt.excluded.asset_class,
        "instrument_type": stmt.excluded.instrument_type,
        "curve_family": stmt.excluded.curve_family,
        "country": stmt.excluded.country,
        "currency": stmt.excluded.currency,
        "tenor": stmt.excluded.tenor,
        "underlying_index": stmt.excluded.underlying_index,
        "contract_code": stmt.excluded.contract_code,
        "expiry_date": stmt.excluded.expiry_date,
        "maturity_date": stmt.excluded.maturity_date,
        "is_rolling_contract": stmt.excluded.is_rolling_contract,
        "is_active": stmt.excluded.is_active,
        "attributes": stmt.excluded.attributes,
        "updated_at": func.now(),
    }

    stmt = stmt.on_conflict_do_update(
        index_elements=["vendor", "vendor_ticker"],
        set_=update_dict,
    )

    tickers = [r["vendor_ticker"] for r in clean_records]
    vendor = clean_records[0]["vendor"]

    with engine.begin() as conn:
        conn.execute(stmt)

        lookup_stmt = (
            select(table.c.vendor_ticker, table.c.instrument_id)
            .where(table.c.vendor == vendor)
            .where(table.c.vendor_ticker.in_(tickers))
        )
        rows = conn.execute(lookup_stmt).fetchall()

    instrument_id_map = {row.vendor_ticker: row.instrument_id for row in rows}
    print(f"[DB] Upserted/verified {len(instrument_id_map)} instruments in instrument_master.")
    return instrument_id_map


# ==============================================================================
# LOAD / EXTRACTION LINEAGE
# ==============================================================================
def insert_load_audit(engine, audit_record: Dict[str, Any]) -> int:
    """
    Inserts one row into `macro_data.load_audit` and returns the generated load_id.

    Recommended fields:
    - playbook_name
    - playbook_version
    - playbook_hash
    - git_commit_hash
    - extractor_version
    - source_file_name
    - source_file_hash
    - dataset_name
    - requested_start_date
    - requested_end_date
    - extracted_at
    - status
    - notes
    """
    metadata = MetaData(schema="macro_data")
    table = Table("load_audit", metadata, autoload_with=engine)

    record = audit_record.copy()
    if "playbook_name" not in record or not record["playbook_name"]:
        raise ValueError("audit_record must include `playbook_name`.")

    stmt = insert(table).values(record).returning(table.c.load_id)

    with engine.begin() as conn:
        load_id = conn.execute(stmt).scalar_one()

    print(f"[DB] Inserted load_audit row with load_id={load_id}.")
    return load_id


# ==============================================================================
# LOAD AUDIT HELPERS
# ==============================================================================

def get_latest_successful_load_for_playbook(engine, playbook_name: str) -> Optional[Dict[str, Any]]:
    """
    Returns the most recent SUCCESS row from macro_data.load_audit for the given
    playbook_name, or None if no successful load exists yet.

    This is intended for pre-ingestion dedup checks such as comparing the current
    parquet artifact hash against the latest successfully ingested artifact for
    the same playbook.
    """
    metadata = MetaData(schema="macro_data")
    table = Table("load_audit", metadata, autoload_with=engine)

    stmt = (
        select(table)
        .where(table.c.playbook_name == playbook_name)
        .where(table.c.status == "SUCCESS")
        .order_by(table.c.ingested_at.desc(), table.c.load_id.desc())
        .limit(1)
    )

    with engine.begin() as conn:
        row = conn.execute(stmt).mappings().first()

    return dict(row) if row else None


def mark_load_audit_skipped_duplicate(
    engine,
    playbook_name: str,
    playbook_version: Optional[str],
    source_file_name: Optional[str],
    source_file_hash: str,
    dataset_name: Optional[str],
    requested_start_date: Optional[Any] = None,
    requested_end_date: Optional[Any] = None,
    extracted_at: Optional[Any] = None,
    notes: Optional[str] = None,
) -> int:
    """
    Inserts a SKIPPED_DUPLICATE row into macro_data.load_audit and returns the
    generated load_id.

    This is useful when the ingestion pipeline detects that the current parquet
    artifact hash matches the latest successfully ingested artifact for the same
    playbook, so no market_data_daily reload is required.
    """
    metadata = MetaData(schema="macro_data")
    table = Table("load_audit", metadata, autoload_with=engine)

    record = {
        "playbook_name": playbook_name,
        "playbook_version": playbook_version,
        "source_file_name": source_file_name,
        "source_file_hash": source_file_hash,
        "dataset_name": dataset_name,
        "requested_start_date": requested_start_date,
        "requested_end_date": requested_end_date,
        "extracted_at": extracted_at,
        "status": "SKIPPED_DUPLICATE",
        "notes": notes or "Skipped ingestion because source_file_hash matches the latest successful load for this playbook.",
    }

    stmt = insert(table).values(record).returning(table.c.load_id)

    with engine.begin() as conn:
        load_id = conn.execute(stmt).scalar_one()

    print(f"[DB] Inserted skipped-duplicate load_audit row with load_id={load_id}.")
    return load_id


# ==============================================================================
# DYNAMIC DAILY MARKET DATA (THE 'WHAT')
# ==============================================================================
def upsert_market_data_daily(
    engine,
    df: pd.DataFrame,
    instrument_id_map: Optional[Dict[str, int]] = None,
    load_id: Optional[int] = None,
):
    """
    Bulk upsert into `macro_data.market_data_daily`.

    Supported input shapes:
    1. DataFrame already contains `instrument_id`
    2. DataFrame contains `ticker` or `vendor_ticker`, and you pass `instrument_id_map`

    If a record for (trade_date, instrument_id, field_name) exists, it updates:
    - field_value
    - load_id
    """
    if df.empty:
        print("[DB] DataFrame is empty. Skipping market_data_daily upsert.")
        return

    df_clean = df.copy()

    df_clean["trade_date"] = pd.to_datetime(df_clean["trade_date"]).dt.strftime("%Y-%m-%d")
    df_clean["field_name"] = df_clean["field_name"].astype(str).str.upper()
    df_clean = df_clean.dropna(subset=["field_value"])

    if "instrument_id" not in df_clean.columns:
        ticker_col = None
        if "vendor_ticker" in df_clean.columns:
            ticker_col = "vendor_ticker"
        elif "ticker" in df_clean.columns:
            ticker_col = "ticker"

        if ticker_col is None:
            raise ValueError(
                "DataFrame must contain either `instrument_id` or `ticker`/`vendor_ticker`."
            )
        if not instrument_id_map:
            raise ValueError(
                "instrument_id_map is required when DataFrame does not already contain `instrument_id`."
            )

        df_clean["instrument_id"] = df_clean[ticker_col].map(instrument_id_map)
        missing = df_clean[df_clean["instrument_id"].isna()][ticker_col].dropna().unique().tolist()
        if missing:
            raise ValueError(
                f"Missing instrument_id mappings for {len(missing)} tickers: {missing[:10]}"
            )

    if load_id is not None:
        df_clean["load_id"] = load_id

    target_columns = ["trade_date", "instrument_id", "field_name", "field_value"]
    if "load_id" in df_clean.columns:
        target_columns.append("load_id")

    records = df_clean[target_columns].to_dict(orient="records")

    metadata = MetaData(schema="macro_data")
    table = Table("market_data_daily", metadata, autoload_with=engine)

    stmt = insert(table).values(records)
    update_set = {"field_value": stmt.excluded.field_value}
    if "load_id" in target_columns:
        update_set["load_id"] = stmt.excluded.load_id

    stmt = stmt.on_conflict_do_update(
        index_elements=["trade_date", "instrument_id", "field_name"],
        set_=update_set,
    )

    with engine.begin() as conn:
        conn.execute(stmt)

    print(f"[DB] Successfully upserted {len(records)} rows into market_data_daily.")


# ==============================================================================
# BACKWARD-COMPATIBILITY WRAPPER
# ==============================================================================
def upsert_market_data_timeseries(
    engine,
    df: pd.DataFrame,
    instrument_id_map: Optional[Dict[str, int]] = None,
    load_id: Optional[int] = None,
):
    """
    Backward-compatible wrapper so older ingestion code can still call the old
    function name while writing into the new `market_data_daily` table.
    """
    return upsert_market_data_daily(
        engine=engine,
        df=df,
        instrument_id_map=instrument_id_map,
        load_id=load_id,
    )
