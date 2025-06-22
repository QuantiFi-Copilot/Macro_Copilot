# earnings_agent/config/universe.py

"""
Defines the universe of companies for the EarningsAgent to process.
Each item in the list is a dictionary containing the full company name
as recognized by the NSE website and the stock ticker for directory/file naming.
"""

COMPANIES = [
    {"name": "Reliance Industries Limited",               "ticker": "RELIANCE"},
    {"name": "HDFC Bank Limited",                         "ticker": "HDFCBANK"},
    {"name": "Tata Consultancy Services Limited",         "ticker": "TCS"},
    {"name": "Bharti Airtel Limited",                     "ticker": "BHARTIARTL"},
    {"name": "ICICI Bank Limited",                        "ticker": "ICICIBANK"},
    {"name": "State Bank of India",                       "ticker": "SBIN"},
    {"name": "Infosys Limited",                           "ticker": "INFY"},
    {"name": "Life Insurance Corporation of India",       "ticker": "LICI"},
    {"name": "Bajaj Finance Limited",                     "ticker": "BAJFINANCE"},
    {"name": "Hindustan Unilever Limited",                "ticker": "HINDUNILVR"},
    {"name": "ITC Limited",                               "ticker": "ITC"},
    {"name": "Larsen & Toubro Limited",                   "ticker": "LT"},
    {"name": "HCL Technologies Limited",                  "ticker": "HCLTECH"},
    {"name": "Kotak Mahindra Bank Limited",               "ticker": "KOTAKBANK"},
    {"name": "Maruti Suzuki India Limited",               "ticker": "MARUTI"},
    {"name": "Sun Pharmaceutical Industries Limited",     "ticker": "SUNPHARMA"},
    {"name": "Mahindra & Mahindra Limited",               "ticker": "M&M"},
    {"name": "Axis Bank Limited",                         "ticker": "AXISBANK"},
    {"name": "UltraTech Cement Limited",                  "ticker": "ULTRACEMCO"},
    {"name": "Hindustan Aeronautics Limited",             "ticker": "HAL"},
]
