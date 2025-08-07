# earnings_agent/normalization/normalization_engine.py

import logging
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).resolve().parents[2]
sys.path.append(str(project_root))

from earnings_agent.normalization.statement_normalizer import run_statement_normalizer_batch
from earnings_agent.normalization.unit_normalizer import (
    run_unit_normalizer_discovery, 
    run_unit_normalizer_application
)
# --- NEW: Import the label normalizer functions ---
from earnings_agent.normalization.label_normalizer import (
    run_label_normalizer_discovery,
    run_label_normalizer_application
)
# ---------------------------------------------------

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(module)s - %(message)s"
)
logger = logging.getLogger(__name__)

def main(allow_llm: bool = True):
    """
    Main orchestration function for the full normalization pipeline.
    """
    logger.info("=" * 80)
    logger.info("STARTING FULL NORMALIZATION WORKFLOW - PHASE 1, 2 & 3")
    logger.info(f"LLM calls enabled: {allow_llm}")
    logger.info("=" * 80)
    
    try:
        # PHASE 1: Statement Normalization
        logger.info("🔄 PHASE 1: Statement Normalization")
        run_statement_normalizer_batch()
        logger.info("✅ Phase 1 Complete - Statement normalization finished")
        
        # PHASE 2: Unit Normalization
        logger.info("🔄 PHASE 2: Unit Normalization")
        run_unit_normalizer_discovery(allow_llm)
        run_unit_normalizer_application()
        logger.info("✅ Phase 2 Complete - Unit normalization finished")

        # --- NEW: PHASE 3: Label Normalization ---
        logger.info("🔄 PHASE 3: Label Normalization")
        run_label_normalizer_discovery(allow_llm)
        run_label_normalizer_application()
        logger.info("✅ Phase 3 Complete - Label normalization finished")
        # -----------------------------------------
        
    except Exception as e:
        logger.error(f"💥 NORMALIZATION WORKFLOW FAILED: {e}", exc_info=True)
        raise
    
    logger.info("=" * 80)
    logger.info("NORMALIZATION WORKFLOW COMPLETE")
    logger.info("=" * 80)

def status_report():
    """
    Generate a comprehensive status report of the entire normalization pipeline.
    """
    from earnings_agent.storage.database import get_session
    from sqlalchemy import select, func
    from earnings_agent.storage.models import StagedNormalizedData, UnitReviewQueue, ParsedDocument, LabelMapping
    
    logger.info("=" * 60)
    logger.info("📊 NORMALIZATION PIPELINE STATUS REPORT")
    logger.info("=" * 60)
    
    session = get_session()
    try:
        total_parsed = session.execute(
            select(func.count(ParsedDocument.doc_id)).where(ParsedDocument.parse_status == 'PARSED_OK')
        ).scalar()
        logger.info(f"📄 Total Parsed Documents: {total_parsed}")
        
        # Statement normalization status
        stmt_complete = session.execute(
            select(func.count(StagedNormalizedData.id)).where(StagedNormalizedData.statement_normalized == True)
        ).scalar()
        logger.info(f"📋 Statement Normalization: {stmt_complete}/{total_parsed} Complete")

        # Unit normalization status
        unit_statuses = session.execute(
            select(StagedNormalizedData.unit_review_status, func.count(StagedNormalizedData.id))
            .group_by(StagedNormalizedData.unit_review_status)
        ).all()
        logger.info("🔬 Unit Normalization:")
        for status, count in unit_statuses:
            logger.info(f"   - {status}: {count}")

        # --- NEW: Label normalization status ---
        label_statuses = session.execute(
            select(StagedNormalizedData.label_review_status, func.count(StagedNormalizedData.id))
            .group_by(StagedNormalizedData.label_review_status)
        ).all()
        logger.info("🏷️ Label Normalization:")
        for status, count in label_statuses:
            logger.info(f"   - {status}: {count}")
        # -----------------------------------------

        # Unit review queue status
        unit_queue_statuses = session.execute(
            select(UnitReviewQueue.status, func.count(UnitReviewQueue.id))
            .group_by(UnitReviewQueue.status)
        ).all()
        logger.info("📋 Unit Review Queue:")
        for status, count in unit_queue_statuses:
            logger.info(f"   - {status}: {count}")

        # --- NEW: Label mapping cache status ---
        label_cache_statuses = session.execute(
            select(LabelMapping.status, func.count(LabelMapping.raw_label))
            .group_by(LabelMapping.status)
        ).all()
        logger.info("🧠 Label Mapping Cache:")
        for status, count in label_cache_statuses:
            logger.info(f"   - {status}: {count}")
        # -----------------------------------------

        fully_normalized = session.execute(
            select(func.count(StagedNormalizedData.id))
            .where(StagedNormalizedData.label_review_status == 'APPROVED')
        ).scalar()
        
        logger.info(f"🏆 Fully Normalized & Ready for Quality Engine: {fully_normalized} filings")
        
    finally:
        session.close()
    
    logger.info("=" * 60)

if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='Full Normalization Engine')
    parser.add_argument('--no-llm', action='store_true', help='Disable all LLM calls')
    parser.add_argument('--status', action='store_true', help='Show detailed pipeline status report')
    args = parser.parse_args()
    
    try:
        if args.status:
            status_report()
        else:
            main(allow_llm=not args.no_llm)
            logger.info("\n" + "="*40 + " FINAL STATUS " + "="*40)
            status_report()
            
    except KeyboardInterrupt:
        logger.info("\n🛑 Pipeline interrupted by user")
    except Exception as e:
        logger.error(f"\n💥 Pipeline failed with error: {e}")
        sys.exit(1)