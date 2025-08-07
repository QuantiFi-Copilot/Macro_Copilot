import json
import logging
import os
import sys
import time
import re
import concurrent.futures
from pathlib import Path

# Correct imports as per your scripts
from google import genai
from google.genai import types
from PyPDF2 import PdfReader, PdfWriter

# --- Project Imports ---
project_root = Path(__file__).resolve().parents[2]
sys.path.append(str(project_root))

from earnings_agent.storage.database import get_session, create_parsed_document
from earnings_agent.storage.models import RawDataAsset, ParsedDocument
from sqlalchemy.orm import Session as SQLAlchemySession
from sqlalchemy import select
from earnings_agent.storage.database import engine as db_engine

# --- Configuration ---
PARSER_VERSION = "pdf-parser-v1.2"
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(module)s - %(message)s')

# --- Gemini Configuration ---
GCP_PROJECT_ID = "pdf-extractor-467911"
GCP_LOCATION = "us-central1"
ISOLATION_MODEL = "gemini-2.5-flash"
EXTRACTION_MODEL = "gemini-2.5-pro"



# Strict JSON configuration
STRICT_JSON_CONFIG = types.GenerateContentConfig(
    response_mime_type="application/json",
    temperature=0.0,
    max_output_tokens=65536,
)

# --- File Paths ---
DATA_ROOT = project_root / "storage" / "data"
PROCESSED_PDF_DIR = DATA_ROOT / "processed" / "isolated_pdf"
PROCESSED_PDF_DIR.mkdir(parents=True, exist_ok=True)

# --- Retry & Error Handling ---
LLM_MAX_RETRIES = 3
LLM_INITIAL_BACKOFF = 5

# --- Prompts ---
ISOLATION_PROMPT = """
You are an expert document analyst for Indian financial reports. Your task is to scan the attached PDF and identify only the six core financial statements listed below, map them, and provide their page numbers.

You MUST return a single, valid JSON object with two top-level keys: "statements_found" and "page_list".

1.  **"statements_found"**: An array of objects, where each object represents one of the six core statements found in the PDF.
2.  **"page_list"**: A simple array of all unique integers from the start and end pages of the statements found.

Six core statements to find and map:
* `standalone_pnl`
* `standalone_balance_sheet`
* `standalone_cash_flow`
* `consolidated_pnl`
* `consolidated_balance_sheet`
* `consolidated_cash_flow`

CRITICAL GUIDELINES:
- **Strict Focus**: Ignore all other sections, including press releases, auditor's notes, related party disclosures, and any other non-essential information. Your sole focus is on finding only the six core statements.
- **Output Format**: **The entire output MUST be a single, valid JSON object, starting with `{` and ending with `}`. It is critical that you do not include the markdown specifiers or any other text, commentary, or explanations outside of the final JSON object.**
- **Semantic Mapping**: You must use your semantic understanding to find the best match. For example, a statement titled "Standalone Statement of Financial Position" must be mapped to `standalone_balance_sheet`. For labels which are ambiguous, make your decision based on its content.
- **Handling Multi-Page Statements**: If a single financial statement (like a Balance Sheet) continues onto a subsequent page, you must identify this and set the `end_page` correctly. A continuation page will have a similar tabular structure, even if it lacks a main title.
- **Null Values**: If a canonical statement is not found in the document, it should not be included in the `statements_found` array.
- **Page List Generation**: The `page_list` must be a flat, unique, and sorted array of all integers from the `start_page` to the `end_page` for every statement found. For example, if a statement spans pages 5 to 6, the list must include both 5 and 6.
- **Page Numbering**: The first page of the PDF is always page 1, regardless of what is printed on it. 

EXAMPLE OUTPUT FORMAT:
{
  "statements_found": [
    {"statement_name": "STATEMENT OF AUDITED STANDALONE FINANCIAL RESULTS", "start_page": 1, "end_page": 1, "mapping": "standalone_pnl"},
    {"statement_name": "Audited Balance Sheet: Standalone", "start_page": 4, "end_page": 4, "mapping": "standalone_balance_sheet"},
    {"statement_name": "Standalone Statement of Cash Flows", "start_page": 5, "end_page": 6, "mapping": "standalone_cash_flow"}
  ],
  "page_list": [1, 4, 5, 6]
}
"""

EXTRACTION_PROMPT = """
You are an expert financial statement analyst specializing in Indian listed company earnings reports.

You will be provided with a JSON array listing the core financial statements found in the attached PDF.

Your task is to use this **list of statements as your guide** to extract data from the attached PDF.
1. For **each** statement listed in the provided JSON, extract **only the most recent quarter's data** from the PDF.
2. Return your answer as a **pure JSON array**, where each element corresponds to a statement from the provided list.

The required JSON format is:
[
  {
    "statement_type": "<exact "statement_name" from the provided list>",
    "quarter": "YYYY-MM-DD",
    "currency": "<currency and units exactly as stated in the statement>",
    "figures": [
      {
        "label": "<line item name as printed in the PDF>",
        "value": <number or null>,
        "suspect": <true|false>, 
        "suspect_reason": "<concise one sentence reason for suspect status | null if not suspect>"
      }
    ]
  }
]

Guidelines:
- **Output Format**: **The entire output MUST be a single, valid JSON array, starting with `[` and ending with `]`. Do not include markdown specifiers or any other text.**
- "statement_type" must exactly match the `statement_name` from the provided list of statements.
- "label" must match the wording in the PDF exactly for traceability.
- "suspect": true if the number is unclear, has multiple possible readings, or includes footnotes.
- If a value is missing for the latest quarter, set "value": null and "suspect": true.
- Do not skip any relevant statements from the provided list.
- DO NOT add any additional symbols or text outside what is required above.
- Strictly maintain the same JSON array format given above for every PDF processed.
"""

# Initialize Gemini Client
client = genai.Client(vertexai=True, project=GCP_PROJECT_ID, location=GCP_LOCATION)

def clean_json_response(response_text: str) -> str:
    """Aggressively clean LLM response to extract only the JSON part."""
    response_text = response_text.strip()
    
    # Remove markdown code blocks
    response_text = re.sub(r'```json\s*', '', response_text)
    response_text = re.sub(r'```\s*', '', response_text)
    
    # Find the first { and last } to extract JSON
    first_brace = response_text.find('{')
    last_brace = response_text.rfind('}')
    
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        json_part = response_text[first_brace:last_brace + 1]
        return json_part.strip()
    
    # If no braces found, return original (will likely fail JSON parsing)
    return response_text

def _call_gemini_with_retry(model_name: str, prompt: str, pdf_bytes: bytes, context_text: str | None = None, use_json_mode: bool = True) -> str:
    """Calls the Gemini API with retry logic and optional strict JSON mode."""
    for attempt in range(LLM_MAX_RETRIES):
        try:
            prompt_part = types.Part.from_text(text=prompt)
            pdf_part = types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf")
            
            parts_list = [prompt_part]
            if context_text:
                context_part = types.Part.from_text(text=context_text)
                parts_list.append(context_part)
            parts_list.append(pdf_part)

            contents = [types.Content(role="user", parts=parts_list)]
            
            # Use strict JSON config if requested, otherwise use default config
            if use_json_mode:
                config = STRICT_JSON_CONFIG
            else:
                config = types.GenerateContentConfig(
                    temperature=0.0,
                    max_output_tokens=16384
                )
            
            # Add safety settings
            config.safety_settings = [
                types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="OFF"),
                types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="OFF"),
                types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="OFF"),
                types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="OFF"),
            ]

            # Use generate_content instead of stream for strict JSON
            response = client.models.generate_content(
                model=model_name,
                contents=contents,
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

def get_document_layout(pdf_bytes: bytes) -> tuple[dict, list[int]]:
    """Step 1: Get document layout and return parsed JSON and page numbers."""
    logging.info("   [Step 1/3] Running reconnaissance to find and map core statements.")
    response_text = _call_gemini_with_retry(ISOLATION_MODEL, ISOLATION_PROMPT, pdf_bytes, use_json_mode=True)
    
    try:
        # With strict JSON mode, the response should be clean JSON already
        data = json.loads(response_text)
        
        if "statements_found" not in data or "page_list" not in data:
            raise ValueError("Response JSON is missing required keys.")

        statements_found = data["statements_found"]
        page_numbers = data["page_list"]

        if not isinstance(page_numbers, list) or not isinstance(statements_found, list):
            raise ValueError("Invalid data types in response.")
            
        logging.info(f"   Found {len(statements_found)} core statements on pages: {page_numbers}")
        return data, page_numbers

    except (json.JSONDecodeError, ValueError) as e:
        logging.error(f"Failed to parse layout: {e}")
        logging.error(f"Response was: {response_text}")
        raise ValueError("Could not decode document layout from LLM.") from e

def parse_pdf_asset(asset_id: int, session: SQLAlchemySession):
    """Main parsing function for a single PDF asset."""
    try:
        asset = session.get(RawDataAsset, asset_id)
        if not asset or not asset.storage_location:
            raise FileNotFoundError(f"Asset ID {asset_id} not found.")

        source_pdf_path = Path(asset.storage_location)
        if not source_pdf_path.exists():
            raise FileNotFoundError(f"PDF file not found: {source_pdf_path}")
        
        with open(source_pdf_path, "rb") as f:
            pdf_bytes = f.read()

        # Step 1: Get Document Layout
        layout_data, page_numbers = get_document_layout(pdf_bytes)
        
        # Step 2: Create Isolated PDF
        logging.info("   [Step 2/3] Creating isolated PDF.")
        iso_pdf_dir = PROCESSED_PDF_DIR / source_pdf_path.parent.name
        iso_pdf_dir.mkdir(parents=True, exist_ok=True)
        isolated_pdf_path = iso_pdf_dir / source_pdf_path.name.replace(".pdf", "_isolated.pdf")

        reader = PdfReader(source_pdf_path)
        writer = PdfWriter()
        for page_num in sorted(list(set(page_numbers))):
            page_index = page_num - 1
            if 0 <= page_index < len(reader.pages):
                writer.add_page(reader.pages[page_index])
        
        with open(isolated_pdf_path, "wb") as out_f:
            writer.write(out_f)
        
        with open(isolated_pdf_path, "rb") as f:
            isolated_pdf_bytes = f.read()

        # Step 3: Extract Data
        logging.info(f"   [Step 3/3] Extracting data from isolated PDF.")
        context_text = f"Statements to extract:\n{json.dumps(layout_data['statements_found'], indent=2)}"
        response_text = _call_gemini_with_retry(
            EXTRACTION_MODEL, 
            EXTRACTION_PROMPT, 
            isolated_pdf_bytes, 
            context_text=context_text,
            use_json_mode=True
        )

        try:
            # Parse the JSON to validate it's valid
            extraction_data = json.loads(response_text)
            
            # The extraction should now be a pure array as per the prompt
            if not isinstance(extraction_data, list):
                raise ValueError(f"Expected array but got {type(extraction_data)}.")
                
        except json.JSONDecodeError as e:
            logging.error(f"Failed to parse extraction response: {e}")
            logging.error(f"Response was: {response_text}")
            raise ValueError("Could not decode extraction JSON from LLM.") from e

        # Store in DB - Raw LLM outputs only
        final_output = {
            "llm_call_1": layout_data,
            "llm_call_2": extraction_data
        }

        doc_data = {
            "asset_id": asset_id,
            "parser_version": PARSER_VERSION,
            "parse_status": 'PARSED_OK',
            "content": final_output,
        }
        create_parsed_document(doc_data)
        logging.info(f"✅ Successfully parsed Asset ID: {asset_id}")

    except Exception as e:
        logging.error(f"❌ Error parsing Asset ID {asset_id}: {e}")
        doc_data = {
            "asset_id": asset_id,
            "parser_version": PARSER_VERSION,
            "parse_status": 'PARSING_ERROR',
            "error_details": str(e)
        }
        create_parsed_document(doc_data)

def _execute_parse_for_worker(asset_id: int):
    """Worker function for multiprocessing."""
    try:
        db_engine.dispose()
        with get_session() as db_session:
            parse_pdf_asset(asset_id, db_session)
    except Exception as e:
        logging.error(f"Worker process for Asset ID {asset_id} failed: {e}")
        raise

def run_parser_batch():
    """Main batch processing function."""
    MAX_WORKERS = 4 
    
    logging.info(f"--- Starting PDF Parser Batch Run v{PARSER_VERSION} ---")
    
    with get_session() as session:
        processed_assets_subquery = select(ParsedDocument.asset_id).where(
            ParsedDocument.parser_version == PARSER_VERSION
        )
        unprocessed_assets_query = select(RawDataAsset.asset_id).where(
            RawDataAsset.source_type == 'PDF_FILE',
            RawDataAsset.asset_id.notin_(processed_assets_subquery)
        )
        asset_ids_to_process = session.execute(unprocessed_assets_query).scalars().all()
    
    if not asset_ids_to_process:
        logging.info("No new PDF assets to process.")
        return

    logging.info(f"Processing {len(asset_ids_to_process)} PDF assets with {MAX_WORKERS} workers.")

    with concurrent.futures.ProcessPoolExecutor(max_workers=MAX_WORKERS) as executor:
        list(executor.map(_execute_parse_for_worker, asset_ids_to_process))

    logging.info("--- Batch run completed. ---")

if __name__ == '__main__':
    run_parser_batch()