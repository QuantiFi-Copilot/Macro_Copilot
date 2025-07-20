import csv
import subprocess
import os
import json

def parse_xbrl(xbrl_file_path, arelle_cmd_line_path):
    """
    Parses an XBRL file by converting its facts to a structured JSON file,
    which is a more reliable method than CSV. It uses the --validate flag
    to ensure all necessary schemas are loaded by Arelle.
    """
    output_json_path = "output.json"
    
    # --- Step 1: Convert XBRL to JSON using Arelle ---
    # !! MAJOR FIX !! Switched from --facts (CSV) to --facts-export-file (JSON).
    # This provides a standardized, reliable output format.
    arelle_command = [
        "python",
        arelle_cmd_line_path,
        "--file",
        xbrl_file_path,
        "--validate", 
        "--facts-export-file",
        output_json_path
    ]

    print(f"-> Running Arelle command: {' '.join(arelle_command)}")
    
    try:
        # Run the Arelle command
        result = subprocess.run(
            arelle_command, 
            check=True, 
            capture_output=True, 
            text=True,
            encoding='utf-8'
        )
        print("-> Arelle processing finished successfully.")

    except subprocess.CalledProcessError as e:
        print("\n!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
        print(f"!! ARELLE FAILED TO EXECUTE for {os.path.basename(xbrl_file_path)}")
        print(f"!! Arelle stderr:\n{e.stderr}")
        print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n")
        return None

    # --- Step 2: Inspect and Parse the JSON output file ---
    if not os.path.exists(output_json_path) or os.path.getsize(output_json_path) == 0:
        print(f"!! CRITICAL ERROR: Arelle did not create a valid JSON output file at '{output_json_path}'!")
        return None

    financial_data = {}
    key_financial_tags = {
        "in-bse-fin:RevenueFromOperations": "Revenue from Operations",
        "in-bse-fin:ProfitLossForPeriod": "Net Profit/Loss",
        "in-bse-fin:BasicEarningsLossPerShareFromContinuingOperations": "Basic EPS",
        "in-bse-fin:EquityShareCapital": "Equity Share Capital",
        "in-bse-fin:NameOfTheCompany": "Company Name"
    }
    
    print(f"-> Parsing '{output_json_path}'...")
    try:
        with open(output_json_path, 'r', encoding='utf-8') as json_file:
            # Load the entire JSON structure
            xbrl_facts = json.load(json_file)
            
            # The facts are in a list under the 'facts' key
            for fact in xbrl_facts.get('facts', []):
                # Extract data from the JSON object for each fact
                concept = fact.get('concept')
                context_id = fact.get('contextID')
                value = fact.get('value')

                # Filter for the specific context ID for the current period's data
                if context_id == "OneD":
                    if concept in key_financial_tags:
                        label = key_financial_tags[concept]
                        if label not in financial_data:
                            financial_data[label] = value
                            print(f"   -> Found '{label}': {value}")

    except json.JSONDecodeError as e:
        print(f"!! CRITICAL ERROR: Failed to decode JSON from 'output.json'. The file may be corrupt. Error: {e}")
        return None
    except Exception as e:
        print(f"!! An error occurred while parsing the JSON file: {e}")
        return None

    # Clean up the temporary file
    os.remove(output_json_path)
    
    return financial_data

if __name__ == "__main__":
    ARELLE_CMD_LINE_PATH = "/Volumes/Sreeram/Arelle/arelleCmdLine.py"

    # Process all files now that the core issue is resolved.
    xbrl_files_to_process = [
        "ITC_FY2024_Q2_Standalone.xml"
    ] 
    
    print("--- Starting XBRL Parser ---")
    for xbrl_file in xbrl_files_to_process:
        print(f"\n========================================================")
        print(f"Processing file: {xbrl_file}")
        print(f"========================================================")

        if not os.path.exists(xbrl_file):
            print(f"!! FILE NOT FOUND: {xbrl_file}. Skipping.")
            continue

        xbrl_file_path = os.path.abspath(xbrl_file)
        
        extracted_data = parse_xbrl(xbrl_file_path, ARELLE_CMD_LINE_PATH)
        
        if extracted_data:
            print("\n---------------------------------")
            print(f"📊 Final Extracted Data for: {extracted_data.get('Company Name', os.path.basename(xbrl_file))}")
            print("---------------------------------")
            for key, value in sorted(extracted_data.items()):
                if key != "Company Name":
                    print(f"   {key}: {value}")
            print("---------------------------------")
        else:
            print("\n-> No data was extracted for this file.")

    print("\n--- Parser Finished ---")
