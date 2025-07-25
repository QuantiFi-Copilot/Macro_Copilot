# qfin_tools/normalization_reviewer/app.py

import streamlit as st
import os
import sys
from pathlib import Path
from datetime import datetime

# --- 1. Environment and Path Setup ---
# This is crucial for allowing the standalone Streamlit app to import modules
# from the main `earnings_agent` directory.
try:
    # Get the project root directory (which is 2 levels up from this script's location)
    # qfin_project_root/qfin_tools/normalization_reviewer/app.py
    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.append(str(project_root))

    # Now we can import from the earnings_agent module
    from earnings_agent.storage.database import get_session
    from earnings_agent.storage.models import LabelMapping
except ImportError as e:
    st.error(f"Failed to import project modules. Ensure the script is in the correct directory `qfin_tools/normalization_reviewer/` and that your project structure is correct. Error: {e}")
    st.stop()


# --- 2. Database Interaction Functions ---

def fetch_pending_reviews(session):
    """Queries the database for all records with 'PENDING_REVIEW' status."""
    return session.query(LabelMapping).filter(LabelMapping.status == 'PENDING_REVIEW').order_by(LabelMapping.created_at.desc()).all()

def update_mapping_status(session, raw_label, new_status):
    """Updates the status of a specific mapping in the database."""
    try:
        mapping_to_update = session.query(LabelMapping).filter(LabelMapping.raw_label == raw_label).one()
        mapping_to_update.status = new_status
        mapping_to_update.last_reviewed_at = datetime.utcnow()
        mapping_to_update.reviewed_by = "ui_human_reviewer"
        session.commit()
        st.toast(f"Updated '{raw_label}' to {new_status}", icon="✅")
    except Exception as e:
        session.rollback()
        st.error(f"Failed to update status for '{raw_label}': {e}")
    finally:
        session.close()

# --- 3. Main Streamlit Application ---

def main():
    st.set_page_config(layout="wide", page_title="Normalization Reviewer")
    st.title("Normalization Mapping Review")
    st.caption("Review LLM-suggested mappings for financial labels.")

    db_session = get_session()
    pending_reviews = fetch_pending_reviews(db_session)

    if not pending_reviews:
        st.success("🎉 No pending reviews found! The queue is clear.")
        st.balloons()
        return

    st.info(f"Found **{len(pending_reviews)}** items pending review.")

    for item in pending_reviews:
        # Use a container with a border for each review item to create a "card" effect
        with st.container(border=True):
            col1, col2, col3 = st.columns([2, 2, 1])

            with col1:
                st.markdown(f"**Raw Label:**")
                st.markdown(f"##### `{item.raw_label}`")

            with col2:
                st.markdown(f"**LLM Suggestion:**")
                st.markdown(f"##### `{item.normalized_label}`")

            with col3:
                # Use the raw_label to create a unique key for each button
                if st.button("✅ Approve", key=f"approve_{item.raw_label}", use_container_width=True):
                    update_mapping_status(db_session, item.raw_label, "APPROVED")
                    st.rerun()

                if st.button("❌ Reject", key=f"reject_{item.raw_label}", use_container_width=True):
                    update_mapping_status(db_session, item.raw_label, "REJECTED")
                    st.rerun()

            # Display the context in an expander to keep the UI clean
            with st.expander("Show Context"):
                st.json(item.source_context)

    db_session.close()


if __name__ == "__main__":
    main()