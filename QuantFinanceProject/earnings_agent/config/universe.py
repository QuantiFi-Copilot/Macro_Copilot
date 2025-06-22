# earnings_agent/config/universe.py

"""
Defines the universe of companies for the EarningsAgent to process.
Each item in the list is a dictionary containing the full company name
as recognized by the NSE website and the stock ticker for directory/file naming.
"""

COMPANIES = [
    {
        "name": "Reliance Industries Limited",
        "ticker": "RELIANCE"
    },
    {
        "name": "Tata Consultancy Services Limited",
        "ticker": "TCS"
    },
    {
        "name": "HDFC Bank Limited",
        "ticker": "HDFCBANK"
    },
    # ... you can add more companies here following the same format
]