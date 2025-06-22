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
    func
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declarative_base, relationship

# CORRECTED: Import from the new, neutral config file to break the circular import.
from .config import DB_SCHEMA

# The Base class which all our models will inherit from
Base = declarative_base()

class RawDocument(Base):
    """
    SQLAlchemy ORM model for the `raw_documents` table.
    Represents a single raw source file that has been downloaded.
    """
    __tablename__ = 'raw_documents'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(20), nullable=False)
    fiscal_date = Column(Date, nullable=False)
    doc_type = Column(String(50), nullable=False)
    source_url = Column(String)
    local_path = Column(String)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Defines the table schema and the unique constraint
    __table_args__ = (
        UniqueConstraint('ticker', 'fiscal_date', 'doc_type', name='uq_raw_documents'),
        {'schema': DB_SCHEMA}
    )

class QuarterlyFundamental(Base):
    """
    SQLAlchemy ORM model for the `quarterly_fundamentals` table.
    Represents the "Core" standardized data for a single quarterly report.
    """
    __tablename__ = 'quarterly_fundamentals'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(20), nullable=False)
    fiscal_date = Column(Date, nullable=False)
    period = Column(String(10), nullable=False)
    filing_date = Column(Date)
    standard_revenue = Column(BIGINT)
    standard_net_income = Column(BIGINT)
    total_assets = Column(BIGINT)
    # CORRECTED: Removed typo "Column.Column"
    total_liabilities = Column(BIGINT)
    operating_cash_flow = Column(BIGINT)
    source = Column(String(50), nullable=False)
    version = Column(Integer, default=1, nullable=False)
    raw_document_id = Column(Integer, ForeignKey(f'{DB_SCHEMA}.raw_documents.id'))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # Relationship to link back to the raw document
    raw_document = relationship("RawDocument")

    __table_args__ = (
        UniqueConstraint('ticker', 'fiscal_date', 'version', name='uq_quarterly_fundamentals'),
        {'schema': DB_SCHEMA}
    )

class CustomKPI(Base):
    """
    SQLAlchemy ORM model for the `custom_kpis` table.
    Represents the "Satellite" industry-specific or non-standard KPIs.
    """
    __tablename__ = 'custom_kpis'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    fundamental_id = Column(Integer, ForeignKey(f'{DB_SCHEMA}.quarterly_fundamentals.id'), nullable=False, unique=True)
    kpi_data = Column(JSONB, nullable=False)

    # Relationship to link back to the fundamental record
    fundamental = relationship("QuarterlyFundamental")

    __table_args__ = ({'schema': DB_SCHEMA})
