# /app/earnings_agent/quality_engine/stage2/review_UI/stage_2_review_ui.py

import streamlit as st
import pandas as pd
from typing import Dict, List, Any

from stage_2_review_utils import (
    get_pending_stage_2_reviews, get_live_calculation_results,
    get_calculation_status_summary, format_calculation_display,
    format_reconciliation_candidate, approve_stage_2_calculation_review,
    reject_stage_2_calculation_review, format_currency_value,
    save_correction_and_re_run
)

st.set_page_config(
    page_title="Stage 2 Quality Engine Review",
    page_icon="🧮",
    layout="wide"
)

def initialize_state():
    """Initialize or refresh the data in the session state."""
    if 'pending_stage_2_reviews' not in st.session_state:
        st.session_state.pending_stage_2_reviews = get_pending_stage_2_reviews()

def get_all_metrics_for_statement(review_item: Dict, statement_key: str) -> List[Dict]:
    """Get a combined list of normalized and unmapped figures for the UI."""
    content = review_item.get('working_content', {})
    statement_data = content.get('llm_call_2_extraction', {}).get(statement_key, {})
    
    all_metrics = []
    all_metrics.extend(statement_data.get('normalized_figures', []))
    for um in statement_data.get('unmapped_from_pdf', []):
        # Give unmapped metrics a temporary playbook_id for selection
        um['playbook_id'] = um['raw_label']
        all_metrics.append(um)
    return sorted([m for m in all_metrics], key=lambda x: x.get('raw_label', ''))

def main():
    st.title("🧮 Stage 2 Quality Engine Review - Calculation Validation")
    st.markdown("---")
    
    initialize_state()
    
    with st.sidebar:
        st.header("Filters")
        companies = ['All'] + sorted(list(set([item['company'] for item in st.session_state.pending_stage_2_reviews])))
        selected_company = st.selectbox("Company", companies)
        if st.button("🔄 Refresh Data"):
            st.session_state.pending_stage_2_reviews = get_pending_stage_2_reviews()
            st.rerun()
    
    filtered_reviews = [r for r in st.session_state.pending_stage_2_reviews if selected_company == 'All' or r['company'] == selected_company]
    
    st.metric("Total Documents for Review", len(filtered_reviews))
    st.markdown("---")
    
    if not filtered_reviews:
        st.info("🎉 No pending calculation reviews found!")
        return
    
    st.subheader("Pending Calculation Reviews")
    for i, review_item in enumerate(filtered_reviews):
        with st.expander(
            f"📄 {review_item['company']} - Q{review_item['quarter']} FY{review_item['fiscal_year']} (Calculation Issues)",
            expanded=i == 0
        ):
            display_calculation_review(review_item, i)

def display_calculation_review(review_item: Dict, index: int):
    st.info(f"**Company:** {review_item['company']} | **Period:** Q{review_item['quarter']} FY{review_item['fiscal_year']} | **Issue:** Calculation Mismatch")
    
    with st.spinner("Loading detailed calculation results..."):
        live_results = get_live_calculation_results(review_item['working_content'], review_item['company'])
    
    if 'error' in live_results:
        st.error(f"Error loading calculations: {live_results['error']}")
        return
    
    for statement_key, statement_data in live_results.get('statement_results', {}).items():
        display_statement_calculations(statement_key, statement_data, index, review_item)
    
    display_approval_controls(review_item, index, live_results)

def display_statement_calculations(statement_key: str, statement_data: Dict, index: int, review_item: Dict):
    summary = get_calculation_status_summary(statement_data)
    status_icon = "✅" if summary['passable'] else "❌"
    st.markdown(f"### {status_icon} Statement: `{statement_key}`")
    
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Total", summary['total'])
    col2.metric("Direct Pass", summary['passed'])
    col3.metric("Auto-Reconciled", summary['tier_1_auto_reconciled'])
    col4.metric("Suggestions", summary['tier_2_suggestions'])
    col5.metric("No Reconciliation", summary['tier_3_no_reconciliation'])
    
    if not summary['passable']:
        st.error(f"❌ Statement needs review: {summary['needs_human_review']} calculations require human attention")

    calculations = statement_data.get('calculations', [])
    review_calcs = [c for c in calculations if c.get('reconciliation_tier') in [2, 3]]
    if review_calcs:
        with st.container(border=True):
            for calc in review_calcs:
                display_failed_calculation_ui(calc, index, review_item, statement_key)

def display_failed_calculation_ui(calculation: Dict, index: int, review_item: Dict, statement_key: str):
    """The main UI component for displaying and correcting a failed calculation."""
    formatted = format_calculation_display(calculation)
    status_text = "Suggestions Available" if formatted['reconciliation_tier'] == 2 else "Manual Investigation Required"
    st.warning(f"**Rule:** `{formatted['rule_id']}` - {status_text}")

    col1, col2 = st.columns([1, 1.2])
    with col1:
        st.markdown("**Calculation Details:**")
        st.json({
            "Expected Value": formatted['parent_value'],
            "Calculated Value": formatted['calculated_value'],
            "Variance": formatted['variance']
        }, expanded=True)
    
    with col2:
        st.markdown("**Correction Workflow:**")
        suggestions = formatted.get('suggestion_candidates', [])
        
        # Display Top Suggestion if available
        if suggestions:
            top_suggestion = suggestions[0]
            formatted_candidate = format_reconciliation_candidate(top_suggestion)
            with st.container(border=True):
                st.info("💡 **Top Suggestion**")
                if formatted_candidate['is_combination']:
                    st.write(f"Apply this {formatted_candidate['combination_size']}-metric combination:")
                    for label, value in zip(formatted_candidate['combination_labels'], formatted_candidate['combination_values']):
                        st.text(f"  - {label} = {value}")
                else:
                    st.write(f"Apply **'{formatted_candidate['raw_label']}'**")
                
                unique_key = f"accept_{index}_{statement_key}_{formatted['rule_id']}"
                if st.button("Accept Suggestion & Save Rule", key=unique_key, type="primary"):
                    # --- FIXED: Build complete rule including missing children ---
                    new_formula = []
                    
                    # 1. Add all children from the original calculation that were found
                    for orig_child in calculation.get('calculation_breakdown', []):
                        new_formula.append({
                            orig_child['playbook_id']: orig_child['multiplier']
                        })
                    
                    # 2. CRITICAL FIX: Add missing children from original rule
                    # These were part of the original rule but not found in the data
                    for missing_child in calculation.get('missing_children', []):
                        # Use multiplier 1 as reasonable default for missing children
                        # This preserves the complete original rule structure
                        new_formula.append({
                            missing_child: 1
                        })
                    
                    # 3. Add the new suggested metrics
                    combo_sum = sum(metric.get('value', 0) for metric in top_suggestion.get('combination', []))
                    # Determine correct sign for the suggested metrics
                    global_multiplier = 1 if calculation['missing_amount'] * combo_sum > 0 else -1
                    
                    for metric in top_suggestion.get('combination', []):
                        new_formula.append({
                            metric.get('playbook_id'): global_multiplier
                        })
                    # --- END FIXED SECTION ---
                    
                    result = save_correction_and_re_run(review_item['run_id'], review_item['company'], formatted['rule_id'], new_formula)
                    if result['status'] == 'success':
                        st.success(result['message'])
                        st.session_state.pending_stage_2_reviews = get_pending_stage_2_reviews()
                        st.rerun()
                    else:
                        st.error(result['message'])

        # Manual Rule Builder as an expander
        with st.expander("🛠️ Manually Build Correction Rule"):
            all_metrics = get_all_metrics_for_statement(review_item, statement_key)
            metric_options = {f"{m['raw_label']} ({format_currency_value(m['value'])})": m['playbook_id'] for m in all_metrics if m.get('playbook_id')}
            
            state_key = f"rule_{index}_{statement_key}_{formatted['rule_id']}"

            if 'rules' not in st.session_state: st.session_state.rules = {}
            if state_key not in st.session_state.rules:
                # --- FIXED: Initialize with complete original rule ---
                rule_components = []
                
                # Add found children with their actual multipliers
                for item in calculation.get('calculation_breakdown', []):
                    rule_components.append({
                        'metric': item['playbook_id'], 
                        'multiplier': item['multiplier']
                    })
                
                # CRITICAL FIX: Add missing children with default multiplier 1
                for missing_child in calculation.get('missing_children', []):
                    rule_components.append({
                        'metric': missing_child,
                        'multiplier': 1
                    })
                
                # If no components exist, start with empty rule
                if not rule_components:
                    rule_components = [{'metric': None, 'multiplier': 1}]
                
                st.session_state.rules[state_key] = rule_components
                # --- END FIXED SECTION ---

            for i, rule_part in enumerate(st.session_state.rules[state_key]):
                col1, col2, col3 = st.columns([4, 1, 1])
                options_list = list(metric_options.keys())
                default_index = list(metric_options.values()).index(rule_part['metric']) if rule_part.get('metric') in metric_options.values() else 0
                
                selected_label = col1.selectbox("Select Metric", options=options_list, key=f"metric_{state_key}_{i}", index=default_index)
                if selected_label:
                    st.session_state.rules[state_key][i]['metric'] = metric_options[selected_label]
                
                st.session_state.rules[state_key][i]['multiplier'] = col2.selectbox("Multiplier", [1, -1], key=f"mult_{state_key}_{i}", index=0 if rule_part['multiplier'] == 1 else 1)
                
                if col3.button("➖", key=f"del_{state_key}_{i}"):
                    st.session_state.rules[state_key].pop(i)
                    st.rerun()

            if st.button("➕ Add Metric", key=f"add_{state_key}"):
                st.session_state.rules[state_key].append({'metric': list(metric_options.values())[0], 'multiplier': 1})
                st.rerun()

            if st.button("Save Manual Rule", key=f"save_manual_{state_key}", type="primary"):
                new_formula = [
                    {part['metric']: part['multiplier']}
                    for part in st.session_state.rules[state_key] if part.get('metric')
                ]
                
                if new_formula:
                    result = save_correction_and_re_run(review_item['run_id'], review_item['company'], formatted['rule_id'], new_formula)
                    if result['status'] == 'success':
                        st.success(result['message'])
                        del st.session_state.rules[state_key]
                        st.session_state.pending_stage_2_reviews = get_pending_stage_2_reviews()
                        st.rerun()
                    else:
                        st.error(result['message'])
                else:
                    st.warning("Please build a rule with at least one metric.")
    
    st.markdown("---")

def display_approval_controls(review_item: Dict, index: int, live_results: Dict):
    st.markdown("### 🎯 Review Decision")
    summary = live_results.get('reconciliation_summary', {})
    needs_review = summary.get('requires_human_review_statements', 0)
    
    if needs_review == 0:
        st.success("✅ All statements have passed or been corrected. This document can be approved.")
        default_action_index = 0
    else:
        st.error(f"❌ {needs_review} statement(s) still require corrections.")
        default_action_index = 1
        
    action = st.radio("Final Action:", ["approve", "investigate", "reject"], index=default_action_index, key=f"action_{index}")
    
    if action == "approve":
        approval_notes = st.text_area("Approval Notes (optional):", key=f"approval_notes_{index}")
        if st.button("✅ Approve Document", type="primary", key=f"approve_btn_{index}"):
            success = approve_stage_2_calculation_review(review_item['run_id'], approval_notes)
            if success:
                st.success("Document approved successfully!")
                st.session_state.pending_stage_2_reviews = get_pending_stage_2_reviews()
                st.rerun()
            else:
                st.error("Failed to approve document.")
    elif action == "reject":
        rejection_reason = st.text_area("Rejection Reason:", key=f"rejection_reason_{index}")
        if st.button("❌ Reject Document", key=f"reject_btn_{index}"):
            if rejection_reason.strip():
                success = reject_stage_2_calculation_review(review_item['run_id'], rejection_reason)
                if success:
                    st.success("Document rejected successfully!")
                    st.session_state.pending_stage_2_reviews = get_pending_stage_2_reviews()
                    st.rerun()
                else:
                    st.error("Failed to reject document.")
            else:
                st.warning("Please provide a rejection reason.")
    else:
        st.info("Document will remain in the review queue for further corrections.")

if __name__ == "__main__":
    main()