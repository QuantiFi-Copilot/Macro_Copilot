# earnings_agent/parsing/semantic_map.py

"""
The Semantic Mapping Layer for the EarningsAgent. (Version 1.1)

This module serves as the centralized "dictionary" to translate diverse XBRL tags
from various financial reporting taxonomies (primarily Ind-AS) into our single,
standardized, internal representation defined in `schema.sql` and `models.py`.
"""

# The primary mapping of various XBRL tags to our "Core" internal metric names.
# The keys are the XBRL tag names (case-sensitive as they appear in the XML).
# The values are the exact column names in our `quarterly_fundamentals` table.
SEMANTIC_MAP = {
    # ===== Income Statement Mappings =====
    'RevenueFromOperations': 'revenue',
    'RevenueFromSaleOfProducts': 'revenue',
    'RevenueFromSaleOfServices': 'revenue',
    'InterestEarned': 'revenue',  # For Banks/NBFCs
    'TotalIncome': 'revenue',     # Common fallback

    'CostOfGoodsSold': 'cost_of_goods_sold',
    'CostOfMaterialsConsumed': 'cost_of_goods_sold',

    'GrossProfit': 'gross_profit',

    'OperatingExpense': 'operating_expenses',
    'Expenses': 'operating_expenses', # Broad but common

    'EarningsBeforeInterestTaxDepreciationAndAmortisation': 'ebitda',
    'ProfitBeforeInterestDepreciationAndTax': 'ebitda',

    'DepreciationDepletionAndAmortisationExpense': 'depreciation_and_amortization',
    'DepreciationAndAmortisationExpense': 'depreciation_and_amortization',

    'ProfitLossBeforeInterestAndTax': 'ebit',
    'ProfitBeforeFinanceCostAndTax': 'ebit',
    'ProfitFromOperationsBeforeOtherIncomeFinanceCostAndExceptionalItems': 'ebit',

    'FinanceCosts': 'interest_expense',
    'InterestExpense': 'interest_expense',

    'ProfitLossBeforeTax': 'profit_before_tax',
    'ProfitBeforeTax': 'profit_before_tax',

    'TaxExpense': 'tax_expense',
    'TotalTaxExpense': 'tax_expense',

    'ProfitLoss': 'net_income',
    'ProfitLossForPeriod': 'net_income',
    'ProfitForThePeriod': 'net_income',

    'BasicEarningsLossPerShare': 'earnings_per_share_basic',
    'BasicEarningsLossPerShareFromContinuingOperations': 'earnings_per_share_basic',
    
    'DilutedEarningsLossPerShare': 'earnings_per_share_diluted',
    'DilutedEarningsLossPerShareFromContinuingOperations': 'earnings_per_share_diluted',

    # ===== Balance Sheet Mappings =====
    'CashAndCashEquivalents': 'cash_and_equivalents',

    'TradeReceivables': 'accounts_receivable',
    'CurrentTradeReceivables': 'accounts_receivable',

    'Inventories': 'inventory',

    'CurrentAssets': 'total_current_assets',
    'TotalCurrentAssets': 'total_current_assets',

    'PropertyPlantAndEquipment': 'property_plant_equipment_net',
    'FixedAssets': 'property_plant_equipment_net',

    'NonCurrentAssets': 'total_non_current_assets',

    'Assets': 'total_assets',
    'NetSegmentAssets': 'total_assets', # NEW: Added from Reliance file

    'TradePayables': 'accounts_payable',
    'CurrentTradePayables': 'accounts_payable',

    'CurrentLiabilities': 'total_current_liabilities',
    
    'LongTermBorrowings': 'total_long_term_debt',
    'NonCurrentBorrowings': 'total_long_term_debt',
    
    'NonCurrentLiabilities': 'total_non_current_liabilities',
    
    'Liabilities': 'total_liabilities',
    'NetSegmentLiabilities': 'total_liabilities', # NEW: Added from Reliance file

    'Equity': 'shareholders_equity',
    'EquityAttributableToEquityHoldersOfParent': 'shareholders_equity',
    'TotalEquity': 'shareholders_equity',

    'EquityAndLiabilities': 'total_liabilities_and_equity',

    # ===== Cash Flow Statement Mappings =====
    'NetCashFlowFromOperatingActivities': 'cash_flow_from_operating',
    'CashFlowFromOperatingActivities': 'cash_flow_from_operating',

    'NetCashFlowFromInvestingActivities': 'cash_flow_from_investing',
    'CashFlowFromInvestingActivities': 'cash_flow_from_investing',

    'NetCashFlowFromFinancingActivities': 'cash_flow_from_financing',
    'CashFlowFromFinancingActivities': 'cash_flow_from_financing',

    'NetIncreaseDecreaseInCashAndCashEquivalents': 'net_change_in_cash',
    'NetIncreaseDecreaseInCashAndCashEquivalentsBeforeEffectOfExchangeRateChanges': 'net_change_in_cash'
}
