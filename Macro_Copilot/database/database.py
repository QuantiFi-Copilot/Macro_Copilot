import os
from contextlib import contextmanager, nullcontext
from datetime import datetime, timedelta
from typing import Any, Dict, Iterator, List, Optional, Union

import pandas as pd
from sqlalchemy import MetaData, Table, create_engine, func, select, text
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.engine import Connection, Engine


# A "connectable" — either an Engine or a Connection.  Functions that
# touch the DB accept either:
#
#   - An Engine, in which case the function manages its own transaction
#     internally (the legacy behavior).  Suitable for one-shot mutations
#     that do not need to compose with sibling mutations under one txn.
#
#   - A Connection, in which case the function assumes the caller has
#     already opened a transaction and DOES NOT begin/commit one itself.
#     This is what makes it safe to call multiple mutating helpers under
#     one ``with engine.begin() as conn:`` block and get a single
#     atomic transaction.
#
# See ``docs/technical_debt.md`` item #1 (closed by Phase 0 PR 3) and the
# call site in ``ingestion/ingest_parquet.py`` for the canonical example.
Connectable = Union[Engine, Connection]


@contextmanager
def _txn(connectable: Connectable) -> Iterator[Connection]:
    """Yield a Connection inside a transaction, regardless of which type
    of connectable the caller passed.

    Semantics:

      - If ``connectable`` is an ``Engine``: opens ``engine.begin()`` and
        yields the connection.  Exiting the ``with`` block commits on
        success, rolls back on exception (standard SQLAlchemy 2.0
        ``engine.begin()`` behavior).

      - If ``connectable`` is a ``Connection``: yields it as-is via
        ``nullcontext``.  No begin / commit is attempted because the
        caller already owns the transaction; their outer ``engine.begin()``
        block will commit or roll back the whole thing.

    This lets every function in this module accept either an Engine
    (one-shot, self-managed) or a Connection (composed under a caller-
    owned transaction) with one shared idiom.
    """
    if isinstance(connectable, Engine):
        with connectable.begin() as conn:
            yield conn
    else:
        # Connection branch: do NOT start a transaction.  The caller's
        # outer ``engine.begin()`` (or equivalent) owns the transaction
        # boundary; our job is to USE their connection, not to fork one.
        with nullcontext(connectable) as conn:
            yield conn


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
        # Cash-bond identity (ADR 0003). NULL for non-cash-bond instruments;
        # an unknown field never reaches here — it lands in `attributes`.
        "cusip": rec.get("cusip"),
        "isin": rec.get("isin"),
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
def upsert_instrument_master(
    connectable: Connectable, records: List[Dict[str, Any]]
) -> Dict[str, int]:
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

    Connection contract
    -------------------
    Accepts either an :class:`Engine` (self-managed transaction, the legacy
    behaviour — unchanged for every existing caller) or a :class:`Connection`
    (caller-managed transaction). Passing a Connection lets this upsert compose
    atomically with sibling mutations under one ``with engine.begin() as conn:``
    block — the OTR-resolution ingestion branch needs instrument_master +
    otr_history written in a single transaction. See :func:`_txn` for the
    dispatch rule; this brings the helper in line with every other mutating
    writer in this module.
    """
    if not records:
        return {}

    metadata = MetaData(schema="macro_data")
    table = Table("instrument_master", metadata, autoload_with=connectable)

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
        "cusip": stmt.excluded.cusip,
        "isin": stmt.excluded.isin,
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

    with _txn(connectable) as conn:
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


def update_load_audit_status(
    connectable: Connectable,
    load_id: int,
    status: str,
    notes: Optional[str] = None,
) -> None:
    """
    Update the status (and optionally notes) of an existing load_audit row.

    Used to transition a row from RUNNING → SUCCESS or RUNNING → FAILED
    after the destructive mutation phase completes or fails.

    Connection contract
    -------------------
    Accepts either an :class:`Engine` (legacy callers; function manages
    its own transaction) or a :class:`Connection` (composed under a
    caller-owned transaction — typical for the critical
    delete + upsert + audit-flip section in
    ``ingestion/ingest_parquet.py``).  See ``_txn`` for the dispatch rule.

    Closes ``docs/technical_debt.md`` item #1 for this function.
    """
    metadata = MetaData(schema="macro_data")
    table = Table("load_audit", metadata, autoload_with=connectable)

    update_values: Dict[str, Any] = {"status": status}
    if notes is not None:
        update_values["notes"] = notes

    stmt = table.update().where(table.c.load_id == load_id).values(**update_values)

    with _txn(connectable) as conn:
        conn.execute(stmt)

    print(f"[DB] Updated load_audit row load_id={load_id} → status={status}.")


def count_instruments_in_load(engine, load_id: int) -> int:
    """
    Count distinct instrument_ids in market_data_daily for a specific load.

    Used by the ingestion sanity gate to compare the previous successful
    load's instrument count against the incoming parquet's instrument count
    before performing a destructive delete.

    Returns 0 if no rows exist for the given load_id.
    """
    metadata = MetaData(schema="macro_data")
    table = Table("market_data_daily", metadata, autoload_with=engine)

    stmt = (
        select(func.count(table.c.instrument_id.distinct()))
        .where(table.c.load_id == load_id)
    )

    with engine.begin() as conn:
        return conn.execute(stmt).scalar() or 0


# ==============================================================================
# DYNAMIC DAILY MARKET DATA (THE 'WHAT')
# ==============================================================================
# Default batch size for the market_data_daily upsert.  market_data_daily
# rows bind up to 5 columns each (trade_date, instrument_id, field_name,
# field_value, load_id), so a 10_000-row batch is ~50_000 bound parameters
# — comfortably under PostgreSQL's ~65_535-parameter-per-statement protocol
# limit.  See ``upsert_market_data_daily`` for the full rationale.
DEFAULT_MARKET_DATA_UPSERT_BATCH_SIZE = 10_000


def upsert_market_data_daily(
    connectable: Connectable,
    df: pd.DataFrame,
    instrument_id_map: Optional[Dict[str, int]] = None,
    load_id: Optional[int] = None,
    batch_size: int = DEFAULT_MARKET_DATA_UPSERT_BATCH_SIZE,
):
    """
    Bulk upsert into `macro_data.market_data_daily`, in bounded batches.

    Supported input shapes:
    1. DataFrame already contains `instrument_id`
    2. DataFrame contains `ticker` or `vendor_ticker`, and you pass `instrument_id_map`

    If a record for (trade_date, instrument_id, field_name) exists, it updates:
    - field_value
    - load_id

    Batching
    --------
    A large historical parquet (e.g. a full sovereign reload of >1M rows)
    cannot be upserted as a SINGLE ``INSERT ... ON CONFLICT`` statement:
    one statement with N rows binds N × ~5 parameters, which both blows
    past PostgreSQL's ~65_535-parameter-per-statement protocol limit AND
    builds an enormous in-memory statement + record list that can OOM-kill
    the ingestion worker (the worker dies with no Python exception — the
    OS kills it).

    This function therefore upserts in batches of ``batch_size`` rows
    (default 10_000 → ~50_000 bound parameters).  Each batch's record
    list is materialised only for that slice — the full DataFrame is never
    expanded into one giant list of dicts.

    **All batches execute on a single connection inside ONE transaction.**
    The function does NOT commit per batch.  ``_txn(connectable)`` is
    opened once around the whole batch loop:

      - If ``connectable`` is an :class:`Engine`: one ``engine.begin()``
        wraps every batch; the helper call is one atomic transaction.
      - If ``connectable`` is a :class:`Connection`: every batch composes
        into the caller's transaction (the helper opens no transaction of
        its own).  This is what preserves the ingester's critical-section
        contract — ``delete prior rows + ALL upsert batches + audit-flip``
        commit together, or roll back together.  Committing per batch
        here would reintroduce the exact corruption mode
        ``docs/technical_debt.md`` item #1 closed (old rows deleted,
        only some new batches committed).

    Connection contract
    -------------------
    Accepts either an :class:`Engine` (legacy callers; function manages
    its own transaction) or a :class:`Connection` (composed under a
    caller-owned transaction — typical for the critical
    delete + upsert + audit-flip section in
    ``ingestion/ingest_parquet.py``).  See ``_txn`` for the dispatch rule.

    Closes ``docs/technical_debt.md`` item #1 for this function.
    """
    if df.empty:
        print("[DB] DataFrame is empty. Skipping market_data_daily upsert.")
        return

    if batch_size < 1:
        raise ValueError(f"batch_size must be >= 1, got {batch_size}.")

    df_clean = df.copy()

    df_clean["trade_date"] = pd.to_datetime(df_clean["trade_date"]).dt.strftime("%Y-%m-%d")
    df_clean["field_name"] = df_clean["field_name"].astype(str).str.upper()
    df_clean = df_clean.dropna(subset=["field_value"])

    if df_clean.empty:
        print(
            "[DB] All rows had a null field_value after cleaning. "
            "Skipping market_data_daily upsert."
        )
        return

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

    df_target = df_clean[target_columns]
    total_rows = len(df_target)
    num_batches = (total_rows + batch_size - 1) // batch_size

    metadata = MetaData(schema="macro_data")
    table = Table("market_data_daily", metadata, autoload_with=connectable)

    # Single transaction around the WHOLE batch loop.  Do not move the
    # ``with _txn`` inside the loop — that would commit per batch and break
    # the atomic delete+upsert+audit-flip contract.
    with _txn(connectable) as conn:
        for batch_index in range(num_batches):
            start = batch_index * batch_size
            end = min(start + batch_size, total_rows)

            # Materialise records for THIS slice only — never the whole frame.
            batch_records = df_target.iloc[start:end].to_dict(orient="records")

            stmt = insert(table).values(batch_records)
            update_set = {"field_value": stmt.excluded.field_value}
            if "load_id" in target_columns:
                update_set["load_id"] = stmt.excluded.load_id
            stmt = stmt.on_conflict_do_update(
                index_elements=["trade_date", "instrument_id", "field_name"],
                set_=update_set,
            )

            conn.execute(stmt)
            print(
                f"[DB] Upserted market_data_daily batch {batch_index + 1}/{num_batches} "
                f"rows {start}-{end - 1}"
            )

    print(f"[DB] Successfully upserted {total_rows} rows into market_data_daily.")


# ==============================================================================
# BACKWARD-COMPATIBILITY WRAPPER
# ==============================================================================
def upsert_market_data_timeseries(
    connectable: Connectable,
    df: pd.DataFrame,
    instrument_id_map: Optional[Dict[str, int]] = None,
    load_id: Optional[int] = None,
    batch_size: int = DEFAULT_MARKET_DATA_UPSERT_BATCH_SIZE,
):
    """
    Backward-compatible wrapper so older ingestion code can still call the old
    function name while writing into the new `market_data_daily` table.

    Accepts either an Engine or a Connection — same contract as
    :func:`upsert_market_data_daily`, including the ``batch_size`` parameter.
    """
    return upsert_market_data_daily(
        connectable=connectable,
        df=df,
        instrument_id_map=instrument_id_map,
        load_id=load_id,
        batch_size=batch_size,
    )


# ==============================================================================
# INSTRUMENT METADATA HISTORY (SCD2)
# ==============================================================================
#
# Closes ``docs/technical_debt.md`` item #2 from the substrate side.
# ADR: ``docs_revamped/05_decisions/0001-instrument-metadata-history.md``.
#
# These helpers do NOT modify ``upsert_instrument_master`` or
# ``ingest_parquet.py``. The live ingestion path is unchanged in Step 0.
# History rows are written by explicit callers:
#
#   * The Step-1.1 backfill script populates effective-dated rows for all
#     rolling tickers from Bloomberg generic-ticker-day metadata.
#   * A later PR may wire ``ingest_parquet.run_ingestion_pipeline`` to call
#     ``upsert_instrument_metadata_history`` per-rolling-ticker on each
#     ingestion (append-on-change). That wiring is deliberately deferred
#     until backfill has populated the table and verified the read path.
#
# Until either of those runs, the table stays empty and the enriched view's
# COALESCE falls through to ``instrument_master`` — byte-identical to the
# pre-history view output.

_HISTORY_VALUE_COLUMNS = (
    "contract_code",
    "expiry_date",
    "maturity_date",
    "security_name",
    "settlement_date",
    "accrual_start_date",
    "accrual_end_date",
    "tick_size",
    "tick_value",
    "contract_size",
    "exchange_code",
    "underlying_ticker",
    "attributes",
    "load_id",
)


def _normalize_history_record(
    record: Dict[str, Any],
    ticker_to_id: Dict[str, int],
    vendor_default: str,
) -> Dict[str, Any]:
    """
    Internal: turn a caller-supplied record into a clean dict mapping the
    columns of ``macro_data.instrument_metadata_history``.

    A record must carry either ``instrument_id`` or ``vendor_ticker``.
    ``effective_from`` is required; ``effective_to`` defaults to NULL
    ("currently in effect"). Every other column is optional.

    The returned dict has **uniform keys across every record**: every
    column in ``_HISTORY_VALUE_COLUMNS`` plus ``instrument_id``,
    ``effective_from``, ``effective_to`` is always present, with
    ``None`` for absent values. SQLAlchemy bulk ``insert(...).values([
    ...])`` generates the column list from the first row, so dictionaries
    with different key sets across rows produce inconsistent INSERT
    statements; normalising every row to the same key set avoids that
    class of bug.
    """
    instrument_id = record.get("instrument_id")
    if instrument_id is None:
        vt = record.get("vendor_ticker")
        if not vt:
            raise ValueError(
                "instrument_metadata_history record needs either "
                "`instrument_id` or `vendor_ticker`."
            )
        instrument_id = ticker_to_id.get(vt)
        if instrument_id is None:
            raise ValueError(
                f"instrument_metadata_history: vendor_ticker {vt!r} "
                f"(vendor={vendor_default!r}) is not present in "
                "instrument_master. Upsert the master row first."
            )

    effective_from = record.get("effective_from")
    if effective_from is None:
        raise ValueError(
            "instrument_metadata_history record requires `effective_from`."
        )

    row: Dict[str, Any] = {
        "instrument_id": int(instrument_id),
        "effective_from": effective_from,
        "effective_to": record.get("effective_to"),  # NULL allowed
    }
    # Every history column always present, defaulting to None — uniform key
    # set across rows so SQLAlchemy bulk insert generates a single coherent
    # column list. (Without this, two records with disjoint optional fields
    # would produce ``ProgrammingError`` or silently dropped values.)
    for col in _HISTORY_VALUE_COLUMNS:
        row[col] = record.get(col)
    return row


# Default batch size for the instrument_metadata_history upsert.
# instrument_metadata_history rows bind ~17 columns each, so a 2_000-row
# batch is ~34_000 bound parameters — comfortably under PostgreSQL's
# ~65_535-parameter-per-statement protocol limit. The default is lower
# than ``DEFAULT_MARKET_DATA_UPSERT_BATCH_SIZE`` (10_000) because these
# rows are ~3x wider than market_data_daily rows.
DEFAULT_METADATA_HISTORY_UPSERT_BATCH_SIZE = 2_000


def upsert_instrument_metadata_history(
    connectable: Connectable,
    records: List[Dict[str, Any]],
    batch_size: int = DEFAULT_METADATA_HISTORY_UPSERT_BATCH_SIZE,
) -> int:
    """
    Append-on-change writer for ``macro_data.instrument_metadata_history``.

    Each record MUST carry:
      - ``instrument_id`` (int) **or** ``vendor_ticker`` (str)
      - ``effective_from`` (date)

    Optional columns (each becomes a typed column on the history row):
      ``effective_to`` (NULL = currently in effect),
      ``contract_code``, ``expiry_date``, ``maturity_date``, ``security_name``,
      ``settlement_date``, ``accrual_start_date``, ``accrual_end_date``,
      ``tick_size``, ``tick_value``, ``contract_size``, ``exchange_code``,
      ``underlying_ticker``, ``attributes`` (JSONB), ``load_id``.

    Behavior:
      * Idempotent on ``(instrument_id, effective_from)`` via
        ``ON CONFLICT DO UPDATE`` — re-running a backfill is safe and
        updates fields in place rather than duplicating rows.
      * Does NOT close prior open windows. Callers wanting append-on-change
        semantics (close prior open row's effective_to to new.effective_from
        − 1) invoke :func:`close_open_metadata_window` explicitly. Keeping
        the two steps separate makes idempotent re-runs cheaper to reason
        about: a backfill can be retried without producing window-collapse
        artefacts.

    Batching
    --------
    A large backfill (every rolling-contract chain enumerated at once)
    can produce enough records that a single ``INSERT ... ON CONFLICT``
    would bind more than PostgreSQL's ~65_535-parameter-per-statement
    protocol limit — each row binds ~17 columns. The upsert is therefore
    issued in batches of ``batch_size`` records (default 2_000 →
    ~34_000 bound parameters).

    **All batches execute on a single connection inside ONE transaction.**
    ``_txn(connectable)`` is opened once around the batch loop and the
    function never commits per batch, so the caller's atomic-composition
    contract is preserved exactly — e.g. the ingester's metadata-history
    critical section (``close_open_metadata_window`` per instrument +
    this upsert + audit-flip) still commits atomically or rolls back
    together. See :func:`upsert_market_data_daily` for the same pattern.

    Connection contract: same as the other helpers in this module —
    accepts either an :class:`Engine` (self-managed transaction) or a
    :class:`Connection` (caller-managed transaction). See :func:`_txn`.
    """
    if not records:
        return 0

    if batch_size < 1:
        raise ValueError(f"batch_size must be >= 1, got {batch_size}.")

    metadata = MetaData(schema="macro_data")
    history = Table("instrument_metadata_history", metadata, autoload_with=connectable)
    master = Table("instrument_master", metadata, autoload_with=connectable)

    # SQLAlchemy reflects a Postgres JSONB column with ``none_as_null=False``
    # (its default). Under that default the JSONB bind processor renders a
    # Python ``None`` as the JSON ``'null'`` literal — a real JSONB value —
    # NOT a SQL ``NULL``. For metadata-history rows whose every static field
    # maps to a typed column, ``attributes`` is always ``None``; left as-is,
    # every row would persist a JSONB ``'null'`` (``attributes IS NOT NULL``
    # would be true, the GIN index would carry a useless entry per row, and
    # ``jsonb_typeof`` would report ``'null'``). Overriding the reflected
    # column's type to ``none_as_null=True`` makes a Python ``None`` persist
    # as a genuine SQL ``NULL`` while a real dict still serialises to JSONB
    # normally. Reassigning ``.type`` before the INSERT is compiled is what
    # makes the new bind processor take effect.
    history.c.attributes.type = JSONB(none_as_null=True)

    # Resolve any vendor_ticker → instrument_id in one round-trip. Records
    # that already carry instrument_id need no lookup. Records with neither
    # raise from _normalize_history_record below.
    vendor_default = "BLOOMBERG"
    for r in records:
        if r.get("vendor"):
            vendor_default = r["vendor"]
            break

    tickers_needing_resolution = sorted({
        r["vendor_ticker"]
        for r in records
        if r.get("instrument_id") is None and r.get("vendor_ticker")
    })

    ticker_to_id: Dict[str, int] = {}
    if tickers_needing_resolution:
        lookup_stmt = (
            select(master.c.vendor_ticker, master.c.instrument_id)
            .where(master.c.vendor == vendor_default)
            .where(master.c.vendor_ticker.in_(tickers_needing_resolution))
        )
        with _txn(connectable) as conn:
            rows = conn.execute(lookup_stmt).fetchall()
        ticker_to_id = {row.vendor_ticker: row.instrument_id for row in rows}

    clean: List[Dict[str, Any]] = [
        _normalize_history_record(r, ticker_to_id, vendor_default)
        for r in records
    ]
    if not clean:
        return 0

    # Column names whose values are refreshed on an
    # (instrument_id, effective_from) conflict — constant across batches,
    # so computed once here and reused per batch below.
    update_col_names = [
        c.name
        for c in history.c
        if c.name
        not in {
            "metadata_history_id",
            "instrument_id",
            "effective_from",
            "created_at",
        }
    ]

    total_rows = len(clean)
    num_batches = (total_rows + batch_size - 1) // batch_size
    affected = 0

    # Single transaction around the WHOLE batch loop. Do not move the
    # ``with _txn`` inside the loop — that would commit per batch and
    # break the caller's atomic delete/close + upsert + audit-flip
    # contract.
    with _txn(connectable) as conn:
        for batch_index in range(num_batches):
            start = batch_index * batch_size
            end = min(start + batch_size, total_rows)
            batch_records = clean[start:end]

            stmt = insert(history).values(batch_records)
            update_set = {
                name: stmt.excluded[name] for name in update_col_names
            }
            stmt = stmt.on_conflict_do_update(
                index_elements=["instrument_id", "effective_from"],
                set_=update_set,
            )

            result = conn.execute(stmt)
            affected += result.rowcount or 0
            print(
                f"[DB] Upserted instrument_metadata_history batch "
                f"{batch_index + 1}/{num_batches} rows {start}-{end - 1}"
            )

    print(f"[DB] Upserted {affected} rows into instrument_metadata_history.")
    return affected


def close_open_metadata_window(
    connectable: Connectable,
    instrument_id: int,
    new_effective_from: Any,
) -> int:
    """
    Close the currently-open (``effective_to IS NULL``) history row for an
    instrument by setting its ``effective_to`` to ``new_effective_from - 1
    day``.

    Used by the backfill / live-append path when introducing a new
    effective window for an instrument: the prior open window's terminal
    date becomes the day before the new window begins. Only the most
    recent open row is closed (matching ``effective_from < new``); rows
    already closed are not touched.

    Returns the number of rows updated (0 if no open prior window existed,
    1 in the typical case).
    """
    new_from = pd.to_datetime(new_effective_from).date()
    close_to = new_from - timedelta(days=1)

    metadata = MetaData(schema="macro_data")
    history = Table("instrument_metadata_history", metadata, autoload_with=connectable)

    stmt = (
        history.update()
        .where(history.c.instrument_id == int(instrument_id))
        .where(history.c.effective_to.is_(None))
        .where(history.c.effective_from < new_from)
        .values(effective_to=close_to)
    )

    with _txn(connectable) as conn:
        result = conn.execute(stmt)
    return result.rowcount or 0


_METADATA_AT_SQL = text(
    """
    SELECT
        i.instrument_id,
        i.vendor,
        i.vendor_ticker,
        i.asset_class,
        i.instrument_type,
        i.curve_family,
        i.country,
        i.currency,
        i.tenor,
        i.underlying_index,
        i.is_rolling_contract,
        i.is_active,
        COALESCE(hist.contract_code,  i.contract_code)  AS contract_code,
        COALESCE(hist.expiry_date,    i.expiry_date)    AS expiry_date,
        COALESCE(hist.maturity_date,  i.maturity_date)  AS maturity_date,
        hist.security_name,
        hist.settlement_date,
        hist.accrual_start_date,
        hist.accrual_end_date,
        hist.tick_size,
        hist.tick_value,
        hist.contract_size,
        hist.exchange_code,
        hist.underlying_ticker,
        hist.effective_from,
        hist.effective_to,
        hist.attributes AS history_attributes,
        i.attributes    AS master_attributes
    FROM macro_data.instrument_master i
    LEFT JOIN LATERAL (
        SELECT h.*
        FROM macro_data.instrument_metadata_history h
        WHERE i.is_rolling_contract = TRUE
          AND h.instrument_id = i.instrument_id
          AND h.effective_from <= :as_of_date
          AND (h.effective_to IS NULL OR h.effective_to >= :as_of_date)
        ORDER BY h.effective_from DESC
        LIMIT 1
    ) hist ON TRUE
    WHERE i.instrument_id = :instrument_id
    """
)


def get_instrument_metadata_at(
    engine: Engine,
    instrument_id: int,
    as_of_date: Any,
) -> Optional[Dict[str, Any]]:
    """
    Point-in-time metadata getter for one instrument.

    Returns a single dict carrying the metadata in effect on
    ``as_of_date``, with the SCD2 history overlay applied:

      * For a rolling-contract ticker whose history row covers
        ``as_of_date``: the history row's ``contract_code`` /
        ``expiry_date`` / ``maturity_date`` win, plus every
        history-only typed column (``security_name``, ``tick_size``,
        accrual window, etc.) is populated.
      * For a non-rolling ticker, or a rolling ticker outside any
        backfilled window: the ``instrument_master`` row's values win
        via COALESCE; the history-only typed columns are NULL.

    ``master_attributes`` and ``history_attributes`` are returned as
    separate JSONB blobs so the caller can decide a merge policy
    (typically: ``history_attributes`` wins on conflict, else master).

    Returns ``None`` only if no ``instrument_master`` row exists for the
    given ``instrument_id``.
    """
    as_of = pd.to_datetime(as_of_date).date().isoformat()
    with engine.connect() as conn:
        row = (
            conn.execute(
                _METADATA_AT_SQL,
                {"instrument_id": int(instrument_id), "as_of_date": as_of},
            )
            .mappings()
            .first()
        )
    return dict(row) if row else None


# ==============================================================================
# ON-THE-RUN (OTR) HISTORY HELPERS (ADR 0003)
#
# Substrate for macro_data.otr_history — the SCD2 table that tracks which
# individual cash sovereign bond was on-the-run for each (country, tenor) slot
# over time. This trio mirrors the instrument_metadata_history trio
# (upsert_instrument_metadata_history / close_open_metadata_window /
# get_instrument_metadata_at), re-keyed from instrument_id to the
# (country, tenor) slot. A3 lands these; the Step-2 data PR (A4) is the first
# caller — A3 itself populates no OTR data.
# ==============================================================================

# Optional otr_history value columns, defaulted to None on every record so the
# bulk insert generates a single coherent column list (see _normalize_otr_record).
_OTR_VALUE_COLUMNS = (
    "effective_to",
    "attributes",
    "load_id",
)


def _normalize_otr_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """
    Internal: turn a caller-supplied OTR record into a clean dict mapping the
    columns of ``macro_data.otr_history``.

    A record MUST carry ``country``, ``tenor``, ``effective_from`` and
    ``otr_instrument_id`` (the ``instrument_master`` row that was on-the-run
    during the window). ``effective_to`` defaults to NULL ("currently
    on-the-run"); ``attributes`` and ``load_id`` are optional.

    The returned dict has **uniform keys across every record** — ``country``,
    ``tenor``, ``effective_from``, ``otr_instrument_id`` plus every column in
    ``_OTR_VALUE_COLUMNS``, with ``None`` for absent values. SQLAlchemy bulk
    ``insert(...).values([...])`` derives the column list from the first row,
    so a uniform key set avoids inconsistent INSERT statements. Mirrors
    :func:`_normalize_history_record`.
    """
    country = record.get("country")
    tenor = record.get("tenor")
    effective_from = record.get("effective_from")
    otr_instrument_id = record.get("otr_instrument_id")

    if not country:
        raise ValueError("otr_history record requires `country`.")
    if not tenor:
        raise ValueError("otr_history record requires `tenor`.")
    if effective_from is None:
        raise ValueError("otr_history record requires `effective_from`.")
    if otr_instrument_id is None:
        raise ValueError(
            "otr_history record requires `otr_instrument_id` — resolve the "
            "on-the-run bond's CUSIP/ticker to an instrument_id before calling."
        )

    row: Dict[str, Any] = {
        "country": str(country),
        "tenor": str(tenor),
        "effective_from": effective_from,
        "otr_instrument_id": int(otr_instrument_id),
    }
    for col in _OTR_VALUE_COLUMNS:
        row[col] = record.get(col)
    return row


def upsert_otr_history(
    connectable: Connectable,
    records: List[Dict[str, Any]],
) -> int:
    """
    Append-on-change writer for ``macro_data.otr_history``.

    Each record MUST carry:
      - ``country`` (str), ``tenor`` (str)
      - ``effective_from`` (date)
      - ``otr_instrument_id`` (int) — the ``instrument_master`` row that was
        on-the-run for the slot during this window.

    Optional columns: ``effective_to`` (NULL = currently on-the-run),
    ``attributes`` (JSONB), ``load_id``.

    Behavior:
      * Idempotent on ``(country, tenor, effective_from)`` via
        ``ON CONFLICT DO UPDATE`` — re-running a backfill is safe and updates
        fields in place rather than duplicating rows.
      * Does NOT close prior open windows. Callers wanting append-on-change
        semantics call :func:`close_open_otr_window` explicitly first. Keeping
        the two steps separate mirrors ``upsert_instrument_metadata_history`` /
        ``close_open_metadata_window`` and makes idempotent re-runs cheap to
        reason about.

    Connection contract: accepts either an :class:`Engine` (self-managed
    transaction) or a :class:`Connection` (caller-managed). See :func:`_txn`.
    """
    if not records:
        return 0

    metadata = MetaData(schema="macro_data")
    otr = Table("otr_history", metadata, autoload_with=connectable)

    # Reflection types the JSONB column with ``none_as_null=False`` (SQLAlchemy
    # default), under which a Python ``None`` binds as the JSON ``'null'``
    # literal rather than SQL ``NULL``. Override so an absent attributes blob
    # persists as a genuine SQL ``NULL`` — the same fix applied to
    # ``upsert_instrument_metadata_history``.
    otr.c.attributes.type = JSONB(none_as_null=True)

    clean: List[Dict[str, Any]] = [_normalize_otr_record(r) for r in records]

    stmt = insert(otr).values(clean)
    update_set = {
        c.name: stmt.excluded[c.name]
        for c in otr.c
        if c.name
        not in {
            "otr_history_id",
            "country",
            "tenor",
            "effective_from",
            "created_at",
        }
    }
    stmt = stmt.on_conflict_do_update(
        index_elements=["country", "tenor", "effective_from"],
        set_=update_set,
    )

    with _txn(connectable) as conn:
        result = conn.execute(stmt)
    affected = result.rowcount or 0
    print(f"[DB] Upserted {affected} rows into otr_history.")
    return affected


def close_open_otr_window(
    connectable: Connectable,
    country: str,
    tenor: str,
    new_effective_from: Any,
) -> int:
    """
    Close the currently-open (``effective_to IS NULL``) ``otr_history`` row for
    a ``(country, tenor)`` slot by setting its ``effective_to`` to
    ``new_effective_from - 1 day``.

    Used by the OTR backfill / live-append path when a newly-auctioned bond
    becomes on-the-run for a slot: the prior on-the-run window's terminal date
    becomes the day before the new window begins. Only the most recent open
    row is closed (matching ``effective_from < new``); rows already closed are
    not touched.

    Returns the number of rows updated (0 if no open prior window existed,
    1 in the typical case). Mirrors :func:`close_open_metadata_window`.
    """
    new_from = pd.to_datetime(new_effective_from).date()
    close_to = new_from - timedelta(days=1)

    metadata = MetaData(schema="macro_data")
    otr = Table("otr_history", metadata, autoload_with=connectable)

    stmt = (
        otr.update()
        .where(otr.c.country == str(country))
        .where(otr.c.tenor == str(tenor))
        .where(otr.c.effective_to.is_(None))
        .where(otr.c.effective_from < new_from)
        .values(effective_to=close_to)
    )

    with _txn(connectable) as conn:
        result = conn.execute(stmt)
    return result.rowcount or 0


_OTR_AT_SQL = text(
    """
    SELECT
        o.otr_history_id,
        o.country,
        o.tenor,
        o.effective_from,
        o.effective_to,
        o.otr_instrument_id,
        o.attributes AS otr_attributes,
        i.vendor,
        i.vendor_ticker,
        i.cusip,
        i.isin,
        i.currency,
        i.instrument_type,
        i.maturity_date
    FROM macro_data.otr_history o
    JOIN macro_data.instrument_master i
      ON i.instrument_id = o.otr_instrument_id
    WHERE o.country = :country
      AND o.tenor = :tenor
      AND o.effective_from <= :as_of_date
      AND (o.effective_to IS NULL OR o.effective_to >= :as_of_date)
    ORDER BY o.effective_from DESC
    LIMIT 1
    """
)


def get_otr_at(
    engine: Engine,
    country: str,
    tenor: str,
    as_of_date: Any,
) -> Optional[Dict[str, Any]]:
    """
    Point-in-time on-the-run getter for one ``(country, tenor)`` slot.

    Returns a single dict describing the cash bond that was on-the-run for the
    slot on ``as_of_date`` — the ``otr_history`` window joined to the OTR
    bond's ``instrument_master`` identity (``otr_instrument_id``,
    ``vendor_ticker``, ``cusip``, ``isin``, ``currency``, ``maturity_date``).

    Returns ``None`` (typed ``Optional``, per P6) when no OTR window covers
    ``as_of_date`` for the slot — e.g. the slot has no backfilled history, or
    the date predates the earliest window. The absence is information, not an
    error; callers handle the ``None`` branch explicitly.

    Mirrors :func:`get_instrument_metadata_at`.
    """
    as_of = pd.to_datetime(as_of_date).date().isoformat()
    with engine.connect() as conn:
        row = (
            conn.execute(
                _OTR_AT_SQL,
                {"country": str(country), "tenor": str(tenor), "as_of_date": as_of},
            )
            .mappings()
            .first()
        )
    return dict(row) if row else None


# ==============================================================================
# EVENT CALENDAR HELPERS (ADR 0004)
#
# Substrate for macro_data.event_calendar — one row per macro EVENT (an
# economic release, a central-bank meeting, or a sovereign auction). Events do
# not fit market_data_daily's long (trade_date, instrument_id, field_name,
# value) shape, so they get their own wide table. These helpers follow the
# established _normalize_* / upsert_* / get_* shape and the _txn-Connectable
# contract. B1 lands these; the Step-3 data PR (B2) is the first caller —
# B1 itself ingests no event data.
# ==============================================================================

# The closed set of valid event_category values. event_category is a free
# VARCHAR on the table (consistent with asset_class / instrument_type); this
# tuple is the single code-level source of truth for the valid set (P8/P10).
# Extending it is a deliberate, ADR-recorded decision.
EVENT_CATEGORIES = ("economic_release", "central_bank_meeting", "auction")

# Optional event_calendar value columns, defaulted to None on every record so
# the bulk insert generates a single coherent column list (see
# _normalize_event_record). The four required columns — event_type,
# event_category, country, release_date — are set explicitly, not via this loop.
_EVENT_VALUE_COLUMNS = (
    "currency",
    "central_bank",
    "release_time",
    "period",
    "actual",
    "consensus_median",
    "consensus_high",
    "consensus_low",
    "prior",
    "revised_prior",
    "surprise",
    "surprise_std_dev",
    "high_yield",
    "bid_to_cover",
    "tail_bps",
    "indirect_pct",
    "related_instrument_id",
    "attributes",
    "load_id",
)


def _normalize_event_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """
    Internal: turn a caller-supplied event record into a clean dict mapping the
    columns of ``macro_data.event_calendar``.

    A record MUST carry ``event_type``, ``event_category``, ``country`` and
    ``release_date``. Every other column is optional. ``event_category`` MUST
    be one of :data:`EVENT_CATEGORIES`; a value outside that set raises
    ``ValueError``. ``event_category`` is a **closed family** (P8) — the DB
    column is a free ``VARCHAR`` for schema-consistency with ``asset_class`` /
    ``instrument_type``, and this helper is the sanctioned write path that
    enforces the closed set. An unrecognised category is a genuine
    contract violation, so it raises a typed exception rather than writing
    silently (P6). Extending the set is a deliberate ADR + code change to
    :data:`EVENT_CATEGORIES`.

    The returned dict has **uniform keys across every record** — the four
    required columns plus every column in :data:`_EVENT_VALUE_COLUMNS`, with
    ``None`` for absent values — so SQLAlchemy bulk ``insert(...).values([...])``
    derives one coherent column list. Mirrors :func:`_normalize_otr_record`.
    """
    event_type = record.get("event_type")
    event_category = record.get("event_category")
    country = record.get("country")
    release_date = record.get("release_date")

    if not event_type:
        raise ValueError("event_calendar record requires `event_type`.")
    if not event_category:
        raise ValueError("event_calendar record requires `event_category`.")
    if not country:
        raise ValueError("event_calendar record requires `country`.")
    if release_date is None:
        raise ValueError("event_calendar record requires `release_date`.")

    if event_category not in EVENT_CATEGORIES:
        raise ValueError(
            f"event_calendar record has event_category={event_category!r}, "
            f"which is not one of the closed set EVENT_CATEGORIES "
            f"{EVENT_CATEGORIES}. event_category is a closed family — adding a "
            "value is a deliberate ADR + code change to EVENT_CATEGORIES, "
            "never a silent write."
        )

    row: Dict[str, Any] = {
        "event_type": str(event_type),
        "event_category": str(event_category),
        "country": str(country),
        "release_date": release_date,
    }
    for col in _EVENT_VALUE_COLUMNS:
        row[col] = record.get(col)
    # Foreign-key columns are integers; a parquet round-trip can yield numpy
    # int64 or a string, so cast when present.
    for fk in ("related_instrument_id", "load_id"):
        if row[fk] is not None:
            row[fk] = int(row[fk])
    return row


def upsert_event_calendar(
    connectable: Connectable,
    records: List[Dict[str, Any]],
) -> int:
    """
    Idempotent writer for ``macro_data.event_calendar``.

    Each record MUST carry:
      - ``event_type`` (str), ``event_category`` (str), ``country`` (str)
      - ``release_date`` (date)

    Optional columns: ``currency``, ``central_bank``, ``release_time``,
    ``period``, the economic-release numeric fields (``actual``,
    ``consensus_median``, ``consensus_high``, ``consensus_low``, ``prior``,
    ``revised_prior``, ``surprise``, ``surprise_std_dev``), the auction result
    fields (``high_yield``, ``bid_to_cover``, ``tail_bps``, ``indirect_pct``),
    ``related_instrument_id``, ``attributes`` (JSONB), ``load_id``.

    Behavior:
      * Idempotent on the natural key ``(event_type, country, release_date)``
        via ``ON CONFLICT DO UPDATE`` — re-ingesting the calendar is safe, and
        an auction's result columns fill in on a post-auction re-upsert of the
        same row (ADR 0004 — one row per auction, updated post-auction).

    FULL-ROW UPSERT — caller contract. On a natural-key conflict **every
    non-key column is overwritten from the incoming record.**
    :func:`_normalize_event_record` defaults every omitted optional column to
    ``None``, so a caller that omits a field writes ``NULL`` to it — omitted
    fields are NOT merge-preserved. **Every call MUST therefore pass the
    complete current state of the event.** For an auction whose results arrive
    after the announcement, the results upsert re-sends the schedule fields
    (``release_time``, ``period``, …) alongside the new result fields — it is
    not a partial delta. This matches :func:`upsert_instrument_metadata_history`
    and :func:`upsert_otr_history` (also full-row upserts) and the natural
    extraction flow (re-pull the whole calendar each run; a vendor's
    post-settlement auction record carries both schedule and results).
    ``COALESCE(excluded, existing)`` merge was deliberately rejected: it would
    diverge from the sibling upserts (P3) and would make it impossible to
    correct a wrongly-set field back to ``NULL``.

    Connection contract: accepts either an :class:`Engine` (self-managed
    transaction) or a :class:`Connection` (caller-managed). See :func:`_txn`.
    """
    if not records:
        return 0

    metadata = MetaData(schema="macro_data")
    events = Table("event_calendar", metadata, autoload_with=connectable)

    # Reflection types the JSONB column with ``none_as_null=False`` (SQLAlchemy
    # default), under which a Python ``None`` binds as the JSON ``'null'``
    # literal rather than SQL ``NULL``. Override so an absent attributes blob
    # persists as a genuine SQL ``NULL`` — the same fix applied to
    # ``upsert_instrument_metadata_history`` and ``upsert_otr_history``.
    events.c.attributes.type = JSONB(none_as_null=True)

    clean: List[Dict[str, Any]] = [_normalize_event_record(r) for r in records]

    stmt = insert(events).values(clean)
    update_set = {
        c.name: stmt.excluded[c.name]
        for c in events.c
        if c.name
        not in {
            "event_id",
            "event_type",
            "country",
            "release_date",
            "created_at",
        }
    }
    stmt = stmt.on_conflict_do_update(
        index_elements=["event_type", "country", "release_date"],
        set_=update_set,
    )

    with _txn(connectable) as conn:
        result = conn.execute(stmt)
    affected = result.rowcount or 0
    print(f"[DB] Upserted {affected} rows into event_calendar.")
    return affected


_EVENTS_IN_WINDOW_SQL = text(
    """
    SELECT *
    FROM macro_data.event_calendar
    WHERE event_type = :event_type
      AND release_date >= :start_date
      AND release_date <= :end_date
    ORDER BY release_date, event_id
    """
)


def get_events_in_window(
    engine: Engine,
    event_type: str,
    start_date: Any,
    end_date: Any,
) -> List[Dict[str, Any]]:
    """
    Read the ``event_calendar`` rows of one ``event_type`` whose
    ``release_date`` falls within ``[start_date, end_date]`` (both inclusive),
    ordered by ``release_date``.

    Returns a list of dicts (one per event row); an empty list when no event
    of that type falls in the window — the absence is information, not an
    error, so no exception is raised.

    The window bounds accept anything ``pandas.to_datetime`` parses (ISO
    strings, ``datetime``, ``date``).
    """
    start = pd.to_datetime(start_date).date().isoformat()
    end = pd.to_datetime(end_date).date().isoformat()
    with engine.connect() as conn:
        rows = (
            conn.execute(
                _EVENTS_IN_WINDOW_SQL,
                {
                    "event_type": str(event_type),
                    "start_date": start,
                    "end_date": end,
                },
            )
            .mappings()
            .all()
        )
    return [dict(r) for r in rows]
