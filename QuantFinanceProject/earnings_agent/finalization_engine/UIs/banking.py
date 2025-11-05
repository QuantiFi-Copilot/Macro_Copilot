# /app/earnings_agent/finalization_engine/UIs/banking.py

import sys
import yaml
import json
from html import escape
from pathlib import Path
from typing import Dict, Any, List, Optional

import streamlit as st
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import joinedload

# --- Path Setup ---
PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.append(str(PROJECT_ROOT))

from earnings_agent.storage.database import get_session
from earnings_agent.storage.models import FundamentalRecord, FundamentalsBanking, CustomKpis

# --- Page Configuration ---
st.set_page_config(
    page_title="Financials Review",
    page_icon="📊",
    layout="wide"
)

# --- Constants ---
PLAYBOOK_PATH = PROJECT_ROOT / "earnings_agent" / "playbooks" / "sebi" / "metrics" / "sebi_banking.yml"
EXPECTATIONS_PATH = PROJECT_ROOT / "earnings_agent" / "playbooks" / "sebi" / "expected_metadata" / "banking_expectations.json"

# --- Data Loading & Caching ---

@st.cache_data(ttl=600)
def load_playbook_hierarchy() -> Dict[str, List[Dict]]:
    """Loads the hierarchical structure from the banking playbook."""
    with open(PLAYBOOK_PATH, "r", encoding="utf-8") as f:
        playbook = list(yaml.safe_load_all(f))
    hierarchy = {
        "pnl": next((doc['nodes'] for doc in playbook if doc['statement'] == 'pnl'), []),
        "balance_sheet": next((doc['nodes'] for doc in playbook if doc['statement'] == 'balance_sheet'), []),
        "cash_flow": next((doc['nodes'] for doc in playbook if doc['statement'] == 'cash_flow_indirect'), [])
    }
    return hierarchy

@st.cache_data(ttl=600)
def load_expectations() -> Dict[str, Any]:
    """Loads and flattens the expectations file for easy lookup by playbook_id."""
    with open(EXPECTATIONS_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)
    id_map = {}
    for section in data.values():
        id_map.update(section.get("ids", {}))
    return id_map

@st.cache_data(ttl=600)
def get_filter_options() -> Dict[str, List[str]]:
    """Queries the database for available tickers and periods."""
    with get_session() as session:
        tickers = session.execute(select(FundamentalRecord.ticker).distinct().order_by(FundamentalRecord.ticker)).scalars().all()
        periods = session.execute(select(FundamentalRecord.period).distinct().order_by(FundamentalRecord.period.desc())).scalars().all()
        return {"tickers": tickers, "periods": periods}

@st.cache_data(ttl=30)
def get_fundamental_data(ticker: str, period: str, consolidation_status: str) -> Optional[Dict[str, Any]]:
    """Fetches all data for a specific record."""
    with get_session() as session:
        stmt = select(FundamentalRecord).where(
            FundamentalRecord.ticker == ticker,
            FundamentalRecord.period == period,
            FundamentalRecord.consolidation_status == consolidation_status
        ).options(joinedload(FundamentalRecord.banking))
        record = session.execute(stmt).scalar_one_or_none()
        
        if not record or not record.banking:
            return None
            
        kpi_stmt = select(CustomKpis).where(CustomKpis.record_id == record.id)
        kpis = session.execute(kpi_stmt).scalar_one_or_none()

        data_map = {key: value for key, value in record.banking.__dict__.items() if not key.startswith('_')}
        data_map['custom_kpis'] = kpis.kpi_data if kpis else {}
        
        return data_map

# --- UI Rendering & Data Structuring Functions ---

def format_value(value: Optional[float], rep: str) -> str:
    """Formats a raw numerical value for display."""
    if value is None:
        return "—"
    if rep == 'percentage':
        return f"{value:.2f} %"
    if rep == 'ratio':
        if abs(value - int(value)) < 0.001 and value < 100:
             return f"₹ {value:,.0f}"
        return f"{value:.2f}"
    if rep == 'currency':
        val_in_crores = value / 10_000_000
        return f"₹ {val_in_crores:,.2f} Cr"
    return str(value)

def build_hierarchical_data(
    nodes: List[Dict],
    data_map: Dict[str, Any],
    expectations: Dict,
    level: int = 0
) -> List[Dict]:
    """
    Recursively builds rows that include BOTH header rows (non-extractable playbook nodes)
    and metric rows (extractable leaves). Indentation uses NBSP and is later rendered inline.
    """
    rows = []
    NBSP = "\u00A0"

    for node in nodes:
        nid = node["id"]
        is_extractable = node.get("extractable", True)
        exp = expectations.get(nid, {})
        label = exp.get("label", nid.replace("_", " ").title())
        rep = exp.get("representation", "currency")

        indent = NBSP * (level * 4)
        bullet = "" if level == 0 else "• "

        if is_extractable:
            value = data_map.get(nid)
            value_str = format_value(value, rep)
            kind = "total" if label.lower().startswith(("total ", "net ", "profit (loss)")) else "item"
            rows.append({
                "Metric": f"{indent}{bullet}{label}",
                "Value": value_str,
                "level": level,
                "kind": kind,
            })
        else:
            rows.append({
                "Metric": f"{indent}{label}",
                "Value": "",
                "level": level,
                "kind": "header",
            })

        if node.get("children"):
            rows.extend(
                build_hierarchical_data(node["children"], data_map, expectations, level + 1)
            )

    return rows

def _hierarchy_table_html(rows: List[Dict]) -> str:
    """
    Return a static, inline HTML table (no inner scroll). Uses simple CSS for headers,
    totals, indentation, and right-aligned values.
    """
    # CSS sits once per table; Streamlit will render inline and the PAGE scrolls.
    css = """
    <style>
      .fin-table { width: 100%; border-collapse: collapse; table-layout: fixed; }
      .fin-table th, .fin-table td { padding: 8px 10px; border-bottom: 1px solid #EEE; vertical-align: top; }
      .fin-table th { text-align: left; color: #334155; font-weight: 700; font-size: 1.4rem; line-height: 1.3; font-family: font-family: "Segoe UI"; }
      .fin-table td.metric { white-space: pre; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace; }
      .fin-table td.value { text-align: right; white-space: nowrap; }
      .fin-table tr.header > td { font-weight: 700; background: #F6F8FB; border-top: 1px solid #EAECEF; }
      .fin-table tr.total > td { font-weight: 600; border-top: 1px solid #ECEFF3; }
      .fin-table col.metric { width: 75%; }
      .fin-table col.value { width: 25%; }
    </style>
    """

    # Build rows
    body_rows = []
    for r in rows:
        cls = "header" if r["kind"] == "header" else ("total" if r["kind"] == "total" else "")
        metric = escape(r["Metric"])   # preserves NBSP chars
        value = escape(r["Value"]) if isinstance(r["Value"], str) else r["Value"]
        body_rows.append(f"<tr class='{cls}'><td class='metric'>{metric}</td><td class='value'>{value}</td></tr>")

    html = f"""
    {css}
    <table class="fin-table">
      <colgroup>
        <col class="metric" />
        <col class="value" />
      </colgroup>
      <thead>
        <tr><th>Metric</th><th>Value</th></tr>
      </thead>
      <tbody>
        {''.join(body_rows)}
      </tbody>
    </table>
    """
    return html

def render_statement_tab(data: Optional[Dict[str, Any]], playbook_hierarchy: Dict, expectations: Dict):
    """Renders the full content for a single tab (Standalone or Consolidated) without inner scroll."""
    if not data:
        st.info("No data found for this selection.")
        return

    st.subheader("Profit & Loss Statement")
    pnl_rows = build_hierarchical_data(playbook_hierarchy['pnl'], data, expectations)
    st.markdown(_hierarchy_table_html(pnl_rows), unsafe_allow_html=True)

    st.subheader("Balance Sheet")
    bs_rows = build_hierarchical_data(playbook_hierarchy['balance_sheet'], data, expectations)
    st.markdown(_hierarchy_table_html(bs_rows), unsafe_allow_html=True)

    st.subheader("Cash Flow Statement")
    cf_rows = build_hierarchical_data(playbook_hierarchy['cash_flow'], data, expectations)
    st.markdown(_hierarchy_table_html(cf_rows), unsafe_allow_html=True)
        
    if data.get('custom_kpis'):
        with st.expander("Company-Specific KPIs"):
            for kpi_name, kpi_value in data['custom_kpis'].items():
                st.metric(label=kpi_name.replace('_', ' ').title(), value=format_value(kpi_value, 'ratio'))

def main():
    """The main Streamlit application."""
    st.title("📊 Financial Statement Review")

    # Load config data once
    playbook_hierarchy = load_playbook_hierarchy()
    expectations = load_expectations()
    filter_options = get_filter_options()

    # Sidebar
    with st.sidebar:
        st.header("Filters")
        if not filter_options["tickers"] or not filter_options["periods"]:
            st.warning("No data found in the database.")
            return

        selected_ticker = st.selectbox("Select Company Ticker", filter_options["tickers"])
        selected_period = st.selectbox("Select Financial Period", filter_options["periods"])

    # Main Display
    st.header(f"Displaying financials for **{selected_ticker}** | **{selected_period}**")

    standalone_tab, consolidated_tab = st.tabs(["**Standalone**", "**Consolidated**"])

    # Fetch data
    standalone_data = get_fundamental_data(selected_ticker, selected_period, "Standalone")
    consolidated_data = get_fundamental_data(selected_ticker, selected_period, "Consolidated")

    # Render Tabs
    with standalone_tab:
        render_statement_tab(standalone_data, playbook_hierarchy, expectations)

    with consolidated_tab:
        render_statement_tab(consolidated_data, playbook_hierarchy, expectations)

if __name__ == "__main__":
    main()
