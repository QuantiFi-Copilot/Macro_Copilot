#!/usr/bin/env python
"""
quick_parse_financials_v6_final.py

This definitive version fixes the AttributeError by ensuring DataFrame headers
are always unique. It builds on the successful Camelot extraction strategy and
applies a robust post-processing function to produce a clean, consolidated output.

Run           :  python quick_parse_financials_v6_final.py /path/to/your/pdf.pdf
Dependencies  :  pip install pymupdf pdfplumber camelot-py[cv] pandas
"""

import sys
import re
from pathlib import Path
import camelot
import pandas as pd
import pdfplumber
import numpy as np
import warnings

warnings.filterwarnings("ignore", category=UserWarning)

# --- CONFIGURATION ---
DEFAULT_PDF_PATH = Path("/Volumes/Sreeram/QuantFinanceProject/QuantFinanceProject/tests/earnings_tests/PDF/SE_Result.pdf")
KEYWORDS = {
    "income": ["revenue from operations", "total income"],
    "expenses": ["cost of materials consumed", "finance costs"],
    "profit": ["profit before tax", "profit after tax"],
    "eps": ["earnings per share"],
}

# --- HELPER & CORE FUNCTIONS ---

def make_headers_unique(headers: list) -> list:
    """Ensures all column headers are unique by appending suffixes."""
    counts = {}
    new_headers = []
    for header in headers:
        header_str = str(header or 'unnamed').replace('\n', ' ').strip()
        if not header_str: header_str = 'unnamed'
        
        if header_str in counts:
            counts[header_str] += 1
            new_headers.append(f"{header_str}_{counts[header_str]}")
        else:
            counts[header_str] = 0
            new_headers.append(header_str)
    return new_headers

def consolidate_and_clean_table(df: pd.DataFrame) -> pd.DataFrame:
    """
    Takes a raw DataFrame from Camelot and performs robust cleaning.
    """
    if df.empty or df.shape[1] < 2:
        return pd.DataFrame()

    # Find header row
    date_pattern = re.compile(r'\b(mar|jun|sep|dec)\b.?.?\d{2,4}', re.IGNORECASE)
    header_row_idx = -1
    for i, row in df.head(5).iterrows():
        row_text = ' '.join(str(cell) for cell in row.values if cell)
        if 'particulars' in row_text.lower() or date_pattern.search(row_text):
            header_row_idx = i
            break
    
    if header_row_idx != -1:
        headers = df.iloc[header_row_idx].tolist()
        df.columns = make_headers_unique(headers) # Ensure unique headers
        df = df.iloc[header_row_idx + 1:].reset_index(drop=True)
    else:
        df.columns = [f"col_{i}" for i in range(df.shape[1])]

    # Consolidate multi-line rows
    new_rows, pending_description = [], ""
    for _, row in df.iterrows():
        row_list = row.tolist()
        stub = str(row_list[0] or "").strip()
        has_numbers = any(bool(re.search(r'\d', str(cell))) for cell in row_list[1:])

        if has_numbers:
            full_stub = (pending_description + " " + stub).strip()
            new_rows.append([full_stub] + row_list[1:])
            pending_description = ""
        elif stub:
            pending_description += " " + stub

    if not new_rows: return pd.DataFrame()

    clean_df = pd.DataFrame(new_rows, columns=df.columns)
    clean_df.replace(to_replace=["", "nan", "-"], value=np.nan, inplace=True)
    
    # Normalize numeric columns
    for col in clean_df.columns[1:]:
        if clean_df[col].dtype == 'object':
            s = clean_df[col].astype(str).str.replace(r'[,]', '', regex=True)
            s = s.str.replace(r'^\((.*)\)$', r'-\1', regex=True)
            clean_df[col] = pd.to_numeric(s, errors='coerce')

    clean_df.dropna(subset=clean_df.columns[1:], how='all', inplace=True)
    
    return clean_df.reset_index(drop=True)

def detect_searchable_pages(pdf_path: Path) -> list[str]:
    searchable = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text and len(text.strip()) > 100:
                searchable.append(str(page.page_number))
    return searchable

def contains_keyword(df: pd.DataFrame) -> bool:
    flat_keywords = [kw for sublist in KEYWORDS.values() for kw in sublist]
    df_text = ' '.join(df.astype(str).values.flatten()).lower()
    return any(re.search(r'\b' + re.escape(kw) + r'\b', df_text) for kw in flat_keywords)

def main():
    if len(sys.argv) > 1:
        pdf_path = Path(sys.argv[1]).expanduser().resolve()
    else:
        pdf_path = DEFAULT_PDF_PATH.expanduser().resolve()

    if not pdf_path.exists():
        sys.exit(f"File not found: {pdf_path}")

    print(f"--- Processing: {pdf_path.name} ---")

    searchable_pages = detect_searchable_pages(pdf_path)
    page_range = ",".join(searchable_pages)
    
    tables = camelot.read_pdf(
        str(pdf_path),
        pages=page_range,
        flavor="stream",
        edge_tol=500,
        row_tol=15
    )
    
    final_tables = []
    for t in tables:
        cleaned_df = consolidate_and_clean_table(t.df)
        if not cleaned_df.empty and contains_keyword(cleaned_df):
            final_tables.append((t.page, cleaned_df))

    print("\n\n--- FINAL RESULT ---")
    if not final_tables:
        print("\nNo keyword-bearing financial tables were successfully parsed.")
        return

    for page_no, df in final_tables:
        print(f"\n### Successfully Parsed Table from Page {page_no} ###")
        with pd.option_context('display.max_rows', None, 'display.max_columns', None, 'display.width', 120):
            print(df)

if __name__ == '__main__':
    main()