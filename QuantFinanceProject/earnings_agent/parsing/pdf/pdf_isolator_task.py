import json
import logging
import os
import sys
import time
import concurrent.futures
from pathlib import Path
from typing import Dict, Any

# Google GenAI SDK imports
from google import genai
from google.genai import types
from google.auth import default
from google.oauth2 import service_account
from PyPDF2 import PdfReader, PdfWriter

# --- Project Imports ---
# FIX: Corrected path to ensure consistency across the project.
project_root = Path(__file__).resolve().parents[3]
sys.path.append(str(project_root))

from earnings_agent.storage.database import get_session, create_parsed_document
from earnings_agent.storage.models import RawDataAsset, ParsedDocument, IngestionJob, JobAssetLink
from sqlalchemy.orm import Session as SQLAlchemySession
from sqlalchemy import select

# --- Configuration ---
PARSER_VERSION = "parser-version-1.0"
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(module)s - %(message)s')

# --- Production Gemini Configuration ---
GCP_PROJECT_ID = "pdf-extractor-467911"
GCP_LOCATION = "us-central1"
ISOLATION_MODEL = "gemini-2.5-flash"

# --- Schema and Paths ---
STATEMENT_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    properties={
        'statement_name': types.Schema(type=types.Type.STRING, description="The exact name of the statement as found in the PDF"),
        'start_page': types.Schema(type=types.Type.INTEGER, description="The starting page number of the statement"),
        'end_page': types.Schema(type=types.Type.INTEGER, description="The ending page number of the statement"),
        'mapping': types.Schema(type=types.Type.STRING, description="The standardized mapping name for the statement (e.g., 'consolidated_pnl')")
    },
    required=['statement_name', 'start_page', 'end_page', 'mapping']
)
PROCESSED_PDF_DIR = project_root / "earnings_agent" / "storage" / "data" / "processed" / "isolated_pdf"

# --- Retry & Error Handling ---
LLM_MAX_RETRIES = 3
LLM_INITIAL_BACKOFF = 5

# --- Enhanced Isolation Prompt ---
ISOLATION_PROMPT = """
You are an expert document analyst for Indian financial reports. Your task is to scan the attached PDF and identify only the six core financial statements listed below, map them accurately, and provide their precise page numbers.

You MUST return a single, valid JSON object that conforms to the provided schema.

**TARGET STATEMENTS TO FIND AND MAP:**
• `standalone_pnl` - Standalone Profit & Loss / Income Statement
• `standalone_balance_sheet` - Standalone Balance Sheet / Statement of Financial Position  
• `standalone_cash_flow` - Standalone Cash Flow Statement
• `consolidated_pnl` - Consolidated Profit & Loss / Income Statement
• `consolidated_balance_sheet` - Consolidated Balance Sheet / Statement of Financial Position
• `consolidated_cash_flow` - Consolidated Cash Flow Statement

**IDENTIFICATION GUIDELINES:**

**Profit & Loss Indicators:**
- "Profit and Loss" / "Statement of Profit and Loss"
- "Income Statement" / "Statement of Income" 
- Contains revenue, expenses, profit metrics

**Balance Sheet Indicators:**
- "Balance Sheet" / "Statement of Financial Position"
- Contains assets, liabilities, equity sections
- Shows balancing equation structure

**Cash Flow Indicators:**
- "Cash Flow Statement" / "Statement of Cash Flows"
- Shows operating, investing, financing activities
- Contains cash movement analysis

**Consolidation Indicators:**
- "Consolidated" explicitly mentioned in title/header
- "Standalone" / "Separate" for individual entity statements
- Default to standalone if consolidation status unclear

**CRITICAL INSTRUCTIONS:**
- **Strict Focus**: Ignore all other sections (notes, auditor reports, management discussion, etc.)
- **Semantic Mapping**: Use understanding to match varied terminology to standard mappings
- **Multi-Page Handling**: If statement spans multiple pages, set correct `end_page` number
- **Missing Statements**: If a statement type is not found, DO NOT include it in the response array
- **Page Accuracy**: First page of PDF = page 1, ensure accurate page counting
- **Title Extraction**: Use exact statement title as found in the document

**MAPPING ACCURACY:**
Map "Statement of Financial Position" → `standalone_balance_sheet` or `consolidated_balance_sheet`
Map "Statement of Profit and Loss" → `standalone_pnl` or `consolidated_pnl`
Map "Statement of Cash Flows" → `standalone_cash_flow` or `consolidated_cash_flow`
"""

# In pdf_isolator_task.py

def _get_gemini_client():
    """
    FIX: Initializes and returns a NEW production-ready Gemini client.
    This is now safe to be called from within each worker process.
    """
    try:
        credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "/app/gcp-credentials.json")
        if not os.path.exists(credentials_path):
            raise FileNotFoundError(f"Credentials file not found at {credentials_path}")

        credentials = service_account.Credentials.from_service_account_file(
            credentials_path,
            scopes=['https://www.googleapis.com/auth/cloud-platform']
        )
        
        # Create and return a NEW client instance every time.
        return genai.Client(
            vertexai=True,
            project=GCP_PROJECT_ID,
            location=GCP_LOCATION,
            credentials=credentials,
            http_options=types.HttpOptions(api_version="v1")
        )
    except Exception as e:
        logging.error(f"Fatal error initializing Gemini client: {e}", exc_info=True)
        raise
def _call_gemini_with_retry(pdf_bytes: bytes) -> str:
    """Calls Gemini API with retry logic. It now gets its own client instance."""
    client = _get_gemini_client() # Each call in a worker gets a fresh client.
    for attempt in range(LLM_MAX_RETRIES):
        try:
            config = types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.0,
                response_schema=types.Schema(
                    type=types.Type.OBJECT,
                    properties={'statements_found': types.Schema(type=types.Type.ARRAY, items=STATEMENT_SCHEMA)},
                    required=['statements_found']
                )
            )
            response = client.models.generate_content(
                model=ISOLATION_MODEL,
                contents=[ISOLATION_PROMPT, types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf")],
                config=config
            )
            if not response.text:
                raise ValueError("LLM returned an empty response.")
            return response.text
        except Exception as e:
            logging.warning(f"Gemini API call failed on attempt {attempt + 1}: {e}")
            if attempt + 1 == LLM_MAX_RETRIES:
                raise
            time.sleep(LLM_INITIAL_BACKOFF * (2 ** attempt))
    raise RuntimeError("LLM call failed after all retry attempts.")

def get_document_layout(pdf_bytes: bytes) -> list:
    """Step 1: Get document layout from the LLM."""
    logging.info("   [Step 1/2] Running reconnaissance to find and map core statements.")
    response_text = _call_gemini_with_retry(pdf_bytes)
    try:
        data = json.loads(response_text)
        statements_found = data.get("statements_found")
        if not isinstance(statements_found, list):
            raise ValueError("Invalid data type for 'statements_found' in response.")
        logging.info(f"   Found {len(statements_found)} core statements.")
        return statements_found
    except (json.JSONDecodeError, ValueError) as e:
        logging.error(f"Failed to parse layout: {e}. Response was: {response_text}")
        raise ValueError("Could not decode document layout from LLM.") from e

def get_filing_metadata(session: SQLAlchemySession, asset_id: int) -> Dict[str, Any]:
    """Helper to get ticker and period for directory naming."""
    result = session.query(IngestionJob.ticker, IngestionJob.fiscal_year, IngestionJob.quarter)\
        .join(JobAssetLink, JobAssetLink.job_id == IngestionJob.job_id)\
        .filter(JobAssetLink.asset_id == asset_id)\
        .first()
    if not result:
        raise ValueError(f"Could not find job metadata for asset_id {asset_id}")
    quarter_str = f"Q{result.quarter}{result.fiscal_year}"
    return {"ticker": result.ticker, "period": quarter_str}

def isolate_and_save_statements(source_pdf_path: Path, statements: list, filing_metadata: Dict[str, Any]) -> Dict[str, str]:
    """Step 2: Create individual PDFs for each statement."""
    logging.info("   [Step 2/2] Creating individual statement PDFs.")
    ticker_period_dir = PROCESSED_PDF_DIR / filing_metadata['ticker'] / filing_metadata['period']
    ticker_period_dir.mkdir(parents=True, exist_ok=True)
    saved_paths = {}
    try:
        reader = PdfReader(source_pdf_path)
        total_pages = len(reader.pages)
        for stmt in statements:
            mapping, start_page, end_page = stmt.get('mapping'), stmt.get('start_page'), stmt.get('end_page')
            if not all([mapping, start_page, end_page]) or not (0 < start_page <= end_page <= total_pages):
                logging.warning(f"Invalid page range for {mapping}: {start_page}-{end_page} (total: {total_pages}). Skipping.")
                continue
            isolated_pdf_path = ticker_period_dir / f"{mapping}.pdf"
            writer = PdfWriter()
            for page_num in range(start_page, end_page + 1):
                writer.add_page(reader.pages[page_num - 1])
            with open(isolated_pdf_path, "wb") as out_f:
                writer.write(out_f)
            saved_paths[mapping] = str(isolated_pdf_path.relative_to(project_root))
            logging.info(f"      -> Saved {mapping} to {isolated_pdf_path.name}")
    except Exception as e:
        logging.error(f"Error during PDF processing for {source_pdf_path.name}: {e}", exc_info=True)
        raise
    return saved_paths

def process_single_asset_isolation(asset_id: int, session: SQLAlchemySession):
    """Main isolation function for a single PDF asset with robust error handling."""
    try:
        asset = session.get(RawDataAsset, asset_id)
        if not asset or not asset.storage_location:
            raise FileNotFoundError(f"Asset ID {asset_id} not found or has no storage location.")
        source_pdf_path = project_root / asset.storage_location
        if not source_pdf_path.is_file():
            raise FileNotFoundError(f"PDF file not found at: {source_pdf_path}")
        with open(source_pdf_path, "rb") as f:
            pdf_bytes = f.read()
        statements_found = get_document_layout(pdf_bytes)
        filing_metadata = get_filing_metadata(session, asset_id)
        isolated_paths = isolate_and_save_statements(source_pdf_path, statements_found, filing_metadata)
        if not isolated_paths:
            raise RuntimeError(f"No statements were successfully isolated from {len(statements_found)} found.")
        content_for_db = {
            "llm_call_1_isolation": statements_found,
            "isolated_statement_paths": isolated_paths,
        }
        create_parsed_document({
            "asset_id": asset_id, "parser_version": PARSER_VERSION,
            "parse_status": "ISOLATION_SUCCESS", "content": content_for_db, "error_details": None
        })
        logging.info(f"✅ Successfully isolated {len(isolated_paths)} statements for Asset ID: {asset_id}")
    except Exception as e:
        logging.error(f"❌ Error isolating Asset ID {asset_id}: {e}", exc_info=True)
        create_parsed_document({
            "asset_id": asset_id, "parser_version": PARSER_VERSION,
            "parse_status": "ISOLATION_ERROR", "content": None, "error_details": f"Isolation failed: {str(e)}"
        })

def _execute_isolation_for_worker(asset_id: int):
    """
    Worker function for multiprocessing. It now creates its own
    database session to be completely independent.
    """
    try:
        # Each worker process gets its own session
        with get_session() as db_session:
            process_single_asset_isolation(asset_id, db_session)
    except Exception as e:
        logging.error(f"Worker process for Asset ID {asset_id} crashed: {e}", exc_info=True)
    finally:
        pass  # No longer needed - each process has its own engine

def run_isolator_batch():
    """Main batch processing function for statement isolation."""
    MAX_WORKERS = 1
    logging.info(f"--- Starting PDF Isolator Batch Run v{PARSER_VERSION} ---")

    # The main process gets the list of work.
    with get_session() as session:
        processed_assets_subquery = select(ParsedDocument.asset_id).where(ParsedDocument.parser_version == PARSER_VERSION)
        unprocessed_assets_query = select(RawDataAsset.asset_id).where(
            RawDataAsset.source_type == 'PDF_FILE',
            RawDataAsset.asset_id.notin_(processed_assets_subquery)
        )
        asset_ids_to_process = session.execute(unprocessed_assets_query).scalars().all()

    if not asset_ids_to_process:
        logging.info("No new PDF assets to process for isolation.")
        return

    logging.info(f"Found {len(asset_ids_to_process)} PDF assets for isolation. Using {MAX_WORKERS} workers.")
    # The ProcessPoolExecutor now calls the safe worker function
    with concurrent.futures.ProcessPoolExecutor(max_workers=MAX_WORKERS) as executor:
        list(executor.map(_execute_isolation_for_worker, asset_ids_to_process))

    logging.info("--- Isolator batch run completed. ---")

if __name__ == '__main__':
    run_isolator_batch()