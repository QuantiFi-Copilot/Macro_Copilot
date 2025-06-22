import xml.etree.ElementTree as ET
from pathlib import Path
import json
import logging
from datetime import datetime

# A more comprehensive semantic map based on the sample files provided.
SEMANTIC_MAP = {
    # Maps to 'standard_revenue'
    "RevenueFromOperations": "standard_revenue",
    "InterestEarned": "standard_revenue",
    "Income": "standard_revenue",

    # Maps to 'standard_net_income'
    "ProfitLoss": "standard_net_income",
    "ProfitLossForThePeriod": "standard_net_income",
    "ProfitOrLossAttributableToOwnersOfParent": "standard_net_income",
    "ProfitLossAfterTaxesMinorityInterestAndShareOfProfitLossOfAssociates": "standard_net_income",

    # Maps to other core fields
    "Assets": "total_assets",
    "EquityAndLiabilities": "total_liabilities", # In many balance sheets, this represents the total L+E side
    "CashFlowsFromUsedInOperatingActivities": "operating_cash_flow",
}

UNIT_MULTIPLIERS = {
    "crores": 10_000_000,
    "lakhs": 100_000,
    "millions": 1_000_000,
}

def find_tag(element, tag_name):
    """Helper function to find a tag regardless of its namespace."""
    return element.find(f".//{{*}}{tag_name}")

def find_all_tags(element, tag_name):
    """Helper function to find all tags regardless of their namespace."""
    return element.findall(f".//{{*}}{tag_name}")

def parse_xbrl_file(file_path: Path) -> dict | None:
    """
    Parses a given XBRL XML file and extracts financial data into a structured dictionary.
    This version is more robust and handles namespaces correctly.
    """
    try:
        logging.info(f"Starting XBRL parsing for: {file_path.name}")
        tree = ET.parse(file_path)
        root = tree.getroot()

        # --- 1. Extract Metadata and Context ---
        # Find the primary context for the most recent quarter.
        # This heuristic looks for a context ID containing "OneD" which typically represents the
        # primary, single-quarter duration for the main financial statement.
        contexts = find_all_tags(root, 'context')
        primary_context_id = next((c.attrib['id'] for c in contexts if 'OneD' in c.attrib.get('id', '')), None)
        
        if not primary_context_id:
            logging.error(f"Could not determine primary reporting context for {file_path.name}")
            return None

        # Find the end date tag within the specific primary context element
        context_element = root.find(f".//*[@id='{primary_context_id}']")
        end_date_tag = find_tag(context_element, 'endDate')
        period_end_date = end_date_tag.text if end_date_tag is not None else "Unknown"

        multiplier_tag = find_tag(root, 'LevelOfRoundingUsedInFinancialStatements')
        multiplier_str = multiplier_tag.text.lower() if multiplier_tag is not None else "absolute"
        multiplier = UNIT_MULTIPLIERS.get(multiplier_str, 1)
        
        ticker_tag = find_tag(root, 'Symbol')
        ticker = ticker_tag.text if ticker_tag is not None else file_path.stem.split('_')[-1]
        
        logging.info(f"Parsing {ticker} for period ending {period_end_date}. Units: {multiplier_str.title()}.")

        # --- 2. Extract and Process All Financial Facts ---
        core_data = {}
        custom_kpis = {}

        for fact in root:
            if 'contextRef' not in fact.attrib:
                continue
                
            if fact.attrib['contextRef'] == primary_context_id:
                tag_name = fact.tag.split('}')[-1]
                raw_value = fact.text

                if raw_value is None:
                    continue
                try:
                    numeric_value = float(raw_value) * multiplier
                except (ValueError, TypeError):
                    continue

                if tag_name in SEMANTIC_MAP:
                    db_field = SEMANTIC_MAP[tag_name]
                    # Don't overwrite a more specific value with a less specific one
                    if db_field not in core_data:
                        core_data[db_field] = int(numeric_value)
                else:
                    custom_kpis[tag_name] = int(numeric_value)
        
        if not core_data:
            logging.warning("No core data extracted. Check context ID and XBRL tags.")
            return None
            
        return {
            "metadata": { "ticker": ticker, "period_end_date": period_end_date, "source_file": file_path.name },
            "core_data": core_data,
            "custom_kpis": custom_kpis,
        }
    except Exception as e:
        logging.error(f"Failed to parse {file_path.name}: {type(e).__name__} - {e}")
        return None


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

    # CORRECTED: Point to the flat directory structure you specified.
    # This path goes up from `parsing/` to `earnings_agent/` then into `storage/xbrl/`
    data_dir = Path(__file__).resolve().parent.parent / "storage" / "data"/"xbrl"
    
    # List of test files to process from the correct location.
    test_files_to_parse = [
        data_dir / "Q2_2024_RELIANCE.xml",
        data_dir / "Q2_2024_HDFCBANK.xml",
        # You can add more files here to test, e.g., data_dir / "Q2_2024_TCS.xml"
    ]

    for test_file in test_files_to_parse:
        print("-" * 60)
        
        if not test_file.exists():
            logging.error(f"Test file not found: {test_file}")
            logging.error("Please ensure the file exists in the 'earnings_agent/storage/data/xbrl/' directory.")
            continue

        parsed_data = parse_xbrl_file(test_file)

        if parsed_data:
            print(f"\n--- XBRL Parsing Successful for {test_file.name} ---")
            print(json.dumps(parsed_data, indent=4))
        else:
            print(f"\n--- XBRL Parsing Failed for {test_file.name} ---")

    print("-" * 60)