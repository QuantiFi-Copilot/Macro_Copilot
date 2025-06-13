import os
from dotenv import load_dotenv, find_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.dialects.postgresql import insert as pg_insert

from earnings_agent.storage.models import RawDocument, QuarterlyFundamental, CustomKPI, Base

# Load environment variables
env_path = find_dotenv()
load_dotenv(env_path, override=True)

# Database configuration from environment variables
DB_USER = os.getenv("EARNINGS_DB_USER", "quantuser")
DB_PASS = os.getenv("EARNINGS_DB_PASSWORD", "myStrongPass")
DB_HOST = os.getenv("EARNINGS_DB_HOST", "tsdb")
DB_PORT = os.getenv("EARNINGS_DB_PORT", "5432")
DB_NAME = os.getenv("EARNINGS_DB_NAME", "quantdata")
DB_SCHEMA = os.getenv("EARNINGS_DB_SCHEMA", "earnings_data")

DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# Create engine and session factory
gine = create_engine(DATABASE_URL, echo=False, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db():
    """
    Initialize the database by creating all tables in the configured schema.
    """
    Base.metadata.create_all(bind=engine, schema=DB_SCHEMA)


def get_session():
    """
    Return a new SQLAlchemy session.
    """
    return SessionLocal()


def upsert_raw_document(document: dict) -> int:
    """
    Insert or update a RawDocument entry and return its primary key.

    Args:
        document: Dict matching RawDocument fields.

    Returns:
        int: ID of the inserted or updated RawDocument.
    """
    session = get_session()
    rd = RawDocument(**document)
    session.add(rd)
    session.commit()
    session.refresh(rd)
    doc_id = rd.id
    session.close()
    return doc_id


def upsert_quarterly_fundamental(data: dict):
    """
    Upsert a QuarterlyFundamental record based on ticker, fiscal_date, and version.

    Args:
        data: Dict matching QuarterlyFundamental fields.
    """
    session = get_session()
    stmt = pg_insert(QuarterlyFundamental).values(**data)
    # Prepare update dict excluding primary key
    update_cols = {c.name: getattr(stmt.excluded, c.name)
                   for c in QuarterlyFundamental.__table__.columns
                   if c.name not in ['id']}
    stmt = stmt.on_conflict_do_update(
        index_elements=['ticker', 'fiscal_date', 'version'],
        set_=update_cols
    )
    session.execute(stmt)
    session.commit()
    session.close()


def upsert_custom_kpis(fundamental_id: int, kpi_data: dict):
    """
    Upsert a CustomKPI JSONB entry for a given QuarterlyFundamental.

    Args:
        fundamental_id: The ID of the QuarterlyFundamental.
        kpi_data: Dict of custom KPI data to store.
    """
    session = get_session()
    stmt = pg_insert(CustomKPI).values(
        fundamental_id=fundamental_id,
        kpi_data=kpi_data
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=['fundamental_id'],
        set_={'kpi_data': stmt.excluded.kpi_data}
    )
    session.execute(stmt)
    session.commit()
    session.close()


def get_quarterly_fundamental(ticker: str, fiscal_date):
    """
    Fetch a QuarterlyFundamental by ticker and fiscal_date.

    Args:
        ticker: Company ticker.
        fiscal_date: Report date.

    Returns:
        QuarterlyFundamental or None
    """
    session = get_session()
    result = session.query(QuarterlyFundamental).filter_by(
        ticker=ticker,
        fiscal_date=fiscal_date
    ).first()
    session.close()
    return result


def get_custom_kpis(fundamental_id: int):
    """
    Fetch CustomKPI for a given QuarterlyFundamental ID.

    Args:
        fundamental_id: ID of the QuarterlyFundamental.

    Returns:
        CustomKPI or None
    """
    session = get_session()
    result = session.query(CustomKPI).filter_by(
        fundamental_id=fundamental_id
    ).first()
    session.close()
    return result
