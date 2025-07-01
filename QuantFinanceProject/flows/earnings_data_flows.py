# flows/earnings_data_flows.py
from prefect import flow, task

# Import the single, consolidated function
from earnings_agent.ingestion.xbrl.ingestion_task import ingest_all_xbrl

@task(name="Run Full XBRL Ingestion", retries=1, timeout_seconds=3600)
def ingestion_task(start_date: str, end_date: str):
    """
    Prefect task wrapper for the monolithic ingestion script.
    """
    ingest_all_xbrl(start_date_str=start_date, to_date_str=end_date)


@flow(name="Simplified XBRL Ingestion Flow", log_prints=True)
def simplified_ingestion_flow(start_date: str = "01-04-2022", end_date: str = "30-06-2025"):
    """
    This simplified flow runs the entire ingestion process as a single, robust task.
    """
    ingestion_task.submit(start_date, end_date)


if __name__ == "__main__":
    # Allows you to run the flow directly for testing
    simplified_ingestion_flow(start_date="01-04-2025", end_date="30-06-2025")