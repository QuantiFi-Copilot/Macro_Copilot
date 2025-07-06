# earnings_agent/storage/database.py

import os
from dotenv import load_dotenv, find_dotenv
from sqlalchemy import create_engine, update, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.dialects.postgresql import insert as pg_insert
from typing import List, Dict, Any, Optional
import datetime

# Import all the new models
from earnings_agent.storage.models import (
    Base,
    IngestionJob,
    RawDataAsset,
    JobAssetLink,
    ParsedDocument,
    ValidationResult,
    QuarterlyFundamental,
    CustomKPI
)
# Assumes a central config file for the schema name
# from .config import DB_SCHEMA
DB_SCHEMA = "earnings_data" # Using a placeholder for standalone clarity

# Load environment variables from the root of the project
env_path = find_dotenv()
load_dotenv(env_path, override=True)

# Database configuration from environment variables
DB_USER = os.getenv("EARNINGS_DB_USER", "quantuser")
DB_PASS = os.getenv("EARNINGS_DB_PASSWORD", "myStrongPass")
DB_HOST = os.getenv("EARNINGS_DB_HOST", "tsdb")
DB_PORT = os.getenv("EARNINGS_DB_PORT", "5432")
DB_NAME = os.getenv("EARNINGS_DB_NAME", "quantdata")

DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# Create engine and session factory
engine = create_engine(DATABASE_URL, echo=False, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db():
    """
    Initialize the database by creating all tables in the configured schema.
    """
    with engine.begin() as conn:
        conn.execute(f"CREATE SCHEMA IF NOT EXISTS {DB_SCHEMA}")
    Base.metadata.create_all(bind=engine)


def get_session():
    """
    Return a new SQLAlchemy session.
    """
    return SessionLocal()


# ================================================================================================
# INGESTION STAGE FUNCTIONS
# ================================================================================================

def create_ingestion_jobs(jobs_data: List[Dict[str, Any]]):
    """
    Bulk inserts ingestion jobs into the database.
    If a job with the same unique constraint already exists, it does nothing.
    """
    if not jobs_data:
        return
        
    session = get_session()
    try:
        stmt = pg_insert(IngestionJob).values(jobs_data)
        # Do nothing on conflict to ensure idempotency
        # --- MODIFIED: Added 'consolidation_status' to the index_elements ---
        stmt = stmt.on_conflict_do_nothing(
            index_elements=['ticker', 'fiscal_year', 'quarter', 'source_type', 'consolidation_status', 'ingestion_script_version']
        )
        session.execute(stmt)
        session.commit()
    finally:
        session.close()


def get_jobs_by_status(statuses: List[str]) -> List[IngestionJob]:
    """
    Retrieves all ingestion jobs with a status in the provided list.
    """
    session = get_session()
    try:
        # The query now uses .in_() to check against a list of statuses
        stmt = select(IngestionJob).where(IngestionJob.status.in_(statuses))
        result = session.execute(stmt).scalars().all()
        return result
    finally:
        session.close()


# In database.py
def log_ingestion_success(
    job_id: int, 
    raw_data_hash: str, 
    source_type: str, 
    storage_location: Optional[str] = None, 
    data_content: Optional[Dict] = None,
    source_last_modified: Optional[datetime] = None
):
    """
    Logs a successful ingestion in a single transaction. Now includes metadata.
    """
    session = get_session()
    try:
        asset_values = {
            "raw_data_hash": raw_data_hash,
            "source_type": source_type,
            "storage_location": storage_location,
            "data_content": data_content,
            "source_last_modified": source_last_modified
        }
        asset_stmt = pg_insert(RawDataAsset).values(asset_values)
        
        asset_stmt = asset_stmt.on_conflict_do_nothing(index_elements=['raw_data_hash'])
        session.execute(asset_stmt)
        
        asset_id = session.execute(select(RawDataAsset.asset_id).where(RawDataAsset.raw_data_hash == raw_data_hash)).scalar_one()

        link_stmt = pg_insert(JobAssetLink).values(job_id=job_id, asset_id=asset_id)
        link_stmt = link_stmt.on_conflict_do_nothing(index_elements=['job_id'])
        session.execute(link_stmt)

        job_update_stmt = update(IngestionJob).where(IngestionJob.job_id == job_id).values(status='SUCCESS', failure_reason=None)
        session.execute(job_update_stmt)

        session.commit()
    except Exception as e:
        session.rollback()
        raise e
    finally:
        session.close()


def log_ingestion_failure(job_id: int, status: str, reason: str):
    """
    Updates the status of an IngestionJob to a failed state.
    """
    if status not in ['FETCH_FAILED', 'MISSING_AT_SOURCE']:
        raise ValueError("Status must be one of 'FETCH_FAILED' or 'MISSING_AT_SOURCE'")

    session = get_session()
    try:
        stmt = update(IngestionJob).where(IngestionJob.job_id == job_id).values(status=status, failure_reason=reason)
        session.execute(stmt)
        session.commit()
    finally:
        session.close()

def get_asset_by_hash(hash_str: str) -> Optional[RawDataAsset]:
    """
    Retrieves a single RawDataAsset object from the database using its hash.
    """
    session = get_session()
    try:
        stmt = select(RawDataAsset).where(RawDataAsset.raw_data_hash == hash_str)
        result = session.execute(stmt).scalar_one_or_none()
        return result
    finally:
        session.close()
# ================================================================================================
# PARSING & VALIDATION STAGE FUNCTIONS
# ================================================================================================

def create_parsed_document(doc_data: Dict[str, Any]):
    """
    Inserts or updates a ParsedDocument. If a document for the same asset_id
    and parser_version exists, it updates the record.
    """
    session = get_session()
    try:
        stmt = pg_insert(ParsedDocument).values(**doc_data)
        update_cols = {
            'parse_status': stmt.excluded.parse_status,
            'error_details': stmt.excluded.error_details,
            'parsed_at': stmt.excluded.parsed_at,
            'content': stmt.excluded.content
        }
        stmt = stmt.on_conflict_do_update(
            index_elements=['asset_id', 'parser_version'],
            set_=update_cols
        )
        session.execute(stmt)
        session.commit()
    finally:
        session.close()


def create_validation_result(val_data: Dict[str, Any]):
    """
    Inserts or updates a ValidationResult. If a result for the same doc_id
    and validation_script_version exists, it updates the record.
    """
    session = get_session()
    try:
        stmt = pg_insert(ValidationResult).values(**val_data)
        update_cols = {
            'status': stmt.excluded.status,
            'summary': stmt.excluded.summary,
            'validated_at': stmt.excluded.validated_at
        }
        stmt = stmt.on_conflict_do_update(
            index_elements=['doc_id', 'validation_script_version'],
            set_=update_cols
        )
        session.execute(stmt)
        session.commit()
    finally:
        session.close()

# Note: Functions for `QuarterlyFundamental` and `CustomKPI` would be added here
# once the reconciliation logic is built. The patterns would be similar (upsert on unique constraints).