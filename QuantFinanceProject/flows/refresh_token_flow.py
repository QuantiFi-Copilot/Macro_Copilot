# flows/refresh_token_flow.py
from prefect import flow
from market_data_agent.auth.refresh_token import refresh_kite_access_token

@flow(name="refresh-token-daily")
def refresh_token_flow():
    """
    A dedicated flow that runs once daily to refresh the Kite access token.
    This is the single source of truth for authentication.
    """
    print("🚀 Starting daily Kite access token refresh...")
    try:
        refresh_kite_access_token()
        print("✔ Daily Kite access token refreshed successfully.")
    except Exception as e:
        print(f"‼ Daily Kite access token refresh failed: {e}")
        raise

if __name__ == "__main__":
    refresh_token_flow()