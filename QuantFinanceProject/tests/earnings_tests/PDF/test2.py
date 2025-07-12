import pdfplumber
import pandas as pd
from tabulate import tabulate

PDF_PATH = "/Volumes/Sreeram/QuantFinanceProject/QuantFinanceProject/tests/earnings_tests/PDF/SE_Result.pdf"

def extract_tables(pdf_path):
    print(f"\nReading file: {pdf_path}")
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages):
            print(f"\n--- Page {i+1} ---")
            text = page.extract_text()
            if text and any(keyword in text for keyword in ["UNAUDITED", "FINANCIAL", "RESULTS", "Segment", "Quarter"]):
                print("Likely contains relevant financial information.\n")

                tables = page.extract_tables()
                if tables:
                    for idx, table in enumerate(tables):
                        try:
                            df = pd.DataFrame(table[1:], columns=table[0])
                            print(f"\nTable {idx+1} on Page {i+1}:\n")
                            print(tabulate(df, headers='keys', tablefmt='psql', showindex=False))
                        except Exception as e:
                            print(f"Error parsing table {idx+1} on Page {i+1}: {e}")
                else:
                    print("No tables found on this page.")
            else:
                print("Skipping page — doesn't seem relevant.")



import os

def clean_camelot_table(df):
    df.columns = [col.replace('\n', ' ').strip() for col in df.iloc[0]]
    df = df.drop(index=0).reset_index(drop=True)
    df = df.applymap(lambda x: str(x).replace('\n', ' ').strip())
    return df

def extract_with_camelot(pdf_path):
    import camelot
    print("\n--- Trying Camelot (lattice) ---")
    tables = camelot.read_pdf(pdf_path, pages="all", flavor="lattice")

    os.makedirs("outputs", exist_ok=True)

    for i, table in enumerate(tables):
        try:
            df = clean_camelot_table(table.df)
            print(f"\n--- Cleaned Camelot Table {i+1} ---")
            print(tabulate(df, headers='keys', tablefmt='psql', showindex=False))
            output_path = f"outputs/table_{i+1}.csv"
            df.to_csv(output_path, index=False)
            print(f"Saved to {output_path}")
        except Exception as e:
            print(f"Error cleaning or saving table {i+1}: {e}")

if __name__ == "__main__":
    extract_tables(PDF_PATH)
    extract_with_camelot(PDF_PATH)
