import os
from dotenv import load_dotenv, find_dotenv
from sqlalchemy import create_engine, update
from sqlalchemy.orm import sessionmaker
from sqlalchemy.dialects.postgresql import insert as pg_insert

# --- MODIFIED: Importing the new IngestionLog model ---
from earnings_agent.storage.models import RawSource, ParsedEarning, QuarterlyFundamental, CustomKPI, IngestionLog, Base
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


def upsert_raw_source(source_data: dict):
    """
    Inserts a RawSource entry. If a source for the same ticker,
    fiscal_date, and source_type already exists, it does nothing.
    """
    session = get_session()
    stmt = pg_insert(RawSource).values(**source_data)
    stmt = stmt.on_conflict_do_nothing(
        index_elements=['ticker', 'fiscal_date', 'source_type']
    )
    session.execute(stmt)
    session.commit()
    session.close()


def upsert_parsed_earning(data: dict):
    """
    Inserts a ParsedEarning entry. If a record for the same raw_source_id
    and parser_version already exists, it does nothing.
    """
    session = get_session()
    stmt = pg_insert(ParsedEarning).values(**data)
    stmt = stmt.on_conflict_do_nothing(
        index_elements=['raw_source_id', 'parser_version']
    )
    session.execute(stmt)
    session.commit()
    session.close()


# --- NEW: Function to log the status of ingestion attempts ---
def upsert_ingestion_log(log_data: dict):
    """
    Upserts an IngestionLog entry. If a log for the same company, period,
    and source already exists, it updates the status and timestamp.
    This makes the logging process idempotent and self-correcting.
    """
    session = get_session()
    stmt = pg_insert(IngestionLog).values(**log_data)
    
    # Define which columns to update if a conflict occurs
    update_cols = {
        'status': stmt.excluded.status,
        'raw_source_id': stmt.excluded.raw_source_id,
        'checked_at': stmt.excluded.checked_at,
    }
    
    # ON CONFLICT, update the existing record with the new status
    stmt = stmt.on_conflict_do_update(
        index_elements=['ticker', 'fiscal_year', 'quarter', 'source_type'],
        set_=update_cols
    )
    session.execute(stmt)
    session.commit()
    session.close()


def upsert_quarterly_fundamental(data: dict):
    # This function remains unchanged
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
    # This function remains unchanged
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


def update_parsed_earning_with_validation(record_id: int, validated_content: dict):
    # This function remains unchanged
    session = get_session()
    stmt = (
        update(ParsedEarning)
        .where(ParsedEarning.id == record_id)
        .values(content=validated_content)
    )
    session.execute(stmt)
    session.commit()
    session.close()


def get_quarterly_fundamental(ticker: str, fiscal_date):
    # This function remains unchanged
    session = get_session()
    result = session.query(QuarterlyFundamental).filter_by(
        ticker=ticker,
        fiscal_date=fiscal_date
    ).first()
    session.close()
    return result


def get_custom_kpis(fundamental_id: int):
    # This function remains unchanged
    session = get_session()
    result = session.query(CustomKPI).filter_by(
        fundamental_id=fundamental_id
    ).first()
    session.close()
    return result