# earnings_agent/storage/database.py

import os
from dotenv import load_dotenv, find_dotenv
from sqlalchemy import create_engine, update, select, delete, func 
from sqlalchemy.orm import sessionmaker, joinedload, Session
from sqlalchemy.dialects.postgresql import insert as pg_insert
from typing import List, Dict, Any, Optional
import datetime
from datetime import date, timezone
import logging
from sqlalchemy.pool import Pool

# Import all the new models
from earnings_agent.storage.models import (
    Base,
    IngestionJob,
    RawDataAsset,
    JobAssetLink,
    ParsedDocument,
    # LabelMapping, # NEW: For the normalization cache
    # StagedNormalizedData, # NEW: For the reconciliation staging area
    # QualityEngineResult,
    # QuarterlyFundamental,
    # CustomKPI,
    Classification,
    # UnitReviewQueue,
    CompanyMaster, 
    QualityEngineRun
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

DATABASE_URL = f"postgresql+psycopg://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# --- MODIFICATION START ---
# These are now initialized to None. They will be created on a per-process basis.
_engine = None
_SessionLocal = None

def get_engine():
    """
    Safely creates a new SQLAlchemy engine for the current process if one doesn't exist.
    """
    global _engine
    if _engine is None:
        # The FIX: Add pool_size, max_overflow, and pool_pre_ping
        _engine = create_engine(
            DATABASE_URL,
            echo=False,  # Set to True temporarily for SQL debug logs
            future=True,
            pool_size=5,  # Matches your MAX_WORKERS +1
            max_overflow=2,
            pool_pre_ping=True,  # Pings connections before use to detect closures
            pool_recycle=300,  # Recycle idle connections every 5 min
            pool_reset_on_return='rollback',  # Rolls back any open transactions on return to pool
            pool_timeout=30,  # Wait 30s for a pool connection
            connect_args={
                'connect_timeout': 10,  # 10s timeout for initial connect
                'keepalives': 1,  # Enable TCP keepalives
                'keepalives_idle': 60,  # Send keepalive every 60s if idle
                'keepalives_interval': 10,  # Resend if no ACK in 10s
                'keepalives_count': 5   # Consider dead after 5 failed keepalives
            }
        )
    return _engine

def get_session():
    """
    Return a new SQLAlchemy session, creating a process-local engine and
    session factory if they don't exist. This is safe for multiprocessing.
    """
    global _SessionLocal
    if _SessionLocal is None:
        engine = get_engine()
        _SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return _SessionLocal()

def init_db():
    """
    Initialize the database using the process-local engine.
    """
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(f"CREATE SCHEMA IF NOT EXISTS {DB_SCHEMA}")
    Base.metadata.create_all(bind=engine)
# --- MODIFICATION END ---


# ================================================================================================
# INGESTION STAGE FUNCTIONS (Unchanged)
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
        stmt = stmt.on_conflict_do_nothing(
            index_elements=['ticker', 'fiscal_year', 'quarter', 'source_type', 'consolidation_status', 'ingestion_script_version']
        )
        session.execute(stmt)
        session.commit()
    finally:
        session.close()


def get_jobs_by_status(statuses: List[str], script_version: Optional[str] = None) -> List[IngestionJob]:
    """
    Retrieves all ingestion jobs with a status in the provided list.
    Can optionally filter by a specific script version.
    """
    session = get_session()
    try:
        stmt = select(IngestionJob).where(IngestionJob.status.in_(statuses))
        
        # This new block filters by version if one is provided
        if script_version:
            stmt = stmt.where(IngestionJob.ingestion_script_version == script_version)
            
        result = session.execute(stmt).scalars().all()
        return result
    finally:
        session.close()


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
# PARSING STAGE FUNCTIONS (Unchanged)
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

# # ================================================================================================
# # NORMALIZATION STAGE FUNCTIONS (NEW)
# # ================================================================================================
# def get_label_mapping(raw_label: str, industry: str) -> Optional[LabelMapping]:
#     """
#     Retrieves a single label mapping from the cache using its composite key.
#     """
#     session = get_session()
#     try:
#         # session.get() works with composite keys when passed a tuple
#         return session.get(LabelMapping, (raw_label, industry))
#     finally:
#         session.close()
# def upsert_label_mapping(mapping_data: Dict[str, Any]):
#     """
#     Inserts or updates a label mapping in the cache.
#     The mapping_data dictionary MUST contain 'raw_label' and 'industry'.
#     """
#     session = get_session()
#     try:
#         stmt = pg_insert(LabelMapping).values(**mapping_data)
#         update_cols = {
#             'normalized_label': stmt.excluded.normalized_label,
#             'status': stmt.excluded.status,
#             'last_reviewed_at': stmt.excluded.last_reviewed_at,
#             'reviewed_by': stmt.excluded.reviewed_by
#         }
#         # Update the index_elements to use the new composite key
#         stmt = stmt.on_conflict_do_update(
#             index_elements=['raw_label', 'industry'],
#             set_=update_cols
#         )
#         session.execute(stmt)
#         session.commit()
#     finally:
#         session.close()
# def create_staged_normalized_data(data: Dict[str, Any]):
#     """
#     Inserts a record in the staging table for normalized data.
#     If a record with the same doc_id already exists, it does nothing.
#     This is safer and prevents accidental overwrites.
#     """
#     session = get_session()
#     try:
#         stmt = pg_insert(StagedNormalizedData).values(**data)
#         # --- THIS IS THE FIX ---
#         # Change the conflict action to DO NOTHING. This function's only job
#         # is to create the initial record if it doesn't exist.
#         stmt = stmt.on_conflict_do_nothing(
#             index_elements=['doc_id']
#         )
#         # --- END OF FIX ---
#         session.execute(stmt)
#         session.commit()
#     finally:
#         session.close()
# def get_unprocessed_approved_labels() -> List[LabelMapping]:
#     """
#     Fetches all label mappings that have been approved but not yet processed
#     by the backfill job. This is the "to-do list" for the backfill script.
#     """
#     session = get_session()
#     try:
#         stmt = select(LabelMapping).where(
#             LabelMapping.status == 'APPROVED',
#             LabelMapping.processed == False
#         )
#         return session.execute(stmt).scalars().all()
#     finally:
#         session.close()
# # --- NEW FUNCTION 2 ---
# def mark_labels_as_processed(raw_labels: List[str]):
#     """
#     Marks a batch of approved labels as processed after the backfill
#     job has successfully run for them.
#     """
#     if not raw_labels:
#         return

#     session = get_session()
#     try:
#         stmt = update(LabelMapping).where(
#             LabelMapping.raw_label.in_(raw_labels)
#         ).values(
#             processed=True
#         )
#         session.execute(stmt)
#         session.commit()
#     except Exception as e:
#         session.rollback()
#         raise e
#     finally:
#         session.close()
# def fetch_pending_label_reviews() -> List[LabelMapping]:
#     """
#     Queries the database for all label mappings with 'PENDING_REVIEW' status.
#     """
#     session = get_session()
#     try:
#         return session.query(LabelMapping).filter(
#             LabelMapping.status == 'PENDING_REVIEW'
#         ).order_by(LabelMapping.created_at.desc()).all()
#     finally:
#         session.close()
# # Add `new_label` as an optional parameter
# def update_label_mapping_status(raw_label: str, industry: str, new_status: str, new_label: str = None, reviewer: str = "human_reviewer"):
#     """
#     Updates the status and optionally the normalized_label of a mapping.
#     """
#     session = get_session()
#     try:
#         mapping_to_update = session.get(LabelMapping, (raw_label, industry))
#         if mapping_to_update:
#             mapping_to_update.status = new_status
#             # --- CORRECTED LINE ---
#             mapping_to_update.last_reviewed_at = datetime.datetime.now(timezone.utc)
#             mapping_to_update.reviewed_by = reviewer
#             if new_label is not None:
#                 mapping_to_update.normalized_label = None if new_label.lower() == 'null' or not new_label else new_label
#             session.commit()
#     except Exception as e:
#         session.rollback()
#         raise e
#     finally:
#         session.close()
# def get_docs_pending_statement_normalization() -> List[int]:
#     """
#     Return all doc_ids that have not yet run through the statement normalizer.
#     """
#     session = get_session()
#     try:
#         stmt = select(StagedNormalizedData.doc_id).where(
#             StagedNormalizedData.statement_normalized == False
#         )
#         return session.execute(stmt).scalars().all()
#     finally:
#         session.close()
# def mark_docs_statement_normalized(doc_ids: List[int]):
#     """
#     Mark the given doc_ids as having completed statement normalization.
#     """
#     if not doc_ids:
#         return
#     session = get_session()
#     try:
#         stmt = (
#             update(StagedNormalizedData)
#             .where(StagedNormalizedData.doc_id.in_(doc_ids))
#             .values(statement_normalized=True)
#         )
#         session.execute(stmt)
#         session.commit()
#     finally:
#         session.close()
# def get_docs_pending_unit_normalization() -> List[int]:
#     """
#     Return all doc_ids ready for unit normalization: statement-normalized but not unit-processed.
#     """
#     session = get_session()
#     try:
#         stmt = select(StagedNormalizedData.doc_id).where(
#             StagedNormalizedData.statement_normalized == True,
#             StagedNormalizedData.unit_review_status == 'PENDING'
#         )
#         return session.execute(stmt).scalars().all()
#     finally:
#         session.close()

# def mark_docs_unit_review_status(doc_ids: List[int], status: str):
#     """
#     Set the unit_review_status for the given doc_ids.
#     status should be one of 'PENDING', 'AUTO_APPROVED', 'PENDING_REVIEW', 'APPROVED'.
#     """
#     if not doc_ids:
#         return
#     session = get_session()
#     try:
#         stmt = (
#             update(StagedNormalizedData)
#             .where(StagedNormalizedData.doc_id.in_(doc_ids))
#             .values(unit_review_status=status)
#         )
#         session.execute(stmt)
#         session.commit()
#     finally:
#         session.close()
# def get_docs_pending_label_normalization() -> List[int]:
#     """
#     Return doc_ids ready for label normalization. This now includes both
#     human-approved and auto-approved documents from the unit normalization phase.
#     """
#     session = get_session()
#     try:
#         stmt = select(StagedNormalizedData.doc_id).where(
#             StagedNormalizedData.unit_review_status.in_(['APPROVED', 'AUTO_APPROVED']),
#             StagedNormalizedData.label_review_status == 'PENDING'
#         )
#         return session.execute(stmt).scalars().all()
#     finally:
#         session.close()
# def get_docs_pending_label_review() -> List[int]:
#     """
#     Return doc_ids that have been scanned for labels and are awaiting human review approval.
#     """
#     session = get_session()
#     try:
#         stmt = select(StagedNormalizedData.doc_id).where(
#             StagedNormalizedData.label_review_status == 'PENDING_REVIEW'
#         )
#         return session.execute(stmt).scalars().all()
#     finally:
#         session.close()

# def mark_docs_label_review_status(doc_ids: List[int], status: str):
#     """
#     Set the label_review_status for the given doc_ids.
#     status should be one of 'PENDING', 'PENDING_REVIEW', 'APPROVED'.
#     """
#     if not doc_ids:
#         return
#     session = get_session()
#     try:
#         stmt = (
#             update(StagedNormalizedData)
#             .where(StagedNormalizedData.doc_id.in_(doc_ids))
#             .values(label_review_status=status)
#         )
#         session.execute(stmt)
#         session.commit()
#     finally:
#         session.close()
# def create_unit_review_record(review_data: Dict[str, Any]):
#     """
#     Inserts a record into the unit review queue for human review.
#     """
#     session = get_session()
#     try:
#         stmt = pg_insert(UnitReviewQueue).values(**review_data)
#         # On conflict, update with new analysis
#         update_cols = {
#             'llm_analysis': stmt.excluded.llm_analysis,
#             'filing_data': stmt.excluded.filing_data,
#             'created_at': stmt.excluded.created_at
#         }
#         stmt = stmt.on_conflict_do_update(
#             index_elements=['doc_id'],
#             set_=update_cols
#         )
#         session.execute(stmt)
#         session.commit()
#     finally:
#         session.close()
# def get_pending_unit_reviews() -> List[UnitReviewQueue]:
#     """
#     Fetches all unit reviews that are pending human review.
#     """
#     session = get_session()
#     try:
#         stmt = select(UnitReviewQueue).where(
#             UnitReviewQueue.status == 'PENDING_REVIEW'
#         ).order_by(UnitReviewQueue.created_at.desc())
#         return session.execute(stmt).scalars().all()
#     finally:
#         session.close()
# def approve_unit_review(review_id: int, corrections: Dict = None):
#     """
#     Marks a unit review as approved and optionally stores human corrections.
#     """
#     session = get_session()
#     try:
#         stmt = update(UnitReviewQueue).where(
#             UnitReviewQueue.id == review_id
#         ).values(
#             status='APPROVED',
#             # --- CORRECTED LINE ---
#             reviewed_at=datetime.datetime.now(timezone.utc),
#             reviewed_by='human_reviewer',
#             human_corrections=corrections
#         )
#         session.execute(stmt)
#         session.commit()
#     finally:
#         session.close()
# def get_approved_unit_reviews() -> List[UnitReviewQueue]:
#     """
#     Fetches all unit reviews that have been approved but not yet applied.
#     """
#     session = get_session()
#     try:
#         stmt = select(UnitReviewQueue).where(
#             UnitReviewQueue.status == 'APPROVED'
#         )
#         return session.execute(stmt).scalars().all()
#     finally:
#         session.close()
# def delete_processed_unit_review(review_id: int):
#     """
#     Removes a unit review record after it has been processed and applied.
#     """
#     session = get_session()
#     try:
#         stmt = delete(UnitReviewQueue).where(UnitReviewQueue.id == review_id)
#         session.execute(stmt)
#         session.commit()
#     finally:
#         session.close()

# ================================================================================================
# QUALITY ENGINE FUNCTIONS
# ================================================================================================
def create_quality_engine_runs_for_new_documents():
    """
    Finds parsed documents that don't have a quality engine run and creates
    an entry for each with default 'PENDING' statuses.
    This function "seeds" the pipeline and is called by the master orchestrator.
    """
    session = get_session()
    try:
        # Subquery to find all doc_ids that are already in the queue
        subquery = select(QualityEngineRun.doc_id)
        
        # Main query to find parsed documents not in the subquery
        stmt = select(ParsedDocument.doc_id).where(
            ParsedDocument.parse_status == 'EXTRACTION_SUCCESS',
            ParsedDocument.doc_id.notin_(subquery)
        )
        doc_ids_to_create = session.execute(stmt).scalars().all()

        if not doc_ids_to_create:
            logging.info("No new documents to seed into the Quality Engine queue.")
            return

        new_runs_data = [{"doc_id": doc_id} for doc_id in doc_ids_to_create]

        # Bulk insert the new runs; all status and version columns will use their defaults (PENDING/NULL)
        session.bulk_insert_mappings(QualityEngineRun, new_runs_data)
        session.commit()
        logging.info(f"Created {len(new_runs_data)} new runs in the Quality Engine queue.")

    except Exception as e:
        session.rollback()
        logging.error(f"Error creating new quality engine runs: {e}", exc_info=True)
        raise
    finally:
        session.close()


def get_runs_by_stage_1_status(statuses: List[str]) -> List[QualityEngineRun]:
    """
    Retrieves a list of QualityEngineRun objects that match one of the
    provided stage_1_status values. This is used by the Stage 1 orchestrator
    to find documents ready for a specific sub-stage.
    """
    session = get_session()
    try:
        # Eagerly load the related parsed_document to avoid extra queries in the main loop
        stmt = (
            select(QualityEngineRun)
            .where(QualityEngineRun.stage_1_status.in_(statuses))
            .options(joinedload(QualityEngineRun.parsed_document)) 
        )
        results = session.execute(stmt).scalars().all()
        return results
    finally:
        session.close()


def update_quality_run(run_id: int, updates: Dict[str, Any]):
    """
    Generic function to update any set of columns for a specific Quality Engine run.
    This single function replaces the need for multiple, stage-specific update functions.
    
    Example usage:
    - On failure: update_quality_run(123, {"stage_1_status": "ORDER_ERROR", "failure_reason": "..."})
    - On success: update_quality_run(123, {"stage_1_status": "PASSED", "stage_1_version": "1.0"})
    """
    session = get_session()
    try:
        stmt = (
            update(QualityEngineRun)
            .where(QualityEngineRun.run_id == run_id)
            .values(**updates)
        )
        session.execute(stmt)
        session.commit()
    except Exception as e:
        session.rollback()
        logging.error(f"Error updating run_id {run_id}: {e}", exc_info=True)
        raise
    finally:
        session.close()

# ================================================================================================
# MASTER DATA FUNCTIONS (Unchanged)
# ================================================================================================

def bulk_upsert_classifications(session: Session, classifications_data: list[dict]):
    """
    Performs a bulk "upsert" (insert or update on conflict) for industry classifications.
    """
    if not classifications_data:
        return

    stmt = pg_insert(Classification).values(classifications_data)
    update_dict = {
        col.name: col for col in stmt.excluded if col.name not in ['basic_industry_name', 'id']
    }
    final_stmt = stmt.on_conflict_do_update(
        index_elements=['basic_industry_name'],
        set_=update_dict
    )
    session.execute(final_stmt)
    print(f"Upserted {len(classifications_data)} classifications.")

def get_classification_id_by_name(session, name):
    stmt = select(Classification.id).where(
        Classification.basic_industry_name == name
    )
    return session.execute(stmt).scalar_one_or_none()

def bulk_upsert_companies(session: Session, company_data: list[dict]):
    """
    Performs a bulk "upsert" for company master data based on the ticker.
    """
    if not company_data:
        return

    stmt = pg_insert(CompanyMaster).values(company_data)
    update_dict = {
        'company_name': stmt.excluded.company_name,
        'isin_code': stmt.excluded.isin_code
    }
    final_stmt = stmt.on_conflict_do_update(
        index_elements=['ticker'],
        set_=update_dict
    )
    session.execute(final_stmt)
    print(f"Upserted {len(company_data)} companies.")

def link_company_to_classification(session: Session, ticker: str, classification_id: int):
    """
    Links a single company in the master table to its classification.
    """
    session.query(CompanyMaster).\
        filter(CompanyMaster.ticker == ticker).\
        update({'classification_id': classification_id})
    print(f"Linked ticker {ticker} to classification ID {classification_id}.")

def get_company_context(session: Session, ticker: str) -> CompanyMaster | None:
    """
    The main function for the pipeline to get a company's full context.
    """
    return session.query(CompanyMaster).\
        options(joinedload(CompanyMaster.classification)).\
        filter(CompanyMaster.ticker == ticker).\
        first()