# earnings_agent/sources/nse_api/nse_decoder_map.py

"""
The NSE API Decoder Map (v1.1)

This module serves as the "decoder ring" for the cryptic keys returned by the
NSE's `/api/corporates-financial-results-data` API endpoint.

It translates the machine-friendly keys (e.g., 're_total_inc') from the
'resultsData2' JSON object into our single, standardized, internal metric names
as defined in our database schema.

This version (v1.1) is built from a diverse sample set including:
- RELIANCE (Conglomerate)
- HDFCBANK (Banking)
- TCS (IT Services)
- ULTRACEMCO (Industrials)
- LT (Industrials)
- SUNPHARMA (Pharma)
"""

NSE_DECODER_MAP = {
    # ===== Income Statement Mappings =====

    # --- Revenue ---
    # The transformer logic should prioritize the most specific key available.
    're_total_inc': 'revenue',          # Universal: "Total Income"
    're_int_earned': 'revenue',         # Specific to Banks: "Interest Earned"
    're_net_sale': 'revenue',           # Specific to Non-Banks: "Revenue from operations"

    # --- Expenses ---
    're_oth_tot_exp': 'operating_expenses',   # Universal: "Total Expenses"
    're_oper_exp': 'operating_expenses',      # Found in Banks
    're_rawmat_consump': 'cost_of_goods_sold',# Non-Banks: "Cost of materials consumed"
    're_pur_trd_goods': 'cost_of_goods_sold', # Non-Banks: "(b) Cost of materials consumed"
    're_int_expd': 'interest_expense',        # Specific to Banks: "Interest Expended"
    're_int_new': 'interest_expense',         # Non-Banks: "Finance costs"
    're_depr_und_exp': 'depreciation_and_amortization', # Non-Banks

    # --- Profitability ---
    're_pro_loss_bef_tax': 'profit_before_tax',
    're_tax': 'tax_expense',                  # Universal: "Total Tax expense"
    
    # --- Net Income (Multiple possibilities, logic should be robust) ---
    're_net_profit': 'net_income',            # Most direct, found in Banks
    're_proloss_ord_act': 'net_income',       # "Profit/(loss) for the period"
    're_con_pro_loss': 'net_income',          # "Consolidated Net Profit/Loss for the period"
    'dis_opr_aftr_tax_plus_ord_act': 'net_income', # "Profit (Loss) for the period from continuing operations"

    # --- Earnings Per Share ---
    're_basic_eps_for_cont_dic_opr': 'earnings_per_share_basic',
    're_basic_eps': 'earnings_per_share_basic',
    're_bsc_eps_bfr_exi': 'earnings_per_share_basic',
    
    're_dilut_eps_for_cont_dic_opr': 'earnings_per_share_diluted',
    're_diluted_eps': 'earnings_per_share_diluted',
    're_dil_eps_bfr_exi': 'earnings_per_share_diluted',

    # Note: Balance Sheet and Cash Flow data is not present in this API response.
    # We will get those from the XBRL source during reconciliation.
}