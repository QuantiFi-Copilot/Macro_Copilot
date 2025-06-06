import streamlit as st
from database import Session, MacroEvent, create_tables
import datetime

# Initialize DB
create_tables()

st.set_page_config(page_title="📊 MacroAnalystAgent", layout="wide")
st.title("📊 Macro Event Dashboard")

# Search & filter
search_query = st.text_input("Search events...", "")
selected_date = st.date_input("Filter by event date", value=None)

# Load events
session = Session()
query = session.query(MacroEvent).order_by(MacroEvent.event_date.desc())

if search_query:
    query = query.filter(MacroEvent.raw_event.ilike(f"%{search_query}%"))

if selected_date:
    query = query.filter(MacroEvent.event_date == selected_date)

events = query.all()
session.close()

# Display
if not events:
    st.warning("No events found with the current filters.")
else:
    for e in events:
        st.markdown(f"### 🗓️ {e.event_date} — {e.raw_event}")
        with st.expander("🧠 LLM Interpretation"):
            st.json(e.interpretation)
        st.markdown("---")