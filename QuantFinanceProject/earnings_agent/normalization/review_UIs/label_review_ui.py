# earnings_agent/normalization/review_UIs/label_review_ui.py

import streamlit as st
import sys
import json
from pathlib import Path
from datetime import datetime
from sqlalchemy import text

# --- 1. Environment and Path Setup ---
try:
    # This path may need adjustment depending on where you run the script from.
    # It assumes the script is in earnings_agent/normalization/review_UIs/
    project_root = Path(__file__).resolve().parents[3]
    if str(project_root) not in sys.path:
        sys.path.append(str(project_root))

    from earnings_agent.storage.database import (
        get_session,
        fetch_pending_label_reviews,
        update_label_mapping_status
    )
    from earnings_agent.storage.models import LabelMapping
except ImportError as e:
    st.error(f"Failed to import project modules. Error: {e}")
    st.stop()


# --- 2. Database Interaction Functions ---

def find_first_figure_context(session, raw_label: str) -> dict:
    """
    Finds the first occurrence of a raw_label in the staged data and returns its context.
    This is an expensive query and should be used with caching.
    """
    # This raw SQL query finds the first document that contains the raw_label
    # and unnests its JSON to find the matching figure's details.
    sql = """
    WITH first_doc AS (
        SELECT doc_id, normalized_data
        FROM earnings_data.staged_normalized_data
        WHERE
            normalized_data -> 'unit_normalized_data' -> 'llm_unit_analysis' -> 'statement_analyses' @> :label_json
        ORDER BY doc_id
        LIMIT 1
    )
    SELECT
        (figure ->> 'value')::numeric AS value,
        figure ->> 'representation' AS representation,
        figure ->> 'currency_context' AS currency_context
    FROM
        first_doc,
        jsonb_to_recordset(first_doc.normalized_data -> 'unit_normalized_data' -> 'llm_unit_analysis' -> 'statement_analyses') AS analysis(figures JSONB),
        jsonb_array_elements(analysis.figures) AS figure
    WHERE
        figure ->> 'label' = :raw_label
    LIMIT 1;
    """
    
    label_json_query = json.dumps([{"figures": [{"label": raw_label}]}])
    
    result = session.execute(
        text(sql), 
        {'label_json': label_json_query, 'raw_label': raw_label}
    ).first()
    
    if result:
        return {
            "value": result.value,
            "representation": result.representation,
            "currency_context": result.currency_context
        }
    return {}


# --- 3. Main Streamlit Application ---

# Page configuration
st.set_page_config(
    layout="wide",
    page_title="Label Normalization Reviewer",
    page_icon="🏷️"
)

st.title("🏷️ Label Normalization Review")
st.caption("Review and approve LLM-suggested mappings. Your approvals create the ground truth for the system.")

# Fetch all pending reviews
try:
    db_session = get_session()
    pending_reviews = fetch_pending_label_reviews()

    if not pending_reviews:
        st.success("🎉 No pending label reviews found! The queue is clear.")
        st.balloons()
        st.stop()

    st.info(f"Found **{len(pending_reviews)}** unique labels pending review.")

    # Display header for the columns
    col1, col2, col3, col4 = st.columns([3, 2, 2, 2])
    col1.markdown("**Raw Label from Document**")
    col2.markdown("**Example Context**")
    col3.markdown("**Editable Mapping**")
    col4.markdown("**Actions**")
    st.markdown("---")

    # Iterate through pending items and display them
    for item in pending_reviews:
        with st.container():
            col1, col2, col3, col4 = st.columns([3, 2, 2, 2])

            with col1:
                st.markdown(f"`{item.raw_label}`")
                with st.expander("Show Source Context"):
                    st.json(item.source_context)

            with col2:
                # Use caching to avoid re-querying the DB on every UI interaction
                @st.cache_data(show_spinner="Fetching context...")
                def get_cached_context(label, industry):
                    # Create a new session within the cached function for thread safety
                    session = get_session()
                    try:
                        return find_first_figure_context(session, label)
                    finally:
                        session.close()

                context = get_cached_context(item.raw_label, item.industry)

                value = context.get('value')
                representation = context.get('representation', 'N/A')
                currency = context.get('currency_context', '')

                if value is None:
                    st.warning("Value: null")
                else:
                    # Display industry in the metric label for context
                    st.metric(label=f"{item.industry} ({currency})", value=f"{value:,}")
                st.caption(f"Rep: {representation}")

            with col3:
                unique_key = f"map_{item.raw_label}_{item.industry}"
                # Handle None from DB for the text input
                current_suggestion = item.normalized_label or "" 
                edited_label = st.text_input(
                    "Suggested Mapping",
                    value=current_suggestion,
                    key=unique_key,
                    label_visibility="collapsed"
                )
            
            with col4:
                approve_key = f"approve_{unique_key}"
                if st.button("✅ Approve", key=approve_key, use_container_width=True):
                    try:
                        update_label_mapping_status(item.raw_label, item.industry, "APPROVED", new_label=edited_label)
                        st.toast(f"Approved: '{item.raw_label}' -> '{edited_label}'", icon="✅")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed to approve '{item.raw_label}': {e}")

                reject_key = f"reject_{unique_key}"
                if st.button("❌ Reject", key=reject_key, use_container_width=True):
                    try:
                        update_label_mapping_status(item.raw_label, item.industry, "REJECTED")
                        st.toast(f"Rejected: '{item.raw_label}'", icon="❌")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed to reject '{item.raw_label}': {e}")
        st.markdown("---")
finally:
    # Ensure the session is closed when the script finishes or is interrupted
    if 'db_session' in locals() and db_session.is_active:
        db_session.close()