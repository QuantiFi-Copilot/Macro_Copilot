# scripts/populate_company_master.py

import pandas as pd
import os


from earnings_agent.storage.database import (
    get_session,
    bulk_upsert_companies,
    get_classification_id_by_name,
    link_company_to_classification
)

def main():
    """
    Populates the company_master table from a CSV and links it to classifications.
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    csv_path = os.path.join(script_dir, 'company_industry_map.csv')
    print(f"Reading company data from '{csv_path}'...")
    
    try:
        df = pd.read_csv(csv_path)
    except FileNotFoundError:
        print(f"ERROR: '{csv_path}' not found. Please create it first.")
        return

    # Prepare data for bulk insert (core company info only)
    company_core_data = df[['Ticker', 'CompanyName', 'ISIN']].copy()
    company_core_data.rename(columns={
        'Ticker': 'ticker',
        'CompanyName': 'company_name',
        'ISIN': 'isin_code'
    }, inplace=True)
    
    session = get_session()
    try:
        # --- Stage 1: Insert Core Company Data ---
        print("\n--- Stage 1: Populating core company info ---")
        bulk_upsert_companies(session, company_core_data.to_dict(orient='records'))
        session.commit()
        print(f"Successfully upserted {len(df)} companies.")

        # --- Stage 2: Link Companies to Classifications ---
        print("\n--- Stage 2: Linking companies to their classifications ---")
        for index, row in df.iterrows():
            ticker = row['Ticker']
            basic_industry_name = row['BasicIndustryName']
            
            # Find the ID for the given industry name from our database
            classification_id = get_classification_id_by_name(session, basic_industry_name)
            
            if classification_id:
                # Link the company to its classification ID
                link_company_to_classification(session, ticker, classification_id)
            else:
                print(f"  > WARNING: No classification found for industry '{basic_industry_name}'. Skipping ticker '{ticker}'.")
        
        session.commit()
        print("Successfully linked companies.")

    except Exception as e:
        print(f"An error occurred: {e}")
        session.rollback()
    finally:
        print("\nClosing database session.")
        session.close()

if __name__ == "__main__":
    main()