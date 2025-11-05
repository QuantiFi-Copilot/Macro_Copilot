# /app/earnings_agent/quality_engine/stage3/review_UI/stage_3_review_ui.py

import streamlit as st
from typing import Dict, List

# Ensure the path is correct to import from the parent directory
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parents[1]))

from stage_3_review_utils import (
    get_pending_stage_3_reviews,
    get_null_metrics_for_statement,
    handle_approval,
    handle_rejection
)

# --- Streamlit Page Configuration ---
st.set_page_config(page_title="Stage 3 Label Mapping Review", layout="wide", page_icon="🏷️")

def initialize_state():
    """Load data into session state if it doesn't exist."""
    if 'pending_reviews' not in st.session_state:
        st.session_state.pending_reviews = get_pending_stage_3_reviews()
    if 'user_name' not in st.session_state:
        st.session_state.user_name = "analyst"

def format_currency(value):
    """Formats a number as currency for display."""
    if value is None:
        return "N/A"
    return f"₹{value:,.2f}"

def main():
    st.title("🏷️ Stage 3 - Label Mapping Review")
    st.markdown("Review and approve or reject AI-proposed mappings for unmapped financial metrics.")
    st.markdown("---")
    
    initialize_state()

    with st.sidebar:
        st.header("Filters & Actions")
        # --- NEW: Added user name input ---
        st.session_state.user_name = st.text_input("Your Name/ID", value=st.session_state.user_name)
        
        if st.button("🔄 Refresh Review Queue"):
            st.session_state.pending_reviews = get_pending_stage_3_reviews()
            st.rerun()

    if not st.session_state.pending_reviews:
        st.success("🎉 No pending label mapping reviews found!")
        return

    # Display each document that has pending mappings
    for doc in st.session_state.pending_reviews:
        with st.expander(f"📄 **{doc['company']}** - {doc['period']}", expanded=True):
            display_document_review_ui(doc)

def display_document_review_ui(doc: Dict):
    """Creates the UI for a single document's pending mappings."""
    st.info(f"This document has **{len(doc['pending_mappings'])}** pending mapping(s) for review.")
    
    for i, mapping in enumerate(doc['pending_mappings']):
        st.markdown("---")
        unique_key_prefix = f"{doc['run_id']}_{mapping['cache_id']}_{i}"
        
        col1, col2 = st.columns([2, 3])
        
        with col1:
            st.markdown(f"#### Unmapped Metric")
            with st.container(border=True):
                st.metric(label=f"`{mapping['raw_label']}`", value=format_currency(mapping['value']))
                st.caption(f"From statement: `{mapping['statement_key']}`")

        with col2:
            st.markdown("#### Review & Action")
            
            # --- Standard Mapping Workflow ---
            if mapping['suggested_mapping_type'] == 'standard':
                st.success(f"AI Suggestion: Map to **`{mapping['suggested_normalized_label']}`** (Standard Metric)")

                null_metrics = get_null_metrics_for_statement(doc['working_content'], mapping['statement_key'])
                options = [f"USE AI SUGGESTION: {mapping['suggested_normalized_label']}"] + list(null_metrics.keys())
                
                selected_option = st.selectbox("Choose mapping target:", options, key=f"select_{unique_key_prefix}")
                
                if st.button("✅ Approve Mapping", key=f"approve_{unique_key_prefix}", type="primary"):
                    target_playbook_id = ""
                    if "USE AI SUGGESTION" in selected_option:
                        target_playbook_id = mapping['suggested_normalized_label']
                    else:
                        target_playbook_id = null_metrics[selected_option]

                    approved_mapping_details = {
                        "raw_label": mapping['raw_label'],
                        "statement_key": mapping['statement_key'],
                        "target_playbook_id": target_playbook_id
                    }
                    # --- FIXED: Pass the user_name string ---
                    result = handle_approval(doc['run_id'], mapping['cache_id'], approved_mapping_details, st.session_state.user_name)
                    if result['status'] == 'success':
                        st.success(result['message'])
                        st.session_state.pending_reviews = get_pending_stage_3_reviews()
                        st.rerun()
                    else:
                        st.error(result['message'])

            # --- Company-Specific KPI Workflow ---
            else:
                st.info(f"AI Suggestion: Treat as **Company-Specific KPI** with name: `{mapping['suggested_normalized_label']}`")
                
                if st.button("✅ Confirm as KPI", key=f"approve_kpi_{unique_key_prefix}", type="primary"):
                    # --- FIXED: Pass the user_name string instead of the mapping dict ---
                    result = handle_rejection(doc['run_id'], mapping['cache_id'], st.session_state.user_name)
                    if result['status'] == 'success':
                        st.success(result['message'])
                        st.session_state.pending_reviews = get_pending_stage_3_reviews()
                        st.rerun()
                    else:
                        st.error(result['message'])

            # --- Rejection Workflow ---
            if st.button("❌ Reject Suggestion (Treat as KPI)", key=f"reject_{unique_key_prefix}"):
                # --- FIXED: Pass the user_name string instead of the mapping dict ---
                result = handle_rejection(doc['run_id'], mapping['cache_id'], st.session_state.user_name)
                if result['status'] == 'success':
                    st.success(result['message'])
                    st.session_state.pending_reviews = get_pending_stage_3_reviews()
                    st.rerun()
                else:
                    st.error(result['message'])


if __name__ == "__main__":
    main()