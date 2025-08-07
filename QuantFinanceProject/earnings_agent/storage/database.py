# earnings_agent/storage/database.py

import os
from dotenv import load_dotenv, find_dotenv
from sqlalchemy import create_engine, update, select, delete, func 
from sqlalchemy.orm import sessionmaker, joinedload, Session
from sqlalchemy.dialects.postgresql import insert as pg_insert
from typing import List, Dict, Any, Optional
import datetime
from datetime import date, timezone

# Import all the new models
from earnings_agent.storage.models import (
    Base,
    IngestionJob,
    RawDataAsset,
    JobAssetLink,
    ParsedDocument,
    LabelMapping, # NEW: For the normalization cache
    StagedNormalizedData, # NEW: For the reconciliation staging area
    QualityEngineResult,
    QuarterlyFundamental,
    CustomKPI,
    Classification,
    CompanyMaster,
    UnitReviewQueue
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

# ================================================================================================
# NORMALIZATION STAGE FUNCTIONS (NEW)
# ================================================================================================

def get_label_mapping(raw_label: str, industry: str) -> Optional[LabelMapping]:
    """
    Retrieves a single label mapping from the cache using its composite key.
    """
    session = get_session()
    try:
        # session.get() works with composite keys when passed a tuple
        return session.get(LabelMapping, (raw_label, industry))
    finally:
        session.close()

def upsert_label_mapping(mapping_data: Dict[str, Any]):
    """
    Inserts or updates a label mapping in the cache.
    The mapping_data dictionary MUST contain 'raw_label' and 'industry'.
    """
    session = get_session()
    try:
        stmt = pg_insert(LabelMapping).values(**mapping_data)
        update_cols = {
            'normalized_label': stmt.excluded.normalized_label,
            'status': stmt.excluded.status,
            'last_reviewed_at': stmt.excluded.last_reviewed_at,
            'reviewed_by': stmt.excluded.reviewed_by
        }
        # Update the index_elements to use the new composite key
        stmt = stmt.on_conflict_do_update(
            index_elements=['raw_label', 'industry'],
            set_=update_cols
        )
        session.execute(stmt)
        session.commit()
    finally:
        session.close()

def create_staged_normalized_data(data: Dict[str, Any]):
    """
    Inserts a record in the staging table for normalized data.
    If a record with the same doc_id already exists, it does nothing.
    This is safer and prevents accidental overwrites.
    """
    session = get_session()
    try:
        stmt = pg_insert(StagedNormalizedData).values(**data)
        # --- THIS IS THE FIX ---
        # Change the conflict action to DO NOTHING. This function's only job
        # is to create the initial record if it doesn't exist.
        stmt = stmt.on_conflict_do_nothing(
            index_elements=['doc_id']
        )
        # --- END OF FIX ---
        session.execute(stmt)
        session.commit()
    finally:
        session.close()
def get_unprocessed_approved_labels() -> List[LabelMapping]:
    """
    Fetches all label mappings that have been approved but not yet processed
    by the backfill job. This is the "to-do list" for the backfill script.
    """
    session = get_session()
    try:
        stmt = select(LabelMapping).where(
            LabelMapping.status == 'APPROVED',
            LabelMapping.processed == False
        )
        return session.execute(stmt).scalars().all()
    finally:
        session.close()


# --- NEW FUNCTION 2 ---
def mark_labels_as_processed(raw_labels: List[str]):
    """
    Marks a batch of approved labels as processed after the backfill
    job has successfully run for them.
    """
    if not raw_labels:
        return

    session = get_session()
    try:
        stmt = update(LabelMapping).where(
            LabelMapping.raw_label.in_(raw_labels)
        ).values(
            processed=True
        )
        session.execute(stmt)
        session.commit()
    except Exception as e:
        session.rollback()
        raise e
    finally:
        session.close()

def fetch_pending_label_reviews() -> List[LabelMapping]:
    """
    Queries the database for all label mappings with 'PENDING_REVIEW' status.
    """
    session = get_session()
    try:
        return session.query(LabelMapping).filter(
            LabelMapping.status == 'PENDING_REVIEW'
        ).order_by(LabelMapping.created_at.desc()).all()
    finally:
        session.close()

# Add `new_label` as an optional parameter
def update_label_mapping_status(raw_label: str, industry: str, new_status: str, new_label: str = None, reviewer: str = "human_reviewer"):
    """
    Updates the status and optionally the normalized_label of a mapping.
    """
    session = get_session()
    try:
        mapping_to_update = session.get(LabelMapping, (raw_label, industry))
        if mapping_to_update:
            mapping_to_update.status = new_status
            mapping_to_update.last_reviewed_at = datetime.now(timezone.utc)
            mapping_to_update.reviewed_by = reviewer
            # If a new label is provided, update it
            if new_label is not None:
                # Handle empty string from UI as null
                mapping_to_update.normalized_label = None if new_label.lower() == 'null' or not new_label else new_label
            session.commit()
    except Exception as e:
        session.rollback()
        raise e
    finally:
        session.close()


# ================================================================================================
# NORMALIZATION STATE MANAGEMENT FUNCTIONS (NEW)
# ================================================================================================
def get_docs_pending_statement_normalization() -> List[int]:
    """
    Return all doc_ids that have not yet run through the statement normalizer.
    """
    session = get_session()
    try:
        stmt = select(StagedNormalizedData.doc_id).where(
            StagedNormalizedData.statement_normalized == False
        )
        return session.execute(stmt).scalars().all()
    finally:
        session.close()

def mark_docs_statement_normalized(doc_ids: List[int]):
    """
    Mark the given doc_ids as having completed statement normalization.
    """
    if not doc_ids:
        return
    session = get_session()
    try:
        stmt = (
            update(StagedNormalizedData)
            .where(StagedNormalizedData.doc_id.in_(doc_ids))
            .values(statement_normalized=True)
        )
        session.execute(stmt)
        session.commit()
    finally:
        session.close()

def get_docs_pending_unit_normalization() -> List[int]:
    """
    Return all doc_ids ready for unit normalization: statement-normalized but not unit-processed.
    """
    session = get_session()
    try:
        stmt = select(StagedNormalizedData.doc_id).where(
            StagedNormalizedData.statement_normalized == True,
            StagedNormalizedData.unit_review_status == 'PENDING'
        )
        return session.execute(stmt).scalars().all()
    finally:
        session.close()

def mark_docs_unit_review_status(doc_ids: List[int], status: str):
    """
    Set the unit_review_status for the given doc_ids.
    status should be one of 'PENDING', 'AUTO_APPROVED', 'PENDING_REVIEW', 'APPROVED'.
    """
    if not doc_ids:
        return
    session = get_session()
    try:
        stmt = (
            update(StagedNormalizedData)
            .where(StagedNormalizedData.doc_id.in_(doc_ids))
            .values(unit_review_status=status)
        )
        session.execute(stmt)
        session.commit()
    finally:
        session.close()


def get_docs_pending_label_normalization() -> List[int]:
    """
    Return doc_ids ready for label normalization. This now includes both
    human-approved and auto-approved documents from the unit normalization phase.
    """
    session = get_session()
    try:
        stmt = select(StagedNormalizedData.doc_id).where(
            StagedNormalizedData.unit_review_status.in_(['APPROVED', 'AUTO_APPROVED']),
            StagedNormalizedData.label_review_status == 'PENDING'
        )
        return session.execute(stmt).scalars().all()
    finally:
        session.close()

def get_docs_pending_label_review() -> List[int]:
    """
    Return doc_ids that have been scanned for labels and are awaiting human review approval.
    """
    session = get_session()
    try:
        stmt = select(StagedNormalizedData.doc_id).where(
            StagedNormalizedData.label_review_status == 'PENDING_REVIEW'
        )
        return session.execute(stmt).scalars().all()
    finally:
        session.close()

def mark_docs_label_review_status(doc_ids: List[int], status: str):
    """
    Set the label_review_status for the given doc_ids.
    status should be one of 'PENDING', 'PENDING_REVIEW', 'APPROVED'.
    """
    if not doc_ids:
        return
    session = get_session()
    try:
        stmt = (
            update(StagedNormalizedData)
            .where(StagedNormalizedData.doc_id.in_(doc_ids))
            .values(label_review_status=status)
        )
        session.execute(stmt)
        session.commit()
    finally:
        session.close()

def create_unit_review_record(review_data: Dict[str, Any]):
    """
    Inserts a record into the unit review queue for human review.
    """
    session = get_session()
    try:
        stmt = pg_insert(UnitReviewQueue).values(**review_data)
        # On conflict, update with new analysis
        update_cols = {
            'llm_analysis': stmt.excluded.llm_analysis,
            'filing_data': stmt.excluded.filing_data,
            'created_at': stmt.excluded.created_at
        }
        stmt = stmt.on_conflict_do_update(
            index_elements=['doc_id'],
            set_=update_cols
        )
        session.execute(stmt)
        session.commit()
    finally:
        session.close()

def get_pending_unit_reviews() -> List[UnitReviewQueue]:
    """
    Fetches all unit reviews that are pending human review.
    """
    session = get_session()
    try:
        stmt = select(UnitReviewQueue).where(
            UnitReviewQueue.status == 'PENDING_REVIEW'
        ).order_by(UnitReviewQueue.created_at.desc())
        return session.execute(stmt).scalars().all()
    finally:
        session.close()

def approve_unit_review(review_id: int, corrections: Dict = None):
    """
    Marks a unit review as approved and optionally stores human corrections.
    """
    session = get_session()
    try:
        stmt = update(UnitReviewQueue).where(
            UnitReviewQueue.id == review_id
        ).values(
            status='APPROVED',
            reviewed_at=datetime.datetime.now(timezone.utc), # MODIFIED LINE
            reviewed_by='human_reviewer',
            human_corrections=corrections
        )
        session.execute(stmt)
        session.commit()
    finally:
        session.close()

def get_approved_unit_reviews() -> List[UnitReviewQueue]:
    """
    Fetches all unit reviews that have been approved but not yet applied.
    """
    session = get_session()
    try:
        stmt = select(UnitReviewQueue).where(
            UnitReviewQueue.status == 'APPROVED'
        )
        return session.execute(stmt).scalars().all()
    finally:
        session.close()

def delete_processed_unit_review(review_id: int):
    """
    Removes a unit review record after it has been processed and applied.
    """
    session = get_session()
    try:
        stmt = delete(UnitReviewQueue).where(UnitReviewQueue.id == review_id)
        session.execute(stmt)
        session.commit()
    finally:
        session.close()
# ================================================================================================
# RECONCILIATION & FINALIZATION FUNCTIONS (NEW & UPDATED)
# ================================================================================================

def get_staged_data_for_reconciliation(ticker: str, fiscal_date: date, consolidation_status: str) -> List[StagedNormalizedData]:
    """
    Fetches all staged data for a filing, eagerly loading the entire relationship
    chain from StagedNormalizedData back to the original IngestionJob.
    """
    session = get_session()
    try:
        stmt = select(StagedNormalizedData).options(
            # This chain tells SQLAlchemy to load everything we need in one go
            joinedload(StagedNormalizedData.parsed_document)
            .joinedload(ParsedDocument.asset)
            .joinedload(RawDataAsset.job_links)
            .joinedload(JobAssetLink.job)
        ).join(
            ParsedDocument, StagedNormalizedData.doc_id == ParsedDocument.doc_id
        ).join(
            JobAssetLink, ParsedDocument.asset_id == JobAssetLink.asset_id
        ).join(
            IngestionJob, JobAssetLink.job_id == IngestionJob.job_id
        ).where(
            StagedNormalizedData.ticker == ticker,
            StagedNormalizedData.fiscal_date == fiscal_date,
            IngestionJob.consolidation_status == consolidation_status
        )
        # --- MODIFIED: Added .unique() to de-duplicate the results ---
        return session.execute(stmt).scalars().unique().all()
    finally:
        session.close()

def create_quality_engine_result(result_data: Dict[str, Any]):
    """
    Inserts or updates a QualityEngineResult. If a result for the same
    ticker, fiscal_date, and playbook_config_hash exists, it updates the record.
    """
    session = get_session()
    try:
        stmt = pg_insert(QualityEngineResult).values(**result_data)
        update_cols = {
            'status': stmt.excluded.status,
            'summary': stmt.excluded.summary,
            'engine_version': stmt.excluded.engine_version,
            'completed_at': stmt.excluded.completed_at
        }
        stmt = stmt.on_conflict_do_update(
            index_elements=['ticker', 'fiscal_date', 'playbook_config_hash'],
            set_=update_cols
        )
        session.execute(stmt)
        session.commit()
    finally:
        session.close()

def upsert_quarterly_fundamental(data: Dict[str, Any]) -> int:
    """
    Inserts or updates a final "Golden Record" in the quarterly_fundamentals table.
    Returns the ID of the upserted record.
    """
    session = get_session()
    try:
        stmt = pg_insert(QuarterlyFundamental).values(**data)
        # Exclude keys that are part of the unique constraint or are immutable
        update_cols = {col.name: col for col in stmt.excluded if col.name not in ['ticker', 'fiscal_date', 'version', 'id']}
        stmt = stmt.on_conflict_do_update(
            index_elements=['ticker', 'fiscal_date', 'version'],
            set_=update_cols,
            where=(QuarterlyFundamental.ticker == data['ticker']) & (QuarterlyFundamental.fiscal_date == data['fiscal_date'])
        ).returning(QuarterlyFundamental.id)
        
        result = session.execute(stmt)
        session.commit()
        return result.scalar_one()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

def upsert_custom_kpi(data: Dict[str, Any]):
    """
    Inserts or updates the custom KPIs associated with a fundamental record.
    """
    session = get_session()
    try:
        stmt = pg_insert(CustomKPI).values(**data)
        update_cols = {'kpi_data': stmt.excluded.kpi_data}
        stmt = stmt.on_conflict_do_update(
            index_elements=['fundamental_id'],
            set_=update_cols
        )
        session.execute(stmt)
        session.commit()
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