from macro_playbook_agent.storage.database import Session, MacroSeries

session = Session()

# Get distinct tickers
tickers = session.query(MacroSeries.ticker).distinct().all()
tickers = [t[0] for t in tickers]

print("🔍 Latest record for each ticker:")
for ticker in tickers:
    latest = (
        session.query(MacroSeries)
        .filter_by(ticker=ticker)
        .order_by(MacroSeries.recorded_at.desc())
        .first()
    )
    print(f"{ticker:15} | {latest.recorded_at} | {latest.value} {latest.units} | {latest.source}")

session.close()