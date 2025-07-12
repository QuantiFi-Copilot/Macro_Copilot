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
    Text,
    BigInteger
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declarative_base, relationship

# Assumes a central config file for the schema name, as in your original code.
# from .config import DB_SCHEMA
DB_SCHEMA = "earnings_data" # Using a placeholder for standalone clarity

# The Base class which all our models will inherit from
Base = declarative_base()


# ================================================================================================
# STAGE 1: INGESTION - Expectations and Raw Results
# ================================================================================================

class IngestionJob(Base):
    """
    SQLAlchemy ORM model for the `ingestion_jobs` table.
    Represents the "To-Do List" or manifest of expected data ingestion tasks.
    """
    __tablename__ = 'ingestion_jobs'
    
    job_id = Column(BigInteger, primary_key=True)
    ticker = Column(String(20), nullable=False)
    fiscal_year = Column(Integer, nullable=False)
    quarter = Column(Integer, nullable=False)
    source_type = Column(String(50), nullable=False)
    
    # --- MODIFIED: Added consolidation_status column ---
    consolidation_status = Column(String(50), nullable=False)
    
    ingestion_script_version = Column(String(50), nullable=False)
    status = Column(String(50), nullable=False, default='PENDING')
    failure_reason = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_attempted_at = Column(DateTime(timezone=True), nullable=True, onupdate=func.now())

    # Relationship to the link table (one-to-one)
    job_asset_link = relationship("JobAssetLink", back_populates="job", uselist=False, cascade="all, delete-orphan")

    __table_args__ = (
        # --- MODIFIED: Added consolidation_status to the unique constraint ---
        UniqueConstraint('ticker', 'fiscal_year', 'quarter', 'source_type', 'consolidation_status', 'ingestion_script_version', name='uq_ingestion_job'),
        {'schema': DB_SCHEMA}
    )

# In models.py

class RawDataAsset(Base):
    """
    SQLAlchemy ORM model for the `raw_data_assets` table.
    Represents a unique piece of raw data, identified by its content hash.
    """
    __tablename__ = 'raw_data_assets'
    
    asset_id = Column(BigInteger, primary_key=True)
    raw_data_hash = Column(String(64), nullable=False, unique=True)
    source_type = Column(String(50), nullable=True)
    storage_location = Column(Text, nullable=True)
    
    # --- ADDED: New columns for data integrity checks ---
    source_last_modified = Column(DateTime(timezone=True), nullable=True)
    
    data_content = Column(JSONB, nullable=True)
    first_seen_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    job_links = relationship("JobAssetLink", back_populates="asset")
    parsed_documents = relationship("ParsedDocument", back_populates="asset", cascade="all, delete-orphan")

    __table_args__ = ({'schema': DB_SCHEMA})


class JobAssetLink(Base):
    """
    SQLAlchemy ORM model for the `job_asset_link` table.
    Links an IngestionJob (the expectation) to a RawDataAsset (the result).
    """
    __tablename__ = 'job_asset_link'
    
    job_id = Column(BigInteger, ForeignKey(f'{DB_SCHEMA}.ingestion_jobs.job_id'), primary_key=True)
    asset_id = Column(BigInteger, ForeignKey(f'{DB_SCHEMA}.raw_data_assets.asset_id'), nullable=False)

    # Relationships
    job = relationship("IngestionJob", back_populates="job_asset_link")
    asset = relationship("RawDataAsset", back_populates="job_links")

    __table_args__ = ({'schema': DB_SCHEMA})


# ================================================================================================
# STAGE 2 & 3: PARSING AND VALIDATION
# ================================================================================================

class ParsedDocument(Base):
    """
    SQLAlchemy ORM model for the `parsed_documents` table.
    Represents the structured data extracted from a RawDataAsset.
    """
    __tablename__ = 'parsed_documents'
    
    doc_id = Column(BigInteger, primary_key=True)
    asset_id = Column(BigInteger, ForeignKey(f'{DB_SCHEMA}.raw_data_assets.asset_id'), nullable=False)
    parser_version = Column(String(50), nullable=False)
    parse_status = Column(String(50), nullable=False)
    error_details = Column(Text,nullable=True)
    parsed_at = Column(DateTime(timezone=True), server_default=func.now())
    content = Column(JSONB, nullable=True)

    # Relationships
    asset = relationship("RawDataAsset", back_populates="parsed_documents")
    validation_results = relationship("ValidationResult", back_populates="parsed_document", cascade="all, delete-orphan")
    
    __table_args__ = (
        UniqueConstraint('asset_id', 'parser_version', name='uq_parsed_document'),
        {'schema': DB_SCHEMA}
    )


class ValidationResult(Base):
    """
    SQLAlchemy ORM model for the `validation_results` table.
    Stores the outcome of running the Validation Engine on a ParsedDocument.
    """
    __tablename__ = 'validation_results'
    
    validation_id = Column(BigInteger, primary_key=True)
    doc_id = Column(BigInteger, ForeignKey(f'{DB_SCHEMA}.parsed_documents.doc_id'), nullable=False)
    validation_script_version = Column(String(50), nullable=False)
    status = Column(String(50), nullable=False)
    summary = Column(JSONB, nullable=True)
    validated_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationship
    parsed_document = relationship("ParsedDocument", back_populates="validation_results")

    __table_args__ = (
        UniqueConstraint('doc_id', 'validation_script_version', name='uq_validation_result'),
        {'schema': DB_SCHEMA}
    )


# ================================================================================================
# STAGE 4: FINAL "GOLDEN RECORD" TABLES
# ================================================================================================

class QuarterlyFundamental(Base):
    """
    SQLAlchemy ORM model for the `quarterly_fundamentals` table.
    This is the final, clean, versioned "golden record" of financial data.
    """
    __tablename__ = 'quarterly_fundamentals'
    
    id = Column(BigInteger, primary_key=True)
    ticker = Column(String(20), nullable=False)
    fiscal_date = Column(Date, nullable=False)
    period = Column(String(10), nullable=False)
    filing_date = Column(Date, nullable=True)
    source = Column(String(50), nullable=False)
    version = Column(Integer, default=1, nullable=False)
    primary_asset_id = Column(BigInteger, ForeignKey(f'{DB_SCHEMA}.raw_data_assets.asset_id'), nullable=True)
    
    # Financial metrics...
    revenue = Column(BIGINT)
    net_income = Column(BIGINT)
    ebitda = Column(BIGINT)
    # (All other financial columns as defined in the schema)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint('ticker', 'fiscal_date', 'version', name='uq_quarterly_fundamentals'),
        {'schema': DB_SCHEMA}
    )


class CustomKPI(Base):
    """
    SQLAlchemy ORM model for the `custom_kpis` table.
    """
    __tablename__ = 'custom_kpis'
    
    id = Column(BigInteger, primary_key=True)
    fundamental_id = Column(BigInteger, ForeignKey(f'{DB_SCHEMA}.quarterly_fundamentals.id'), nullable=False, unique=True)
    kpi_data = Column(JSONB, nullable=False)

    fundamental = relationship("QuarterlyFundamental")

    __table_args__ = ({'schema': DB_SCHEMA})

# ================================================================================================
# MASTER DATA MODELS
# ================================================================================================

class Classification(Base):
    """
    SQLAlchemy ORM model for the `classifications` table.
    This table is the master list of all official industry classifications.
    """
    __tablename__ = 'classifications'
    
    id = Column(Integer, primary_key=True)
    basic_industry_name = Column(Text, nullable=False, unique=True)
    basic_industry_code = Column(String(20))
    industry_name = Column(Text)
    industry_code = Column(String(20))
    sector_name = Column(Text)
    sector_code = Column(String(20))
    macro_economic_sector_name = Column(Text)
    mes_code = Column(String(20))
    source_system = Column(String(50), default='NSE_2023')
    
    # This defines the one-to-many relationship: one classification can have many companies.
    companies = relationship("CompanyMaster", back_populates="classification")

    __table_args__ = ({'schema': DB_SCHEMA})

class CompanyMaster(Base):
    """
    SQLAlchemy ORM model for the `company_master` table.
    This is the central directory for all companies in your universe.
    """
    __tablename__ = 'company_master'
    
    id = Column(Integer, primary_key=True)
    ticker = Column(String(20), nullable=False, unique=True)
    company_name = Column(Text, nullable=False)
    isin_code = Column(String(20), unique=True)
    listing_status = Column(String(20), nullable=False, default='LISTED')
    
    # This column holds the foreign key linking to the classifications table.
    classification_id = Column(Integer, ForeignKey(f'{DB_SCHEMA}.classifications.id'))
    
    # This defines the many-to-one relationship, allowing easy access
    # to a company's full classification details via `company.classification`.
    classification = relationship("Classification", back_populates="companies")
    
    __table_args__ = ({'schema': DB_SCHEMA})