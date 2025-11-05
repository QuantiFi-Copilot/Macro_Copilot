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
    Index,
    Numeric
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
    doc_id = Column(BigInteger, ForeignKey(f'{DB_SCHEMA}.parsed_documents.doc_id', ondelete="CASCADE"), nullable=False, unique=True)

    # === Data & History Columns ===
    working_content = Column(JSONB, nullable=True, comment="The working copy of the parsed document content, which is modified at each QE stage.")
    run_history = Column(JSONB, nullable=True, comment="A JSONB array that serves as an immutable audit log of all transformations and checks.")

    # === Stage-by-Stage Status Columns ===
    stage_1_status = Column(String(50), nullable=False, server_default='PENDING')
    stage_2_status = Column(String(50), nullable=False, server_default='PENDING')
    stage_3_status = Column(String(50), nullable=False, server_default='PENDING')
    stage_4_status = Column(String(50), nullable=False, server_default='PENDING')

    # === Per-Stage Versioning Columns ===
    stage_1_version = Column(String(20), nullable=True)
    stage_2_version = Column(String(20), nullable=True)
    stage_3_version = Column(String(20), nullable=True)
    stage_4_version = Column(String(20), nullable=True)

    # === Audit & Debugging Fields ===
    failure_reason = Column(Text, nullable=True)
    playbook_config_hash = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationship to the ParsedDocument object for lineage
    parsed_document = relationship("ParsedDocument", back_populates="quality_engine_run")

    is_loaded_to_golden_record = Column(Boolean, nullable=False, server_default='FALSE')

    __table_args__ = (
        UniqueConstraint('doc_id', name='uq_quality_engine_run_doc_id'),
        {'schema': DB_SCHEMA}
    )
class QualityEngineRuleVariant(Base):
    __tablename__ = 'quality_engine_rule_variants'
    
    id = Column(BigInteger, primary_key=True)
    parent_playbook_id = Column(Text, nullable=False)
    issuer_ticker = Column(String(20), nullable=False)
    industry = Column(Text)
    variant_definition = Column(JSONB, nullable=False)
    created_by = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint('parent_playbook_id', 'issuer_ticker', name='uq_rule_variant'),
        {'schema': DB_SCHEMA}
    )

class LabelMappingCache(Base):
    """
    SQLAlchemy ORM model for the `label_mapping_cache` table.
    Stores both PENDING_REVIEW and APPROVED mappings to serve as the single
    source of truth for label normalization.
    """
    __tablename__ = 'label_mapping_cache'

    id = Column(BigInteger, primary_key=True)
    raw_label = Column(Text, nullable=False)
    ticker = Column(Text, nullable=False)
    statement_key = Column(Text, nullable=False)
    
    # --- ADDED THIS LINE ---
    status = Column(String(20), nullable=False, server_default='PENDING_REVIEW')
    # ---------------------

    mapping_type = Column(Text, nullable=False)
    normalized_label = Column(Text, nullable=False)
    approved_by = Column(Text, nullable=True)
    approved_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint('raw_label', 'ticker', 'statement_key', name='uq_label_mapping_context'),
        {'schema': DB_SCHEMA}
    )
# ================================================================================================
# STAGE 4: FINAL "GOLDEN RECORD" TABLES
# ================================================================================================
class FundamentalRecord(Base):
    """
    SQLAlchemy ORM model for the `fundamental_records` table.
    This is the central hub for each unique financial record.
    """
    __tablename__ = 'fundamental_records'
    id = Column(BigInteger, primary_key=True)
    ticker = Column(String(20), nullable=False)
    fiscal_date = Column(Date, nullable=False)
    period = Column(String(10), nullable=False)
    # ADD THIS COLUMN
    consolidation_status = Column(String(50), nullable=False)
    filing_date = Column(Date)
    version = Column(Integer, default=1, nullable=False)
    source_playbook = Column(String(50), nullable=False)
    source_run_id = Column(
        BigInteger,
        ForeignKey(f'{DB_SCHEMA}.quality_engine_runs.run_id')
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    # Relationship back to the Quality Engine run for lineage
    quality_engine_run = relationship("QualityEngineRun")
    # One-to-one child row for the Banking playbook table
    banking = relationship(
        "FundamentalsBanking",
        back_populates="record",
        uselist=False,
        cascade="all, delete-orphan",
    )
    # UPDATE THE UNIQUE CONSTRAINT
    __table_args__ = (
        UniqueConstraint('ticker', 'fiscal_date', 'version', 'consolidation_status', name='uq_fundamental_record'),
        {'schema': DB_SCHEMA}
    )

class CustomKpis(Base):
    """
    SQLAlchemy model for storing all company-specific KPIs in a flexible JSONB format.
    """
    __tablename__ = 'custom_kpis'

    id = Column(BigInteger, primary_key=True)
    record_id = Column(
        BigInteger,
        ForeignKey(f"{DB_SCHEMA}.fundamental_records.id", ondelete="CASCADE"),
        nullable=False,
        unique=True
    )
    kpi_data = Column(JSONB, nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Add a back-populating relationship to the master record
    record = relationship("FundamentalRecord")

    __table_args__ = ({'schema': DB_SCHEMA})
# ================================
# STAGE 4: GOLDEN RECORD – BANKING
# ================================

class FundamentalsBanking(Base):
    """
    SQLAlchemy model for earnings_data.fundamentals_banking.
    One-to-one child of FundamentalRecord via record_id.
    """
    __tablename__ = "fundamentals_banking"
    __table_args__ = ({'schema': DB_SCHEMA})

    # Primary key + FK to master record (cascade in DB)
    record_id = Column(
        BigInteger,
        ForeignKey(f"{DB_SCHEMA}.fundamental_records.id", ondelete="CASCADE"),
        primary_key=True,
    )

    # -------------------------
    # Profit & Loss (Banks)
    # -------------------------
    interest_or_discount_on_advances_or_bills = Column(BigInteger)
    revenue_on_investments = Column(BigInteger)
    interest_on_balances_with_reserve_bank_of_india_and_other_inter_bank_funds = Column(BigInteger)
    other_interest = Column(BigInteger)
    interest_earned = Column(BigInteger)
    other_income = Column(BigInteger)
    income = Column(BigInteger)
    interest_expended = Column(BigInteger)
    employees_cost = Column(BigInteger)
    other_operating_expenses = Column(BigInteger)
    operating_expenses = Column(BigInteger)
    total_expenditure_excluding_provisions_and_contingencies = Column(BigInteger)
    operating_profit_before_provision_and_contingencies = Column(BigInteger)
    provisions_other_than_tax_and_contingencies = Column(BigInteger)
    exceptional_items = Column(BigInteger)
    net_profit_loss_from_ordinary_activities_before_tax = Column(BigInteger)
    tax_expense = Column(BigInteger)
    profit_loss_from_ordinary_activities_after_tax = Column(BigInteger)
    extraordinary_items = Column(BigInteger)
    net_profit_loss_for_the_period = Column(BigInteger)
    share_of_profit_loss_of_associates = Column(BigInteger)
    net_profit_loss_after_taxes_before_minority_interest = Column(BigInteger)
    profit_loss_of_minority_interest = Column(BigInteger)
    profit_loss_after_taxes_minority_interest_and_share_of_profit_loss_of_associates = Column(BigInteger)

    paid_up_value_of_equity_share_capital = Column(BigInteger)
    face_value_of_equity_share_capital = Column(Numeric(18, 4))

    reserve_excluding_revaluation_reserves = Column(BigInteger)

    percentage_of_share_held_by_government_of_india = Column(Numeric(18, 4))
    cet1_ratio = Column(Numeric(18, 4))
    additional_tier1_ratio = Column(Numeric(18, 4))

    basic_earnings_per_share_before_extraordinary_items = Column(Numeric(18, 4))
    diluted_earnings_per_share_before_extraordinary_items = Column(Numeric(18, 4))
    basic_earnings_per_share_after_extraordinary_items = Column(Numeric(18, 4))
    diluted_earnings_per_share_after_extraordinary_items = Column(Numeric(18, 4))

    gross_non_performing_assets = Column(BigInteger)
    percentage_of_gross_npa = Column(Numeric(18, 4))
    net_non_performing_assets = Column(BigInteger)
    percentage_of_net_npa = Column(Numeric(18, 4))
    return_on_assets = Column(Numeric(18, 4))

    # -------------------------
    # Balance Sheet
    # -------------------------
    capital = Column(BigInteger)
    reserves_and_surplus = Column(BigInteger)
    deposits = Column(BigInteger)
    borrowings = Column(BigInteger)
    other_liabilities_and_provisions = Column(BigInteger)
    total_capital_and_liabilities = Column(BigInteger)
    policyholder_funds = Column(BigInteger)

    cash_and_balances_with_reserve_bank_of_india = Column(BigInteger)
    balances_with_banks_and_money_at_call_and_short_notice = Column(BigInteger)
    investments = Column(BigInteger)
    advances = Column(BigInteger)
    fixed_assets = Column(BigInteger)
    other_assets = Column(BigInteger)
    total_assets = Column(BigInteger)

    # -------------------------
    # Cash Flow (Indirect)
    # -------------------------
    profit_before_tax = Column(BigInteger)

    adjustments_for_depreciation_and_amortisation_expense = Column(BigInteger)
    profit_loss_on_revaluation_of_investments = Column(BigInteger)
    amortisation_of_premium_on_investments = Column(BigInteger)
    profit_loss_on_sale_of_fixed_assets = Column(BigInteger)
    profit_loss_on_sale_of_subsidiaries = Column(BigInteger)
    profit_loss_on_sale_of_investments = Column(BigInteger)
    provision_for_non_performing_assets = Column(BigInteger)
    provision_for_floating_provisions = Column(BigInteger)
    provision_for_standard_assets_and_contingencies = Column(BigInteger)
    dividend_income_from_subsidiaries = Column(BigInteger)
    adjustments_for_sharebased_payments = Column(BigInteger)
    other_adjustments_for_noncash_items = Column(BigInteger)

    adjustments_for_decrease_increase_in_advances = Column(BigInteger)
    adjustments_for_increase_decrease_in_deposits = Column(BigInteger)
    adjustments_for_decrease_increase_in_other_current_assets = Column(BigInteger)
    adjustments_for_increase_decrease_in_other_current_liabilities = Column(BigInteger)
    adjustments_for_decrease_increase_in_investments = Column(BigInteger)

    interest_received_classified_as_operating_activities = Column(BigInteger)
    interest_paid_classified_as_operating_activities = Column(BigInteger)
    income_taxes_paid_refund_classified_as_operating_activities = Column(BigInteger)
    net_cash_from_used_in_operating_activities = Column(BigInteger)

    purchase_of_tangible_assets_classified_as_investing_activities = Column(BigInteger)
    proceeds_from_sale_of_tangible_assets_classified_as_investing_activities = Column(BigInteger)
    purchase_of_investments_classified_as_investing_activities = Column(BigInteger)
    proceeds_from_sale_of_investments_classified_as_investing_activities = Column(BigInteger)
    dividends_received_classified_as_investing_activities = Column(BigInteger)
    other_inflows_outflows_of_cash_classified_as_investing_activities = Column(BigInteger)
    net_cash_from_used_in_investing_activities = Column(BigInteger)

    proceeds_from_issuing_shares = Column(BigInteger)
    proceeds_from_issuing_other_equity_instruments = Column(BigInteger)
    proceeds_from_issue_of_tier_1_and_tier_2_capital_instruments = Column(BigInteger)
    redemption_of_tier_1_and_tier_2_capital_instruments = Column(BigInteger)
    repayments_of_borrowings_classified_as_financing_activities = Column(BigInteger)
    proceeds_from_borrowings_classified_as_financing_activities = Column(BigInteger)
    dividends_paid_classified_as_financing_activities = Column(BigInteger)
    other_inflows_outflows_of_cash_classified_as_financing_activities = Column(BigInteger)
    net_cash_from_used_in_financing_activities = Column(BigInteger)

    effect_of_exchange_rate_changes_on_cash_and_cash_equivalents = Column(BigInteger)
    increase_decrease_in_cash_and_cash_equivalents = Column(BigInteger)
    cash_and_cash_equivalents_at_beginning_of_period = Column(BigInteger)
    cash_and_cash_equivalents_at_end_of_period = Column(BigInteger)

    # Metadata
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # ORM relationship back to the master record
    record = relationship(
        "FundamentalRecord",
        back_populates="banking",
        uselist=False,
        passive_deletes=True,
    )



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

