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
    Classification,
    CompanyMaster, 
    QualityEngineRun,
    QualityEngineRuleVariant,
    LabelMappingCache, 
    FundamentalRecord, 
    FundamentalsBanking, 
    CustomKpis
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

# ================================================================================================
# QUALITY ENGINE FUNCTIONS
# ================================================================================================
def create_quality_engine_runs_for_new_documents():
    """
    Finds parsed documents that don't have a quality engine run and creates
    an entry for each.
    
    MODIFIED: This function now copies the original content from ParsedDocument
    into the QualityEngineRun's 'working_content' to initialize the run.
    """
    session = get_session()
    try:
        # Subquery to find all doc_ids that are already in the queue
        subquery = select(QualityEngineRun.doc_id)
        # The query now selects both the doc_id and the original content.
        stmt = select(ParsedDocument.doc_id, ParsedDocument.content).where(
            ParsedDocument.parse_status == 'EXTRACTION_SUCCESS',
            ParsedDocument.doc_id.notin_(subquery)
        )
        docs_to_seed = session.execute(stmt).all()
        if not docs_to_seed:
            logging.info("No new documents to seed into the Quality Engine queue.")
            return
        # Build the new run objects, mapping the original content to the
        # 'working_content' field and initializing the history.
        new_runs_data = []
        for doc_id, content in docs_to_seed:
            # Extract only the LLM 2 extraction data
            if content and "llm_call_2_extraction" in content:
                working_content = {"llm_call_2_extraction": content["llm_call_2_extraction"]}
            else:
                # Log warning and skip if no extraction data exists
                logging.warning(f"Skipping doc_id {doc_id}: No llm_call_2_extraction found")
                continue
        
            new_runs_data.append({
                "doc_id": doc_id,
                "working_content": working_content,  # ✅ Only extraction data
                "run_history": [{
                    "step": "seed", 
                    "status": "INITIALIZED", 
                    "timestamp": datetime.datetime.now(timezone.utc).isoformat()
                }]
            })

        # Bulk insert the new runs
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

# Additional functions to add to database.py

def get_runs_by_stage_2_status(statuses: List[str]) -> List[QualityEngineRun]:
    """
    Retrieves QualityEngineRun objects that are ready for Stage 2 processing.
    """
    session = get_session()
    try:
        stmt = (
            select(QualityEngineRun)
            # FIX: This gate ensures Stage 2 only runs on documents that passed Stage 1.
            .where(QualityEngineRun.stage_1_status == 'PASSED')
            .where(QualityEngineRun.stage_2_status.in_(statuses))
            .options(joinedload(QualityEngineRun.parsed_document))
        )
        results = session.execute(stmt).scalars().all()
        return results
    finally:
        session.close()

def get_stage_2_summary_stats() -> Dict[str, int]:
    """
    Get summary statistics for Stage 2 processing.
    """
    session = get_session()
    try:
        from sqlalchemy import func
        
        # Count documents by stage_2_status
        stmt = (
            select(
                QualityEngineRun.stage_2_status,
                func.count(QualityEngineRun.run_id).label('count')
            )
            .group_by(QualityEngineRun.stage_2_status)
        )
        
        results = session.execute(stmt).all()
        
        stats = {
            'PENDING': 0,
            'PASSED': 0,
            'CALCULATION_MISMATCH': 0,
            'TOTAL': 0
        }
        
        for status, count in results:
            if status in stats:
                stats[status] = count
            stats['TOTAL'] += count
        
        return stats
    finally:
        session.close()

def get_rule_variant(session: Session, ticker: str, parent_playbook_id: str) -> Optional[QualityEngineRuleVariant]:
    """
    Retrieves a specific rule variant for a given ticker and parent playbook ID.
    This is the core function the Playbook Resolver will use to find an override.
    """
    try:
        stmt = (
            select(QualityEngineRuleVariant)
            .where(
                QualityEngineRuleVariant.issuer_ticker == ticker,
                QualityEngineRuleVariant.parent_playbook_id == parent_playbook_id
            )
        )
        return session.execute(stmt).scalar_one_or_none()
    except Exception as e:
        logging.error(f"Error fetching rule variant for {ticker} - {parent_playbook_id}: {e}")
        return None

def save_rule_variant(variant_data: Dict[str, Any]):
    """
    Inserts or updates a rule variant in the database.
    This is the function the Rule Correction UI will call after a human makes a fix.
    """
    session = get_session()
    try:
        # Pydantic models in FastAPI/Streamlit might pass enums; convert them to strings
        for key, value in variant_data.items():
            if hasattr(value, 'value'):
                 variant_data[key] = value.value

        stmt = pg_insert(QualityEngineRuleVariant).values(**variant_data)
        
        # On conflict (if a rule for this ticker/playbook_id already exists), update it
        update_cols = {
            'variant_definition': stmt.excluded.variant_definition,
            'created_by': stmt.excluded.created_by,
            'created_at': func.now() # Reset timestamp on update
        }
        stmt = stmt.on_conflict_do_update(
            index_elements=['parent_playbook_id', 'issuer_ticker'],
            set_=update_cols
        )
        
        session.execute(stmt)
        session.commit()
        logging.info(f"Saved rule variant for {variant_data.get('issuer_ticker')} - {variant_data.get('parent_playbook_id')}")
    except Exception as e:
        session.rollback()
        logging.error(f"Error saving rule variant: {e}", exc_info=True)
        raise
    finally:
        session.close()

def delete_rule_variant(variant_id: int):
    """
    Deletes a rule variant from the database.
    Useful for admin purposes if a bad rule is saved by mistake.
    """
    session = get_session()
    try:
        stmt = delete(QualityEngineRuleVariant).where(QualityEngineRuleVariant.id == variant_id)
        session.execute(stmt)
        session.commit()
        logging.info(f"Deleted rule variant with ID: {variant_id}")
    except Exception as e:
        session.rollback()
        logging.error(f"Error deleting rule variant ID {variant_id}: {e}", exc_info=True)
        raise
    finally:
        session.close()

def get_mapping_from_cache(raw_label: str, ticker: str, statement_key: str) -> Optional[LabelMappingCache]:
    """
    Queries the cache for a mapping, prioritizing 'APPROVED' over 'PENDING_REVIEW'.

    This is the core lookup function for the Stage 3 orchestrator. It checks for a
    definitive, human-approved mapping first. If none exists, it checks for an
    in-flight, pending suggestion to avoid redundant LLM calls.

    Args:
        raw_label: The raw text of the label from the document.
        ticker: The company ticker.
        statement_key: The statement the label belongs to (e.g., 'consolidated_pnl').

    Returns:
        The highest-priority LabelMappingCache object if a match is found, otherwise None.
    """
    session = get_session()
    try:
        # Step 1: Prioritize the approved mapping
        approved_stmt = (
            select(LabelMappingCache)
            .where(
                LabelMappingCache.raw_label == raw_label,
                LabelMappingCache.ticker == ticker,
                LabelMappingCache.statement_key == statement_key,
                LabelMappingCache.status == 'APPROVED'
            )
        )
        approved_result = session.execute(approved_stmt).scalar_one_or_none()
        if approved_result:
            return approved_result

        # Step 2: If no approved mapping, look for a pending one
        pending_stmt = (
            select(LabelMappingCache)
            .where(
                LabelMappingCache.raw_label == raw_label,
                LabelMappingCache.ticker == ticker,
                LabelMappingCache.statement_key == statement_key,
                LabelMappingCache.status == 'PENDING_REVIEW'
            )
        )
        pending_result = session.execute(pending_stmt).scalar_one_or_none()
        return pending_result

    finally:
        session.close()


def save_mapping_to_cache(mapping_data: Dict[str, Any]):
    """
    Saves a new 'PENDING_REVIEW' mapping to the cache. Performs an "upsert".

    This is called by Stage 3 after getting a new suggestion from the LLM.
    If a mapping for the same context already exists, it will be updated.

    Args:
        mapping_data: A dictionary with the mapping details. It should not
                      include a 'status' as this is handled by default.
    """
    session = get_session()
    try:
        # Ensure status is correctly set for new suggestions
        mapping_data['status'] = 'PENDING_REVIEW'

        stmt = pg_insert(LabelMappingCache).values(**mapping_data)

        # On conflict, update the details but keep the status as 'PENDING_REVIEW'
        update_cols = {
            'mapping_type': stmt.excluded.mapping_type,
            'normalized_label': stmt.excluded.normalized_label,
            'status': 'PENDING_REVIEW', # Re-set to pending on update
            'approved_at': func.now()
        }

        stmt = stmt.on_conflict_do_update(
            constraint='uq_label_mapping_context',
            set_=update_cols
        )

        session.execute(stmt)
        session.commit()
        logging.info(f"Saved PENDING mapping for '{mapping_data.get('raw_label')}' to cache.")
    except Exception as e:
        session.rollback()
        logging.error(f"Error saving mapping to cache: {e}", exc_info=True)
        raise
    finally:
        session.close()


def update_mapping_status(mapping_id: int, new_status: str, user: str):
    """
    Updates the status of an existing mapping in the cache.

    This function is intended to be used by the human review UI to approve or
    reject a pending mapping.

    Args:
        mapping_id: The primary key (id) of the mapping record.
        new_status: The new status, typically 'APPROVED' or 'REJECTED'.
        user: The identifier for the user performing the action.
    """
    session = get_session()
    try:
        stmt = (
            update(LabelMappingCache)
            .where(LabelMappingCache.id == mapping_id)
            .values(
                status=new_status,
                approved_by=user,
                approved_at=func.now()
            )
        )
        session.execute(stmt)
        session.commit()
        logging.info(f"Updated mapping ID {mapping_id} to status '{new_status}' by user '{user}'.")
    except Exception as e:
        session.rollback()
        logging.error(f"Error updating mapping status for ID {mapping_id}: {e}", exc_info=True)
        raise
    finally:
        session.close()

def get_runs_by_stage_4_status(statuses: List[str]) -> List[QualityEngineRun]:
    """
    Retrieves QualityEngineRun objects that are ready for Stage 4 processing.
    """
    session = get_session()
    try:
        stmt = (
            select(QualityEngineRun)
            # FIX: This gate ensures Stage 4 only runs on documents that passed Stage 3.
            .where(QualityEngineRun.stage_3_status == 'PASSED')
            .where(QualityEngineRun.stage_4_status.in_(statuses))
            .options(joinedload(QualityEngineRun.parsed_document))
        )
        results = session.execute(stmt).scalars().all()
        return results
    finally:
        session.close()

# ================================================================================================
# FINALIZATION ENGINE FUNCTIONS
# ================================================================================================
# The corrected function in earnings_agent/storage/database.py

# In earnings_agent/storage/database.py

# In earnings_agent/storage/database.py

# In earnings_agent/storage/database.py

def get_runs_ready_for_golden_record() -> List[QualityEngineRun]:
    """
    Finds all QE runs that have successfully passed Stage 4 and are waiting
    to be loaded into the golden record tables, ensuring oldest are processed first.
    """
    session = get_session()
    try:
        stmt = (
            select(QualityEngineRun)
            .where(
                QualityEngineRun.stage_4_status == 'PASSED',
                QualityEngineRun.is_loaded_to_golden_record == False
            )
            .options(
                joinedload(QualityEngineRun.parsed_document)
                .joinedload(ParsedDocument.asset)
                .joinedload(RawDataAsset.job_links)
                .joinedload(JobAssetLink.job)
            )
            # --- THIS IS THE FIX ---
            .order_by(QualityEngineRun.created_at.asc())
        )
        results = session.execute(stmt).unique().scalars().all()
        return results
    finally:
        session.close()

def create_fundamental_record(session: Session, record_data: Dict[str, Any]) -> int:
    """
    Performs an "upsert" on the fundamental_records table.
    
    It inserts a new record or, if a record with the same unique constraint
    (ticker, fiscal_date, version) already exists, it updates it.
    
    Args:
        session: The SQLAlchemy session to use for the transaction.
        record_data: A dictionary containing all the data for the new record.
        
    Returns:
        The integer ID of the inserted or updated fundamental record.
    """
    stmt = pg_insert(FundamentalRecord).values(**record_data)
    
    update_cols = {
        'filing_date': stmt.excluded.filing_date,
        'source_playbook': stmt.excluded.source_playbook,
        'source_run_id': stmt.excluded.source_run_id,
        'updated_at': func.now()
    }
    
    # On conflict with the unique constraint, update the existing row
    stmt = stmt.on_conflict_do_update(
        constraint='uq_fundamental_record',
        set_=update_cols
    ).returning(FundamentalRecord.id)
    
    # Execute and return the ID
    record_id = session.execute(stmt).scalar_one()
    return record_id

def get_master_records_pending_population(child_model: Base) -> List[FundamentalRecord]:
    """
    Finds all FundamentalRecord entries that do not yet have a corresponding
    child record in the specified table (e.g., FundamentalsBanking).
    
    This is the "finder" function for the Finalization Engine stages.
    """
    session = get_session()
    try:
        # Perform a LEFT JOIN from the master table to the child table
        stmt = (
            select(FundamentalRecord)
            .outerjoin(child_model, FundamentalRecord.id == child_model.record_id)
            .where(child_model.record_id == None) # Filter for master records that have no child
            .options(
                # Eagerly load the QE run to get access to the working_content
                joinedload(FundamentalRecord.quality_engine_run) 
            )
        )
        results = session.execute(stmt).scalars().all()
        return results
    finally:
        session.close()

def upsert_banking_fundamentals(session: Session, banking_data: Dict[str, Any]):
    """
    Performs an "upsert" on the fundamentals_banking table using the record_id.
    If a row with the record_id exists, it's updated; otherwise, it's inserted.
    """
    stmt = pg_insert(FundamentalsBanking).values(**banking_data)
    
    # Dynamically create the update dictionary, excluding the primary key
    update_dict = {
        col.name: getattr(stmt.excluded, col.name)
        for col in FundamentalsBanking.__table__.columns if col.name != 'record_id'
    }
    update_dict['updated_at'] = func.now()

    stmt = stmt.on_conflict_do_update(
        index_elements=['record_id'], # Use the primary key for conflict detection
        set_=update_dict
    )
    session.execute(stmt)

def upsert_custom_kpis(session: Session, kpi_record_data: Dict[str, Any]):
    """
    Performs an "upsert" on the custom_kpis table using the record_id.
    """
    stmt = pg_insert(CustomKpis).values(**kpi_record_data)
    
    update_dict = {
        'kpi_data': stmt.excluded.kpi_data,
        'updated_at': func.now()
    }

    stmt = stmt.on_conflict_do_update(
        index_elements=['record_id'], # The record_id is UNIQUE, so this works as our key
        set_=update_dict
    )
    session.execute(stmt)
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