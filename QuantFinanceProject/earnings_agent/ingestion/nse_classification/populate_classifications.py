# scripts/populate_classifications.py

# CORRECTED: Importing the correct function name 'get_session'
from earnings_agent.storage.database import get_session, bulk_upsert_classifications
from earnings_agent.ingestion.nse_classification.nse_classifications import CLASSIFICATION_DATA

def main():
    """
    Main function to populate the classifications table.
    """
    print("Starting to populate the 'classifications' table...")
    
    # Get a new session from your database module
    # CORRECTED: Calling the correct function 'get_session()'
    session = get_session()
    
    # Use a try...finally block to ensure the session is always closed
    try:
        # Call the function to perform the bulk insert/update
        bulk_upsert_classifications(session, CLASSIFICATION_DATA)
        
        # Commit the transaction to make the changes permanent
        session.commit()
        print("Successfully populated classifications table.")
        
    except Exception as e:
        print(f"An error occurred: {e}")
        # If an error occurs, roll back the transaction
        session.rollback()
        
    finally:
        # No matter what happens, always close the session
        print("Closing database session.")
        session.close()

if __name__ == "__main__":
    main()