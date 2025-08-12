#!/usr/bin/env python3
"""
Central PDF Parser Task - Production Coordinator
==============================================

This script coordinates the complete PDF parsing pipeline:
1. PDF Isolation: Extract individual financial statements from full filings
2. Data Extraction: Extract structured financial data from isolated statements

Designed for production deployment as a Docker service with comprehensive
error handling, monitoring, and resource management.
"""

import logging
import sys
import time
from pathlib import Path
from typing import Optional

# --- Project Imports ---
project_root = Path(__file__).resolve().parents[2]
sys.path.append(str(project_root))

from earnings_agent.parsing.pdf.pdf_isolator_task import run_isolator_batch
from earnings_agent.parsing.pdf.pdf_extractor_task import run_extractor_batch

# --- Configuration ---
COORDINATOR_VERSION = "parser-version-1.0"  # Unified version for the complete PDF parsing pipeline

# Ensure logs directory exists
logs_dir = project_root / "logs"
logs_dir.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(levelname)s - %(name)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(logs_dir / "pdf_parser_coordinator.log")
    ]
)
logger = logging.getLogger("PDFParserCoordinator")

class PDFParsingCoordinator:
    """
    Production-ready coordinator for the complete PDF parsing pipeline.
    
    Manages the sequential execution of isolation and extraction phases
    with comprehensive error handling and monitoring.
    """
    
    def __init__(self):
        self.start_time = None
        self.phase_stats = {
            'isolation': {'status': 'pending', 'duration': None, 'error': None},
            'extraction': {'status': 'pending', 'duration': None, 'error': None}
        }
        
    def run_isolation_phase(self) -> bool:
        """
        Execute the PDF isolation phase.
        
        Returns:
            bool: True if successful, False if failed
        """
        logger.info("🔄 Starting PDF Isolation Phase...")
        logger.info("   Processing: unprocessed assets + isolation failures")
        phase_start = time.time()
        
        try:
            run_isolator_batch()
            
            duration = time.time() - phase_start
            self.phase_stats['isolation'] = {
                'status': 'completed',
                'duration': duration,
                'error': None
            }
            
            logger.info(f"✅ PDF Isolation Phase completed successfully in {duration:.2f} seconds")
            return True
            
        except Exception as e:
            duration = time.time() - phase_start
            error_msg = f"PDF Isolation Phase failed: {str(e)}"
            
            self.phase_stats['isolation'] = {
                'status': 'failed',
                'duration': duration,
                'error': error_msg
            }
            
            logger.error(f"❌ {error_msg}", exc_info=True)
            return False
    
    def run_extraction_phase(self) -> bool:
        """
        Execute the data extraction phase.
        
        Returns:
            bool: True if successful, False if failed
        """
        logger.info("🔄 Starting Data Extraction Phase...")
        logger.info("   Processing: isolation success + extraction failures (banking companies only)")
        phase_start = time.time()
        
        try:
            run_extractor_batch()
            
            duration = time.time() - phase_start
            self.phase_stats['extraction'] = {
                'status': 'completed',
                'duration': duration,
                'error': None
            }
            
            logger.info(f"✅ Data Extraction Phase completed successfully in {duration:.2f} seconds")
            return True
            
        except Exception as e:
            duration = time.time() - phase_start
            error_msg = f"Data Extraction Phase failed: {str(e)}"
            
            self.phase_stats['extraction'] = {
                'status': 'failed',
                'duration': duration,
                'error': error_msg
            }
            
            logger.error(f"❌ {error_msg}", exc_info=True)
            return False
    
    def run_complete_pipeline(self, skip_isolation: bool = False) -> bool:
        """
        Execute the complete PDF parsing pipeline.
        
        Args:
            skip_isolation: If True, skip isolation phase (useful for reprocessing)
            
        Returns:
            bool: True if pipeline completed successfully, False otherwise
        """
        self.start_time = time.time()
        
        logger.info(f"🚀 Starting Complete PDF Parsing Pipeline v{COORDINATOR_VERSION}")
        logger.info("="*80)
        
        # Phase 1: PDF Isolation
        if not skip_isolation:
            if not self.run_isolation_phase():
                logger.error("Pipeline terminated due to isolation phase failure")
                self._log_final_summary(success=False)
                return False
        else:
            logger.info("⏭️  Skipping PDF Isolation Phase (skip_isolation=True)")
            self.phase_stats['isolation']['status'] = 'skipped'
        
        # Brief pause between phases for resource cleanup
        time.sleep(2)
        
        # Phase 2: Data Extraction
        if not self.run_extraction_phase():
            logger.error("Pipeline terminated due to extraction phase failure")
            self._log_final_summary(success=False)
            return False
        
        # Pipeline completed successfully
        self._log_final_summary(success=True)
        return True
    
    def _log_final_summary(self, success: bool):
        """Log final pipeline execution summary."""
        total_duration = time.time() - self.start_time if self.start_time else 0
        
        logger.info("="*80)
        logger.info(f"📊 PDF Parsing Pipeline Summary - {'SUCCESS' if success else 'FAILED'}")
        logger.info("="*80)
        
        # Phase summaries
        for phase_name, stats in self.phase_stats.items():
            status_emoji = {
                'completed': '✅',
                'failed': '❌', 
                'skipped': '⏭️',
                'pending': '⏸️'
            }.get(stats['status'], '❓')
            
            duration_str = f"{stats['duration']:.2f}s" if stats['duration'] else "N/A"
            logger.info(f"{status_emoji} {phase_name.title()} Phase: {stats['status'].upper()} ({duration_str})")
            
            if stats['error']:
                logger.info(f"   Error: {stats['error']}")
        
        # Overall summary
        logger.info(f"⏱️  Total Pipeline Duration: {total_duration:.2f} seconds")
        logger.info(f"🏁 Pipeline Result: {'SUCCESS' if success else 'FAILURE'}")
        logger.info("="*80)

def main():
    """Main entry point for the PDF parsing coordinator."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="PDF Parsing Pipeline Coordinator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python pdf_parser_task.py                    # Run complete pipeline
  python pdf_parser_task.py --skip-isolation   # Skip isolation, run extraction only
        """
    )
    
    parser.add_argument(
        '--skip-isolation',
        action='store_true',
        help='Skip the PDF isolation phase (useful for reprocessing extractions)'
    )
    
    parser.add_argument(
        '--version',
        action='version',
        version=f'PDF Parser Coordinator {COORDINATOR_VERSION}'
    )
    
    args = parser.parse_args()
    
    # Create and run coordinator
    coordinator = PDFParsingCoordinator()
    
    try:
        success = coordinator.run_complete_pipeline(skip_isolation=args.skip_isolation)
        exit_code = 0 if success else 1
        
    except KeyboardInterrupt:
        logger.warning("🛑 Pipeline interrupted by user (Ctrl+C)")
        exit_code = 130
        
    except Exception as e:
        logger.error(f"💥 Unexpected error in pipeline coordinator: {e}", exc_info=True)
        exit_code = 1
    
    logger.info(f"🏁 PDF Parsing Coordinator exiting with code {exit_code}")
    sys.exit(exit_code)

if __name__ == '__main__':
    main()