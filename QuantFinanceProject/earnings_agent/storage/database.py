import os
from dotenv import load_dotenv, find_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.dialects.postgresql import insert as pg_insert

# Import your ORM models from models.py
from earnings_agent.storage.models import RawDocument, ParsedEarning, QuarterlyFundamental, CustomKPI, Base
# CHANGED: Import DB_SCHEMA from the new neutral config file to prevent circular imports
from earnings_agent.storage.config import DB_SCHEMA

# Load environment variables from the root of the project
env_path = find_dotenv()
load_dotenv(env_path, override=True)

# Database configuration from environment variables
DB_USER = os.getenv("EARNINGS_DB_USER", "quantuser")
DB_PASS = os.getenv("EARNINGS_DB_PASSWORD", "myStrongPass")
DB_HOST = os.getenv("EARNINGS_DB_HOST", "tsdb")
DB_PORT = os.getenv("EARNINGS_DB_PORT", "5432")
DB_NAME = os.getenv("EARNINGS_DB_NAME", "quantdata")

DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# Create engine and session factory
engine = create_engine(DATABASE_URL, echo=False, future=True)
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


def upsert_raw_document(document: dict):
    """
    CHANGED: Inserts a RawDocument entry. If a document for the same ticker,
    fiscal_date, and doc_type already exists, it does nothing. This makes the
    operation idempotent and safe to re-run.
    """
    session = get_session()
    # Use the pg_insert construct to handle conflicts gracefully
    stmt = pg_insert(RawDocument).values(**document)
    
    # "ON CONFLICT DO NOTHING" tells PostgreSQL to ignore the insert
    # if a row with the same unique keys (ticker, fiscal_date, doc_type) already exists.
    stmt = stmt.on_conflict_do_nothing(
        index_elements=['ticker', 'fiscal_date', 'doc_type']
    )
    
    session.execute(stmt)
    session.commit()
    session.close()

def upsert_parsed_earning(data: dict):
    """
    Inserts a ParsedEarning entry. If a record for the same ticker, fiscal_date,
    source_type, and parser_version already exists, it does nothing.
    This makes the operation idempotent and safe to re-run.
    """
    session = get_session()
    
    # Use the pg_insert construct to handle conflicts gracefully
    stmt = pg_insert(ParsedEarning).values(**data)
    
    # "ON CONFLICT DO NOTHING" leverages the 'uq_parsed_earnings' constraint
    # defined in the models.py and schema.sql files.
    stmt = stmt.on_conflict_do_nothing(
        index_elements=['ticker', 'fiscal_date', 'source_type', 'parser_version']
    )
    
    session.execute(stmt)
    session.commit()
    session.close()
    
def upsert_quarterly_fundamental(data: dict):
    """
    Upsert a QuarterlyFundamental record based on ticker, fiscal_date, and version.
    """
    session = get_session()
    stmt = pg_insert(QuarterlyFundamental).values(**data)
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
    """
    session = get_session()
    result = session.query(CustomKPI).filter_by(
        fundamental_id=fundamental_id
    ).first()
    session.close()
    return result
