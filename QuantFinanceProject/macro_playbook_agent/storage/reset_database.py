# reset_database.py
from macro_playbook_agent.storage.database import Session, MacroSeries, create_tables

def reset_macro_series():
    create_tables()  # ✅ This line ensures the table exists before deletion
    session = Session()
    session.query(MacroSeries).delete()
    session.commit()
    session.close()
    print("✅ Cleared all records from macro_series")

if __name__ == "__main__":
    reset_macro_series()