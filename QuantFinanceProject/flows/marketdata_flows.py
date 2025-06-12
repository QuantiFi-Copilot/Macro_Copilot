from dotenv import load_dotenv
import os
from prefect import flow, task

# Load environment variables from the .env file at project root
base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
env_path = os.path.join(base_dir, '.env')
load_dotenv(env_path)

# Fix Docker host for local development
host = os.getenv("MARKET_DB_HOST")
if host == "host.docker.internal":
    os.environ["MARKET_DB_HOST"] = "127.0.0.1"

@task
def run_daily():
    from market_data_agent.ingestion.update_daily import update_daily
    update_daily()

@task
def run_5m():
    from market_data_agent.ingestion.update_intraday_5m import update_intraday_5m
    update_intraday_5m()

@flow(name="update_daily")
def update_daily_flow():
    """
    Flow to run daily EOD OHLCV update
    """
    run_daily()

@flow(name="update_intraday_5m")
def update_5m_flow():
    """
    Flow to run rolling 5-minute intraday update
    """
    run_5m()

@flow(name="bootstrap_historical")
def bootstrap_flow():
    """
    One-time backfill of historical data
    """
    from market_data_agent.ingestion.bootstrap_historical import bootstrap_historical
    bootstrap_historical()
