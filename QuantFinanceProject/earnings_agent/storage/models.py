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
    Numeric  # CORRECTED: Import Numeric for decimal types like EPS
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declarative_base, relationship

# Import from the neutral config file
from .config import DB_SCHEMA

# The Base class which all our models will inherit from
Base = declarative_base()

class RawDocument(Base):
    """
    SQLAlchemy ORM model for the `raw_documents` table.
    Represents a single raw source file that has been downloaded.
    (No changes to this model)
    """
    __tablename__ = 'raw_documents'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(20), nullable=False)
    fiscal_date = Column(Date, nullable=False)
    doc_type = Column(String(50), nullable=False)
    source_url = Column(String)
    local_path = Column(String)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    __table_args__ = (
        UniqueConstraint('ticker', 'fiscal_date', 'doc_type', name='uq_raw_documents'),
        {'schema': DB_SCHEMA}
    )
class ParsedEarning(Base):
    __tablename__ = 'parsed_earnings'
    id = Column(Integer, primary_key=True, autoincrement=True)
    
    # MODIFIED: raw_document_id can be null for sources that aren't files
    raw_document_id = Column(Integer, ForeignKey(f'{DB_SCHEMA}.raw_documents.id'), nullable=True) 
    
    # NEW: The source of this parsed data
    source_type = Column(String(50), nullable=False)
    
    parser_version = Column(String(20), nullable=False)
    parsed_at = Column(DateTime(timezone=True), server_default=func.now())
    content = Column(JSONB, nullable=False)

    # Added to make the unique constraint more robust
    ticker = Column(String(20), nullable=False)
    fiscal_date = Column(Date, nullable=False)

    raw_document = relationship("RawDocument")
    
    # MODIFIED: A more robust unique constraint
    __table_args__ = (
        UniqueConstraint('ticker', 'fiscal_date', 'source_type', 'parser_version', name='uq_parsed_earnings'),
        {'schema': DB_SCHEMA}
    )
class QuarterlyFundamental(Base):
    """
    SQLAlchemy ORM model for the `quarterly_fundamentals` table.
    Represents the "Core" standardized data for a single quarterly report.
    CORRECTED: This model is now expanded to match Schema v1.0.
    """
    __tablename__ = 'quarterly_fundamentals'
    
    # ===== Metadata =====
    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(20), nullable=False)
    fiscal_date = Column(Date, nullable=False)
    period = Column(String(10), nullable=False)
    filing_date = Column(Date)
    source = Column(String(50), nullable=False)
    version = Column(Integer, default=1, nullable=False)
    raw_document_id = Column(Integer, ForeignKey(f'{DB_SCHEMA}.raw_documents.id'))

    # ===== Income Statement =====
    revenue = Column(BIGINT)
    cost_of_goods_sold = Column(BIGINT)
    gross_profit = Column(BIGINT)
    operating_expenses = Column(BIGINT)
    ebitda = Column(BIGINT)
    depreciation_and_amortization = Column(BIGINT)
    ebit = Column(BIGINT)
    interest_expense = Column(BIGINT)
    profit_before_tax = Column(BIGINT)
    tax_expense = Column(BIGINT)
    net_income = Column(BIGINT)
    earnings_per_share_basic = Column(Numeric(18, 4))
    earnings_per_share_diluted = Column(Numeric(18, 4))

    # ===== Balance Sheet =====
    cash_and_equivalents = Column(BIGINT)
    accounts_receivable = Column(BIGINT)
    inventory = Column(BIGINT)
    total_current_assets = Column(BIGINT)
    property_plant_equipment_net = Column(BIGINT)
    total_non_current_assets = Column(BIGINT)
    total_assets = Column(BIGINT)
    accounts_payable = Column(BIGINT)
    total_current_liabilities = Column(BIGINT)
    total_long_term_debt = Column(BIGINT)
    total_non_current_liabilities = Column(BIGINT)
    total_liabilities = Column(BIGINT)
    shareholders_equity = Column(BIGINT)
    total_liabilities_and_equity = Column(BIGINT)

    # ===== Cash Flow Statement =====
    cash_flow_from_operating = Column(BIGINT)
    cash_flow_from_investing = Column(BIGINT)
    cash_flow_from_financing = Column(BIGINT)
    net_change_in_cash = Column(BIGINT)

    # ===== Timestamps =====
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
    (No changes to this model)
    """
    __tablename__ = 'custom_kpis'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    fundamental_id = Column(Integer, ForeignKey(f'{DB_SCHEMA}.quarterly_fundamentals.id'), nullable=False, unique=True)
    kpi_data = Column(JSONB, nullable=False)

    fundamental = relationship("QuarterlyFundamental")

    __table_args__ = ({'schema': DB_SCHEMA})
