# streamlit_apps/unit_review_ui.py

import streamlit as st
import sys 
from pathlib import Path
try:
    # Go up 3 levels from the script's location to find the project root (/app)
    project_root = Path(__file__).resolve().parents[3]
    if str(project_root) not in sys.path:
        sys.path.append(str(project_root))
except IndexError:
    # Handle cases where the script is not in the expected directory
    st.error("Could not find project root. Please ensure the script is in the correct directory.")
    st.stop()
import json
import time
from datetime import datetime
from typing import Dict, List, Any

# Import database functions
from earnings_agent.storage.database import (
    get_session,
    get_pending_unit_reviews,
    approve_unit_review,
    delete_processed_unit_review
)
from earnings_agent.storage.models import UnitReviewQueue

# Page configuration
st.set_page_config(
    page_title="Unit Normalization Review",
    page_icon="🔍",
    layout="wide"
)

def get_representation_options(representation: str) -> List[str]:
    """
    Return dropdown options based on representation type.
    """
    options_map = {
        'currency': ['lakhs', 'crores', 'millions', 'thousands', 'rupees'],
        'percentage': ['percentage'],
        'ratio': ['percentage', 'absolute', 'basis_points'],
        'count': ['count']
    }
    return options_map.get(representation, ['unknown'])

def get_ratio_context_options(representation: str) -> List[str]:
    """
    Return ratio context options based on representation type.
    """
    if representation in ['percentage', 'ratio']:
        return ['percentage', 'absolute', 'basis_points']
    return ['null']

def display_figure_review(figure: Dict, statement_info: Dict, review_id: int, figure_index: int) -> Dict:
    """
    Display individual figure for review and return user corrections.
    """
    col1, col2, col3, col4 = st.columns([3, 1, 2, 2])
    
    with col1:
        st.write(f"**{figure['label']}**")
        st.write(f"Value: {figure['value']}")
        st.write(f"Statement: {statement_info['standard_mapping']}")
    
    with col2:
        st.write("**LLM Analysis:**")
        st.write(f"Rep: {figure['representation']}")
        st.write(f"Confidence: {figure.get('confidence', 'unknown')}")
    
    with col3:
        st.write("**Currency Context:**")
        currency_options = get_representation_options(figure['representation'])
        
        # Find current selection index
        current_currency = figure.get('currency_context', 'unknown')
        try:
            current_idx = currency_options.index(current_currency)
        except ValueError:
            current_idx = 0
        
        selected_currency = st.selectbox(
            "Currency/Unit:",
            options=currency_options,
            index=current_idx,
            # --- MODIFIED LINE ---
            key=f"currency_{review_id}_{figure_index}_{figure['label']}"
        )
    
    with col4:
        st.write("**Ratio Context:**")
        ratio_options = get_ratio_context_options(figure['representation'])
        
        # Find current selection index
        current_ratio = figure.get('ratio_context', 'null')
        try:
            current_ratio_idx = ratio_options.index(current_ratio)
        except ValueError:
            current_ratio_idx = 0
        
        selected_ratio = st.selectbox(
            "Ratio Type:",
            options=ratio_options,
            index=current_ratio_idx,
            # --- MODIFIED LINE ---
            key=f"ratio_{review_id}_{figure_index}_{figure['label']}"
        )
    
    # Show reasoning if available
    if figure.get('reasoning'):
        st.info(f"LLM Reasoning: {figure['reasoning']}")
    
    # Return user corrections
    corrections = {
        'label': figure['label'],
        'currency_context': selected_currency if selected_currency != 'unknown' else None,
        'ratio_context': selected_ratio if selected_ratio != 'null' else None,
        'representation': figure['representation']  # Keep original representation
    }
    
    return corrections
def display_filing_summary(review: UnitReviewQueue):
    """
    Display summary information about the filing under review.
    """
    st.subheader(f"Filing: {review.ticker} - {review.fiscal_date}")
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        st.metric("Company", review.ticker)
        st.metric("Fiscal Date", str(review.fiscal_date))
    
    with col2:
        filing_data = review.filing_data
        st.metric("Total Figures", filing_data.get('total_figures_analyzed', 0))
        st.metric("Suspicious Figures", filing_data.get('low_confidence_count', 0))
    
    with col3:
        st.metric("Created", review.created_at.strftime("%Y-%m-%d %H:%M"))
        st.metric("Status", review.status)

def create_corrected_analysis(original_analysis: Dict, user_corrections: List[Dict]) -> Dict:
    """
    Apply user corrections to the original LLM analysis.
    """
    corrected_analysis = json.loads(json.dumps(original_analysis))  # Deep copy
    
    # Create correction mapping by label
    corrections_map = {corr['label']: corr for corr in user_corrections}
    
    # Apply corrections to each statement
    for stmt_analysis in corrected_analysis['statement_analyses']:
        for figure in stmt_analysis['figures']:
            if figure['label'] in corrections_map:
                correction = corrections_map[figure['label']]
                
                # Apply user corrections
                figure['currency_context'] = correction['currency_context']
                figure['ratio_context'] = correction['ratio_context']
                
                # Mark as human reviewed
                figure['confidence'] = 'high'  # Human review makes it high confidence
                figure['reasoning'] = 'Human reviewed and corrected'
    
    # Update filing analysis
    corrected_analysis['filing_analysis']['requires_human_review'] = False
    corrected_analysis['filing_analysis']['confidence_summary'] = 'Human reviewed and approved'
    
    return corrected_analysis

def main():
    st.title("🔍 Unit Normalization Review")
    st.markdown("Review and correct unit normalization decisions for financial figures")
    
    # Get pending reviews
    session = get_session()
    try:
        pending_reviews = get_pending_unit_reviews()
    finally:
        session.close()
    
    if not pending_reviews:
        st.success("🎉 No pending unit reviews! All filings have been processed.")
        st.stop()
    
    st.info(f"Found {len(pending_reviews)} filings pending review")
    
    # Select review to work on
    review_options = [
        f"{review.ticker} - {review.fiscal_date} ({review.filing_data.get('low_confidence_count', 0)} figures)"
        for review in pending_reviews
    ]
    
    selected_idx = st.selectbox(
        "Select filing to review:",
        range(len(review_options)),
        format_func=lambda x: review_options[x]
    )
    
    if selected_idx is None:
        st.stop()
    
    current_review = pending_reviews[selected_idx]
    
    # Display filing summary
    display_filing_summary(current_review)
    
    st.markdown("---")
    
    # Get suspicious figures
    suspicious_figures = current_review.filing_data.get('suspicious_figures', [])
    
    if not suspicious_figures:
        st.warning("No suspicious figures found in this filing.")
        
        # Auto-approve button
        if st.button("Auto-Approve Filing", type="primary"):
            session = get_session()
            try:
                approve_unit_review(current_review.id)
                session.commit()
                st.success("Filing approved successfully!")
                st.rerun()
            except Exception as e:
                st.error(f"Error approving filing: {e}")
                session.rollback()
            finally:
                session.close()
        st.stop()
    
    # Display figures for review
    st.subheader(f"Review {len(suspicious_figures)} Suspicious Figures")
    
    user_corrections = []
    
    for i, figure in enumerate(suspicious_figures):
        st.markdown(f"### Figure {i+1}")
        
        # Create statement info
        statement_info = {
            'standard_mapping': figure.get('standard_mapping', 'unknown'),
            'statement_type': figure.get('statement_type', 'unknown')
        }
        
        # --- MODIFIED LINE: Pass the index `i` ---
        correction = display_figure_review(figure, statement_info, current_review.id, figure_index=i)
        user_corrections.append(correction)
        
        st.markdown("---")
    
    # Approval buttons
    col1, col2, col3 = st.columns([1, 1, 1])
    
    with col1:
        if st.button("❌ Reject Filing", type="secondary"):
            st.warning("Rejection functionality not implemented yet.")
    
    with col2:
        if st.button("💾 Save Progress", type="secondary"):
            st.info("Auto-save functionality not implemented yet.")
    
    with col3:
        if st.button("✅ Approve Filing", type="primary"):
            try:
                # Create corrected analysis
                corrected_analysis = create_corrected_analysis(
                    current_review.llm_analysis, 
                    user_corrections
                )
                
                # Approve with corrections
                session = get_session()
                try:
                    approve_unit_review(current_review.id, corrected_analysis)
                    session.commit()
                    st.success("Filing approved with corrections!")
                    st.balloons()
                    time.sleep(2)
                    st.rerun()
                    
                except Exception as e:
                    st.error(f"Error approving filing: {e}")
                    session.rollback()
                finally:
                    session.close()
                    
            except Exception as e:
                st.error(f"Error creating corrections: {e}")

    # Display raw data for debugging (collapsible)
    with st.expander("🔍 Debug Information"):
        st.subheader("LLM Analysis")
        st.json(current_review.llm_analysis)
        
        st.subheader("Filing Data")
        st.json(current_review.filing_data)

if __name__ == "__main__":
    main()