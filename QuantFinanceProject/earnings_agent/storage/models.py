# earnings_agent/storage/models.py

from sqlalchemy import (
    Column,
    Integer,
    String,
    Date,
    BIGINT,
    ForeignKey,
    DateTime,
    UniqueConstraint,
    func,
    Numeric
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declarative_base, relationship

# Import from the neutral config file
from .config import DB_SCHEMA

# The Base class which all our models will inherit from
Base = declarative_base()


class RawSource(Base):
    """
    SQLAlchemy ORM model for the unified `raw_sources` table.
    Represents a single raw data source, which can be a file OR an API response.
    """
    __tablename__ = 'raw_sources'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(20), nullable=False)
    fiscal_date = Column(Date, nullable=False)
    source_type = Column(String(50), nullable=False)
    source_url = Column(String)
    local_path = Column(String, nullable=True)
    raw_content = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    parsed_earnings = relationship("ParsedEarning", back_populates="raw_source")
    # --- MODIFIED: Added relationship to the new IngestionLog ---
    ingestion_logs = relationship("IngestionLog", back_populates="raw_source")
    
    __table_args__ = (
        UniqueConstraint('ticker', 'fiscal_date', 'source_type', name='uq_raw_sources'),
        {'schema': DB_SCHEMA}
    )


class ParsedEarning(Base):
    """
    SQLAlchemy ORM model for the `parsed_earnings` table.
    """
    __tablename__ = 'parsed_earnings'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    raw_source_id = Column(Integer, ForeignKey(f'{DB_SCHEMA}.raw_sources.id'), nullable=False)
    parser_version = Column(String(20), nullable=False)
    parsed_at = Column(DateTime(timezone=True), server_default=func.now())
    content = Column(JSONB, nullable=False)

    raw_source = relationship("RawSource", back_populates="parsed_earnings")
    
    __table_args__ = (
        UniqueConstraint('raw_source_id', 'parser_version', name='uq_parsed_earnings'),
        {'schema': DB_SCHEMA}
    )


class QuarterlyFundamental(Base):
    """
    SQLAlchemy ORM model for the `quarterly_fundamentals` table.
    """
    __tablename__ = 'quarterly_fundamentals'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(20), nullable=False)
    fiscal_date = Column(Date, nullable=False)
    period = Column(String(10), nullable=False)
    filing_date = Column(Date)
    source = Column(String(50), nullable=False)
    version = Column(Integer, default=1, nullable=False)
    raw_source_id = Column(Integer, ForeignKey(f'{DB_SCHEMA}.raw_sources.id'))
    
    revenue = Column(BIGINT)
    net_income = Column(BIGINT)
    # ... all other financial columns ...
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    raw_source = relationship("RawSource")

    __table_args__ = (
        UniqueConstraint('ticker', 'fiscal_date', 'version', name='uq_quarterly_fundamentals'),
        {'schema': DB_SCHEMA}
    )


class CustomKPI(Base):
    """
    SQLAlchemy ORM model for the `custom_kpis` table.
    """
    __tablename__ = 'custom_kpis'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    fundamental_id = Column(Integer, ForeignKey(f'{DB_SCHEMA}.quarterly_fundamentals.id'), nullable=False, unique=True)
    kpi_data = Column(JSONB, nullable=False)

    fundamental = relationship("QuarterlyFundamental")

    __table_args__ = ({'schema': DB_SCHEMA})


# --- NEW: SQLAlchemy ORM model for the ingestion_log table ---
class IngestionLog(Base):
    """
    SQLAlchemy ORM model for the `ingestion_log` table.
    Tracks the status of data ingestion attempts for every expected filing.
    """
    __tablename__ = 'ingestion_log'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(20), nullable=False)
    fiscal_year = Column(Integer, nullable=False)
    quarter = Column(Integer, nullable=False)
    source_type = Column(String(50), nullable=False)
    status = Column(String(50), nullable=False)
    
    # This is nullable because a log entry with status 'MISSING' will not have a corresponding raw_source.
    raw_source_id = Column(Integer, ForeignKey(f'{DB_SCHEMA}.raw_sources.id'), nullable=True)
    
    checked_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationship back to the raw source record, if one was found.
    raw_source = relationship("RawSource", back_populates="ingestion_logs")
    
    __table_args__ = (
        UniqueConstraint('ticker', 'fiscal_year', 'quarter', 'source_type', name='uq_ingestion_log'),
        {'schema': DB_SCHEMA}
    )