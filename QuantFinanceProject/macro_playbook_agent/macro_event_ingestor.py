import pandas as pd
from macro_event_interpreter import interpret_event
from database import save_event, create_tables
from datetime import datetime

# Load CSV
df = pd.read_csv("data/macro_events.csv")

# Ensure DB tables exist
create_tables()

# Loop through events
for idx, row in df.iterrows():
    print(f"\n🔍 Processing event {idx+1}: {row['description']}")
    
    # Format a full prompt
    full_event_text = (
        f"Macro Event: {row['description']}\n"
        f"Date: {row['date']}\n"
        f"Event Type: {row['event_type']}\n"
        f"Expected: {row['expected_value']}\n"
        f"Actual: {row['actual_value']}\n"
        f"Source: {row['source']}"
    )

    try:
        result = interpret_event(full_event_text)
        event_date = datetime.strptime(row['date'], "%Y-%m-%d").date()
        save_event(row['description'], result, event_date=event_date)
        print("✅ Saved to DB")

    except Exception as e:
        print(f"❌ Error with event {idx+1}: {e}")