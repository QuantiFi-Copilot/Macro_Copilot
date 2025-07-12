import sys
sys.path.append("/Volumes/Sreeram/QuantFinanceProject/QuantFinanceProject/tests/earnings_tests/PaddleOCR")

from ppstructure.table.predict_table import StructureTable

table_ocr = StructureTable()
results = table_ocr("page_10.jpeg")

for i, tbl in enumerate(results):
    print(f"\n--- Table {i+1} ---\n{tbl['html'][:400]}...")