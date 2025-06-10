from prefect import flow, task

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
    run_daily()

@flow(name="update_intraday_5m")
def update_5m_flow():
    run_5m()

@flow(name="bootstrap_historical")
def bootstrap_flow():
    from market_data_agent.ingestion.bootstrap_historical import bootstrap_historical
    bootstrap_historical()
