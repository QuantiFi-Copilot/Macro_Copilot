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
    BigInteger,
    Boolean,
    CheckConstraint,
    Index
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
    
    consolidation_status = Column(String(50), nullable=False)
    
    ingestion_script_version = Column(String(50), nullable=False)
    status = Column(String(50), nullable=False, default='PENDING')
    failure_reason = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_attempted_at = Column(DateTime(timezone=True), nullable=True, onupdate=func.now())

    # Relationship to the link table (one-to-one)
    job_asset_link = relationship("JobAssetLink", back_populates="job", uselist=False, cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint('ticker', 'fiscal_year', 'quarter', 'source_type', 'consolidation_status', 'ingestion_script_version', name='uq_ingestion_job'),
        {'schema': DB_SCHEMA}
    )


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
# STAGE 2: PARSING
# ================================================================================================

class ParsedDocument(Base):
    """
    SQLAlchemy ORM model for the `parsed_documents` table.
    Represents the structured data extracted from a RawDataAsset.
    This holds the raw, un-normalized key-value pairs.
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
    quality_engine_run = relationship("QualityEngineRun", back_populates="parsed_document", uselist=False, cascade="all, delete-orphan")
    __table_args__ = (
        UniqueConstraint('asset_id', 'parser_version', name='uq_parsed_document'),
        {'schema': DB_SCHEMA}
    )

# # ================================================================================================
# # STAGE 3: NORMALIZATION & RECONCILIATION
# # ================================================================================================

# class LabelMapping(Base):
#     """
#     SQLAlchemy ORM model for the `label_mapping_cache` table.
#     This is the persistent cache for the financial label normalization engine,
#     acting as the system's long-term, human-verified memory.
#     Mappings are now specific to an industry.
#     """
#     __tablename__ = 'label_mapping_cache'

#     # --- MODIFIED: Changed to a composite primary key ---
#     raw_label = Column(Text, primary_key=True)
#     industry = Column(Text, primary_key=True)
#     # ---------------------------------------------------
    
#     normalized_label = Column(Text, nullable=True)
#     status = Column(String(20), nullable=False)

#     processed = Column(Boolean, nullable=False, server_default='f', default=False)
    
#     source_context = Column(JSONB, nullable=True)
#     created_at = Column(DateTime(timezone=True), server_default=func.now())
#     last_reviewed_at = Column(DateTime(timezone=True), nullable=True)
#     reviewed_by = Column(Text, nullable=True)

#     __table_args__ = ({'schema': DB_SCHEMA})

# class StagedNormalizedData(Base):
#     """
#     SQLAlchemy ORM model for the `staged_normalized_data` table.
#     This is an intermediate staging table that holds the output of the
#     Normalization Engine for a single source. The Quality Engine will
#     gather all records for a given filing from this table to perform
#     reconciliation.
#     """
#     __tablename__ = 'staged_normalized_data'

#     id = Column(BigInteger, primary_key=True)
    
#     # Foreign key to the source document it was created from.
#     doc_id = Column(BigInteger, ForeignKey(f'{DB_SCHEMA}.parsed_documents.doc_id'), nullable=False, unique=True)
    
#     # Denormalized fields for easy querying by the Quality Engine.
#     ticker = Column(String(20), nullable=False)
#     fiscal_date = Column(Date, nullable=False)
    
#     # The fully normalized data from this one source.
#     normalized_data = Column(JSONB, nullable=False)

#     # Hash of the normalized_data content to detect changes.
#     data_hash = Column(String(64), nullable=True)
    
#     # NEW: Three-phase normalization status tracking
#     statement_normalized = Column(Boolean, nullable=False, server_default='false')
#     unit_review_status = Column(String(20), nullable=False, server_default="'PENDING'")
#     label_review_status = Column(String(20), nullable=False, server_default="'PENDING'")
    
#     created_at = Column(DateTime(timezone=True), server_default=func.now())

#     # Relationship back to the parsed document for full lineage
#     parsed_document = relationship("ParsedDocument")

#     __table_args__ = (
#         CheckConstraint(
#             "unit_review_status IN ('PENDING','AUTO_APPROVED','PENDING_REVIEW','APPROVED')",
#             name='ck_unit_review_status'
#         ),
#         CheckConstraint(
#             "label_review_status IN ('PENDING','PENDING_REVIEW','APPROVED')",
#             name='ck_label_review_status'
#         ),
#         {'schema': DB_SCHEMA}
#     )
# class UnitReviewQueue(Base):
#     """
#     SQLAlchemy ORM model for the `unit_review_queue` table.
#     Stores filing-level unit normalization decisions pending human review.
#     """
#     __tablename__ = 'unit_review_queue'

#     id = Column(BigInteger, primary_key=True)
#     doc_id = Column(BigInteger, ForeignKey(f'{DB_SCHEMA}.parsed_documents.doc_id'), nullable=False, unique=True)
#     asset_id = Column(BigInteger, ForeignKey(f'{DB_SCHEMA}.raw_data_assets.asset_id'), nullable=False)
#     ticker = Column(String(20), nullable=False)
#     fiscal_date = Column(Date, nullable=False)
    
#     # The LLM's analysis and raw filing data
#     llm_analysis = Column(JSONB, nullable=False)
#     filing_data = Column(JSONB, nullable=False)
    
#     # Human review fields
#     status = Column(String(20), nullable=False, server_default="'PENDING_REVIEW'")
#     reviewed_by = Column(Text, nullable=True)
#     reviewed_at = Column(DateTime(timezone=True), nullable=True)
#     human_corrections = Column(JSONB, nullable=True)
    
#     created_at = Column(DateTime(timezone=True), server_default=func.now())

#     # Relationships
#     parsed_document = relationship("ParsedDocument")
#     asset = relationship("RawDataAsset")

#     __table_args__ = (
#         CheckConstraint(
#             "status IN ('PENDING_REVIEW','APPROVED','REJECTED')",
#             name='ck_unit_review_queue_status'
#         ),
#         {'schema': DB_SCHEMA}
#     )


# ================================================================================================
# STAGE 3: QUALITY ENGINE
# ================================================================================================
# In models.py, under the STAGE 3: QUALITY ENGINE section
class QualityEngineRun(Base):
    """
    SQLAlchemy ORM model for the `quality_engine_runs` table.
    This class represents the state machine for a document's journey
    through the entire quality and normalization pipeline.
    """
    __tablename__ = 'quality_engine_runs'

    # Core Fields
    run_id = Column(BigInteger, primary_key=True)
    doc_id = Column(BigInteger, ForeignKey(f'{DB_SCHEMA}.parsed_documents.doc_id'), nullable=False, unique=True)

    # === Stage-by-Stage Status Columns ===
    stage_1_status = Column(String(50), nullable=False, server_default='PENDING')
    stage_2_status = Column(String(50), nullable=False, server_default='PENDING')
    stage_3_status = Column(String(50), nullable=False, server_default='PENDING')
    stage_4_status = Column(String(50), nullable=False, server_default='PENDING')
    stage_5_status = Column(String(50), nullable=False, server_default='PENDING')

    # === NEW: Per-Stage Versioning Columns ===
    # These are nullable as they are only set upon successful stage completion.
    stage_1_version = Column(String(20), nullable=True)
    stage_2_version = Column(String(20), nullable=True)
    stage_3_version = Column(String(20), nullable=True)
    stage_4_version = Column(String(20), nullable=True)
    stage_5_version = Column(String(20), nullable=True)

    # === Audit & Debugging Fields ===
    failure_reason = Column(Text, nullable=True)
    details = Column(JSONB, nullable=True)
    playbook_config_hash = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationship to the ParsedDocument object
    parsed_document = relationship("ParsedDocument", back_populates="quality_engine_run")

    __table_args__ = (
        UniqueConstraint('doc_id', name='uq_quality_engine_run_doc_id'),
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
    This is the master list of all official industry classifications.
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
    bse_code = Column(String(10), unique=True)
    # This column holds the foreign key linking to the classifications table.
    classification_id = Column(Integer, ForeignKey(f'{DB_SCHEMA}.classifications.id'))
    
    # This defines the many-to-one relationship, allowing easy access
    # to a company's full classification details via `company.classification`.
    classification = relationship("Classification", back_populates="companies")
    
    __table_args__ = ({'schema': DB_SCHEMA})

# # Index to speed up normalization status queries
# Index(
#     'idx_staged_normalization_status',
#     StagedNormalizedData.statement_normalized,
#     StagedNormalizedData.unit_review_status,
#     StagedNormalizedData.label_review_status,
# )