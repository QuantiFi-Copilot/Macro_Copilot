# /app/earnings_agent/quality_engine/review_ui.py

import streamlit as st
import pandas as pd
from typing import Dict, List, Any
from copy import deepcopy

# Import our backend functions
from review_utils import (
    get_pending_reviews, get_metadata_options, get_metric_from_working_content,
    add_missing_metric_to_content, update_metric_in_content, approve_stage_1_fix
)
from earnings_agent.quality_engine.expectations_utils import load_expectations

# Page configuration
st.set_page_config(
    page_title="Stage 1 Quality Engine Review",
    page_icon="🔍",
    layout="wide"
)

def main():
    st.title("🔍 Stage 1 Quality Engine Review")
    st.markdown("---")
    
    # Load data
    if 'pending_reviews' not in st.session_state:
        st.session_state.pending_reviews = get_pending_reviews()
    
    # Sidebar filters
    with st.sidebar:
        st.header("Filters")
        
        # Error type filter
        error_types = ['All'] + list(set([item['error_type'] for item in st.session_state.pending_reviews]))
        selected_error_type = st.selectbox("Error Type", error_types)
        
        # Company filter
        companies = ['All'] + sorted(list(set([item['company'] for item in st.session_state.pending_reviews])))
        selected_company = st.selectbox("Company", companies)
        
        # Refresh button
        if st.button("🔄 Refresh Data"):
            st.session_state.pending_reviews = get_pending_reviews()
            st.rerun()
    
    # Filter data
    filtered_reviews = filter_reviews(
        st.session_state.pending_reviews, 
        selected_error_type, 
        selected_company
    )
    
    # Summary statistics
    display_summary_stats(filtered_reviews)
    
    # Main review interface
    if not filtered_reviews:
        st.info("🎉 No pending reviews found! All documents have passed Stage 1 validation.")
        return
    
    # Display reviews
    st.subheader("Pending Reviews")
    
    for i, review_item in enumerate(filtered_reviews):
        with st.expander(
            f"📄 {review_item['company']} - Q{review_item['quarter']} FY{review_item['fiscal_year']} "
            f"({review_item['error_type'].replace('_', ' ').title()})",
            expanded=i == 0  # Expand first item by default
        ):
            display_review_item(review_item, i)

def filter_reviews(reviews: List[Dict], error_type: str, company: str) -> List[Dict]:
    """Filter reviews based on selected criteria."""
    filtered = reviews
    
    if error_type != 'All':
        filtered = [r for r in filtered if r['error_type'] == error_type]
    
    if company != 'All':
        filtered = [r for r in filtered if r['company'] == company]
    
    return filtered

def display_summary_stats(reviews: List[Dict]):
    """Display summary statistics."""
    if not reviews:
        return
    
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.metric("Total Issues", len(reviews))
    
    with col2:
        completeness_count = len([r for r in reviews if r['error_type'] == 'COMPLETENESS_ERROR'])
        st.metric("Completeness Errors", completeness_count)
    
    with col3:
        metadata_count = len([r for r in reviews if r['error_type'] == 'METADATA_ERROR'])
        st.metric("Metadata Errors", metadata_count)
    
    with col4:
        ai_disagreement_count = len([r for r in reviews if r['error_type'] == 'AI_DISAGREEMENT_ERROR'])
        st.metric("AI Disagreements", ai_disagreement_count)
    
    st.markdown("---")

def display_review_item(review_item: Dict, index: int):
    """Display a single review item with appropriate UI for its error type."""
    error_type = review_item['error_type']
    
    # Common context information
    st.info(f"**Company:** {review_item['company']} | **Period:** Q{review_item['quarter']} FY{review_item['fiscal_year']} | **Error Type:** {error_type.replace('_', ' ').title()}")
    
    if error_type == 'COMPLETENESS_ERROR':
        handle_completeness_error(review_item, index)
    elif error_type == 'METADATA_ERROR':
        handle_metadata_error(review_item, index)
    elif error_type == 'AI_DISAGREEMENT_ERROR':
        handle_ai_disagreement_error(review_item, index)
    elif error_type == 'ORDER_ERROR':
        handle_order_error(review_item, index)

def handle_completeness_error(review_item: Dict, index: int):
    """Handle completeness error reviews."""
    st.subheader("🚨 Missing Metrics")
    
    error_details = review_item['error_details']
    missing_ids = error_details.get('missing_ids', [])
    statement_type = error_details.get('statement', 'Unknown')
    
    st.warning(f"**Statement:** {statement_type}")
    st.error(f"**Missing Metrics:** {', '.join(missing_ids)}")
    
    # Load expectations for metadata validation
    expectations = load_expectations()
    metadata_options = get_metadata_options()
    
    # Form for each missing metric
    for metric_id in missing_ids:
        st.markdown(f"### 📊 Add Missing Metric: `{metric_id}`")
        
        with st.form(f"completeness_form_{index}_{metric_id}"):
            col1, col2 = st.columns(2)
            
            with col1:
                # Value input
                raw_label = st.text_input("Raw Label", value="Missing in Filing", key=f"raw_label_{index}_{metric_id}")
                value = st.text_input("Value (or leave empty for null)", key=f"value_{index}_{metric_id}")
                
                # Parse value
                parsed_value = None
                if value.strip():
                    try:
                        parsed_value = float(value.strip())
                    except ValueError:
                        st.error("Please enter a valid number or leave empty for null")
                        continue
                
                confidence = st.selectbox("Confidence", ["high", "low"], key=f"confidence_{index}_{metric_id}")
            
            with col2:
                # Get expected metadata for this metric
                expectation = expectations.get(metric_id, {})
                
                # Metadata inputs with defaults from expectations
                representation = st.selectbox(
                    "Representation", 
                    metadata_options['representation'], 
                    index=metadata_options['representation'].index(expectation.get('representation', 'currency')) if expectation.get('representation') in metadata_options['representation'] else 0,
                    key=f"representation_{index}_{metric_id}"
                )
                
                currency_context = st.selectbox(
                    "Currency Context", 
                    [None] + metadata_options['currency_context'][:-1],  # Remove the duplicate None
                    index=0 if expectation.get('currency_context') is None else metadata_options['currency_context'][:-1].index(expectation.get('currency_context', 'INR')) + 1,
                    key=f"currency_context_{index}_{metric_id}"
                )
                
                unit_scale = st.selectbox(
                    "Unit Scale", 
                    [None] + metadata_options['unit_scale'][:-1],  # Remove the duplicate None
                    key=f"unit_scale_{index}_{metric_id}"
                )
                
                ratio_context = st.selectbox(
                    "Ratio Context", 
                    [None] + metadata_options['ratio_context'][:-1],  # Remove the duplicate None
                    index=0 if expectation.get('ratio_context') is None else metadata_options['ratio_context'][:-1].index(expectation.get('ratio_context', 'absolute')) + 1 if expectation.get('ratio_context') in metadata_options['ratio_context'][:-1] else 0,
                    key=f"ratio_context_{index}_{metric_id}"
                )
            
            # Approve button
            if st.form_submit_button("✅ Add Metric and Approve", type="primary"):
                # Create metric data
                metric_data = {
                    "playbook_id": metric_id,
                    "raw_label": raw_label,
                    "value": parsed_value,
                    "confidence": confidence,
                    "representation": representation if parsed_value is not None else None,
                    "currency_context": currency_context if parsed_value is not None else None,
                    "unit_scale": unit_scale if parsed_value is not None else None,
                    "ratio_context": ratio_context if parsed_value is not None else None
                }
                
                # Update working content
                updated_content = deepcopy(review_item['working_content'])
                updated_content = add_missing_metric_to_content(
                    updated_content, statement_type, metric_id, metric_data
                )
                
                # Approve the fix
                success = approve_stage_1_fix(
                    review_item['run_id'], 
                    updated_content, 
                    "COMPLETENESS_CHECK_PASSED"
                )
                
                if success:
                    st.success(f"✅ Successfully added metric {metric_id} and approved the document!")
                    st.session_state.pending_reviews = get_pending_reviews()  # Refresh data
                    st.rerun()
                else:
                    st.error("❌ Failed to update the document. Please try again.")

def handle_metadata_error(review_item: Dict, index: int):
    """Handle metadata error reviews."""
    st.subheader("🔧 Metadata Issues")
    
    error_details = review_item['error_details']
    affected_statements = error_details.get('affected_statements', [])
    
    expectations = load_expectations()
    metadata_options = get_metadata_options()
    
    for i, error_info in enumerate(affected_statements):
        statement_type = error_info['statement']
        playbook_id = error_info['playbook_id']
        error_msg = error_info['error']
        
        st.markdown(f"### 🎯 Fix Metadata: `{playbook_id}` in `{statement_type}`")
        st.error(f"**Error:** {error_msg}")
        
        # Get current metric data
        current_metric = get_metric_from_working_content(
            review_item['working_content'], statement_type, playbook_id
        )
        
        if not current_metric:
            st.error("Could not find metric in working content")
            continue
        
        with st.form(f"metadata_form_{index}_{i}"):
            col1, col2 = st.columns(2)
            
            with col1:
                st.markdown("**Current Values:**")
                st.text(f"Raw Label: {current_metric.get('raw_label', 'N/A')}")
                st.text(f"Value: {current_metric.get('value', 'N/A')}")
                st.text(f"Confidence: {current_metric.get('confidence', 'N/A')}")
            
            with col2:
                st.markdown("**Fix Metadata:**")
                
                # Get expected values
                expectation = expectations.get(playbook_id, {})
                
                representation = st.selectbox(
                    "Representation", 
                    metadata_options['representation'], 
                    index=metadata_options['representation'].index(expectation.get('representation', current_metric.get('representation', 'currency'))),
                    key=f"meta_representation_{index}_{i}"
                )
                
                currency_context = st.selectbox(
                    "Currency Context", 
                    [None] + metadata_options['currency_context'][:-1],
                    index=0 if expectation.get('currency_context') is None else metadata_options['currency_context'][:-1].index(expectation.get('currency_context', 'INR')) + 1,
                    key=f"meta_currency_context_{index}_{i}"
                )
                
                unit_scale = st.selectbox(
                    "Unit Scale", 
                    [None] + metadata_options['unit_scale'][:-1],
                    index=0 if current_metric.get('unit_scale') is None else metadata_options['unit_scale'][:-1].index(current_metric.get('unit_scale', 'crores')) + 1,
                    key=f"meta_unit_scale_{index}_{i}"
                )
                
                ratio_context = st.selectbox(
                    "Ratio Context", 
                    [None] + metadata_options['ratio_context'][:-1],
                    index=0 if expectation.get('ratio_context') is None else metadata_options['ratio_context'][:-1].index(expectation.get('ratio_context', 'absolute')) + 1 if expectation.get('ratio_context') in metadata_options['ratio_context'][:-1] else 0,
                    key=f"meta_ratio_context_{index}_{i}"
                )
            
            if st.form_submit_button("✅ Fix Metadata and Approve", type="primary"):
                # Update metric data
                updated_data = {
                    "representation": representation,
                    "currency_context": currency_context,
                    "unit_scale": unit_scale,
                    "ratio_context": ratio_context
                }
                
                # Update working content
                updated_content = deepcopy(review_item['working_content'])
                updated_content = update_metric_in_content(
                    updated_content, statement_type, playbook_id, updated_data
                )
                
                # Approve the fix
                success = approve_stage_1_fix(
                    review_item['run_id'], 
                    updated_content, 
                    "METADATA_CHECK_PASSED"
                )
                
                if success:
                    st.success(f"✅ Successfully fixed metadata for {playbook_id} and approved!")
                    st.session_state.pending_reviews = get_pending_reviews()
                    st.rerun()
                else:
                    st.error("❌ Failed to update the document. Please try again.")

def handle_ai_disagreement_error(review_item: Dict, index: int):
    """Handle AI disagreement error reviews."""
    st.subheader("🤖 AI Disagreements")
    
    error_details = review_item['error_details']
    
    # Debug information
    with st.expander("🔍 Debug Information", expanded=False):
        st.write("**Raw failure_reason:**", review_item.get('failure_reason', 'None'))
        st.write("**Error details:**", error_details)
        st.write("**Disagreements string:**", error_details.get('disagreements', 'None'))
    
    affected_metrics = error_details.get('affected_metrics', [])
    
    if not affected_metrics:
        st.error("❌ No disagreements found in the parsed data. Check debug information above.")
        
        # Try to parse manually from failure_reason as backup
        failure_reason = review_item.get('failure_reason', '')
        if 'LLM1=' in failure_reason and 'LLM2=' in failure_reason:
            st.warning("🔧 Attempting manual parsing from failure_reason...")
            
            # Manual parsing as backup
            try:
                # Extract from "LLM disagreements: standalone_pnl: other_income: LLM1=19074.5625, LLM2=18166.25"
                if 'LLM disagreements: ' in failure_reason:
                    disagreement_part = failure_reason.split('LLM disagreements: ')[1]
                    
                    # Parse manually
                    from review_utils import parse_ai_disagreements
                    manual_parsed = parse_ai_disagreements(disagreement_part)
                    
                    if manual_parsed:
                        st.success(f"✅ Found {len(manual_parsed)} disagreements via manual parsing!")
                        affected_metrics = manual_parsed
                    else:
                        st.error("❌ Manual parsing also failed")
            except Exception as e:
                st.error(f"❌ Manual parsing error: {e}")
    
    if not affected_metrics:
        st.error("❌ Could not parse any disagreements. Please check the data format.")
        return
    
    metadata_options = get_metadata_options()
    
    for i, disagreement in enumerate(affected_metrics):
        statement_type = disagreement['statement']
        playbook_id = disagreement['playbook_id']
        llm1_value = disagreement['llm1_value']
        llm2_value = disagreement['llm2_value']
        
        st.markdown(f"### 🔍 Resolve Disagreement: `{playbook_id}` in `{statement_type}`")
        
        # Get current metric for context
        current_metric = get_metric_from_working_content(
            review_item['working_content'], statement_type, playbook_id
        )
        
        if not current_metric:
            st.error(f"Could not find metric {playbook_id} in {statement_type}")
            continue
        
        with st.form(f"ai_disagreement_form_{index}_{i}"):
            col1, col2 = st.columns(2)
            
            with col1:
                st.markdown("**Current Metric Info:**")
                st.text(f"Raw Label: {current_metric.get('raw_label', 'N/A')}")
                st.text(f"Current Value: {current_metric.get('value', 'N/A')}")
                
                st.markdown("**LLM Disagreement:**")
                st.info(f"🤖 LLM 1 Value: {llm1_value}")
                st.info(f"🤖 LLM 2 Value: {llm2_value}")
                
                # Value choice
                value_choice = st.radio(
                    "Choose the correct value:",
                    ["LLM 1", "LLM 2", "Custom"],
                    key=f"value_choice_{index}_{i}"
                )
                
                if value_choice == "Custom":
                    custom_value = st.text_input("Enter custom value:", key=f"custom_value_{index}_{i}")
                    try:
                        final_value = float(custom_value) if custom_value.strip() else None
                    except ValueError:
                        st.error("Please enter a valid number")
                        final_value = None
                elif value_choice == "LLM 1":
                    try:
                        final_value = float(llm1_value) if llm1_value.lower() != 'null' else None
                    except ValueError:
                        final_value = None
                else:  # LLM 2
                    try:
                        final_value = float(llm2_value) if llm2_value.lower() != 'null' else None
                    except ValueError:
                        final_value = None
            
            with col2:
                st.markdown("**Metadata (if needed):**")
                
                # Only show metadata options if custom value or value changed significantly
                confidence = st.selectbox("Confidence", ["high", "low"], index=0, key=f"ai_confidence_{index}_{i}")
                
                representation = st.selectbox(
                    "Representation", 
                    metadata_options['representation'], 
                    index=metadata_options['representation'].index(current_metric.get('representation', 'currency')),
                    key=f"ai_representation_{index}_{i}"
                )
                
                currency_context = st.selectbox(
                    "Currency Context", 
                    [None] + metadata_options['currency_context'][:-1],
                    index=0 if current_metric.get('currency_context') is None else metadata_options['currency_context'][:-1].index(current_metric.get('currency_context', 'INR')) + 1,
                    key=f"ai_currency_context_{index}_{i}"
                )
                
                unit_scale = st.selectbox(
                    "Unit Scale", 
                    [None] + metadata_options['unit_scale'][:-1],
                    index=0 if current_metric.get('unit_scale') is None else metadata_options['unit_scale'][:-1].index(current_metric.get('unit_scale', 'crores')) + 1,
                    key=f"ai_unit_scale_{index}_{i}"
                )
                
                ratio_context = st.selectbox(
                    "Ratio Context", 
                    [None] + metadata_options['ratio_context'][:-1],
                    index=0 if current_metric.get('ratio_context') is None else metadata_options['ratio_context'][:-1].index(current_metric.get('ratio_context', 'absolute')) + 1 if current_metric.get('ratio_context') in metadata_options['ratio_context'][:-1] else 0,
                    key=f"ai_ratio_context_{index}_{i}"
                )
            
            if st.form_submit_button("✅ Resolve Disagreement and Approve", type="primary"):
                # Update metric data
                updated_data = {
                    "value": final_value,
                    "confidence": confidence,
                    "representation": representation if final_value is not None else None,
                    "currency_context": currency_context if final_value is not None else None,
                    "unit_scale": unit_scale if final_value is not None else None,
                    "ratio_context": ratio_context if final_value is not None else None
                }
                
                # Update working content
                updated_content = deepcopy(review_item['working_content'])
                updated_content = update_metric_in_content(
                    updated_content, statement_type, playbook_id, updated_data
                )
                
                # Approve the fix
                success = approve_stage_1_fix(
                    review_item['run_id'], 
                    updated_content, 
                    "PASSED"
                )
                
                if success:
                    st.success(f"✅ Successfully resolved disagreement for {playbook_id} and approved!")
                    st.session_state.pending_reviews = get_pending_reviews()
                    st.rerun()
                else:
                    st.error("❌ Failed to update the document. Please try again.")

def handle_order_error(review_item: Dict, index: int):
    """Handle order error reviews."""
    st.subheader("📋 Order Issues")
    st.warning("Order errors are typically auto-fixed. If you see this, there's a complex ordering issue that needs manual intervention.")
    
    # For now, just show the error and allow manual approval
    # In a full implementation, you might show the current vs expected order
    # and allow manual reordering
    
    if st.button(f"✅ Mark as Resolved ({index})", key=f"order_resolve_{index}"):
        success = approve_stage_1_fix(
            review_item['run_id'], 
            review_item['working_content'], 
            "ORDER_CHECK_PASSED"
        )
        
        if success:
            st.success("✅ Order issue marked as resolved!")
            st.session_state.pending_reviews = get_pending_reviews()
            st.rerun()
        else:
            st.error("❌ Failed to update the document. Please try again.")

if __name__ == "__main__":
    main()