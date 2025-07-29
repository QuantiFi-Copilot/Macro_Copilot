import json
import logging
import re
import sys
from datetime import date
from pathlib import Path
import os
import arelle

# --- Main Arelle components ---
from arelle.RuntimeOptions import RuntimeOptions
from arelle.api.Session import Session
from arelle.logging.handlers.StructuredMessageLogHandler import StructuredMessageLogHandler

# --- Project Imports ---
project_root = Path(__file__).resolve().parents[3]
sys.path.append(str(project_root))

from earnings_agent.parsing.xbrl.taxonomy_config import TAXONOMY_REGISTRY
from earnings_agent.storage.database import get_session, create_parsed_document
from earnings_agent.storage.models import RawDataAsset, JobAssetLink, IngestionJob, CompanyMaster, ParsedDocument
from sqlalchemy.orm import Session as SQLAlchemySession

# --- Configuration ---
PARSER_VERSION = "1.1.0"
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(module)s - %(message)s')

# --- Unit resolver -------------------------------------------------
def resolve_unit_measure(unit) -> str | None:
    """
    Return a readable unit measure, e.g.:
      - 'iso4217:INR'
      - 'iso4217:INR/xbrli:shares'
      - 'xbrli:pure'
    Works across Arelle variants where measures are sets/tuples/model objects/strings.
    """
    def name_of(obj):
        # plain string like 'iso4217:INR'
        if isinstance(obj, str):
            return obj
        # QName-like with prefixedName
        n = getattr(obj, "prefixedName", None)
        if n:
            return n
        # Model object with .qname.prefixedName
        qn = getattr(obj, "qname", None)
        if qn and getattr(qn, "prefixedName", None):
            return qn.prefixedName
        # Fallback to str
        return str(obj)

    m = getattr(unit, "measures", None)
    if m is not None:
        try:
            nums, dens = m
            num = name_of(next(iter(nums))) if nums else None
            den = name_of(next(iter(dens))) if dens else None
            if num and den:
                return f"{num}/{den}"
            return num or None
        except Exception:
            # last resort, stringify what Arelle exposes
            return str(m)

    # Older fallback shape
    div = getattr(unit, "divideUnit", None)
    if div:
        try:
            nums = div[0].measures[0]
            dens = div[1].measures[0]
            num = name_of(next(iter(nums))) if nums else None
            den = name_of(next(iter(dens))) if dens else None
            return f"{num}/{den}" if (num and den) else (num or None)
        except Exception:
            return None

    return None


def get_taxonomy_package_path(file_path: str, ticker: str, db_session: SQLAlchemySession) -> Path:
    """
    Finds the correct taxonomy package path using a regex pre-scan.
    """
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read(8192) # Read first 8KB
        match = re.search(r'<link:schemaRef[^>]*\s+xlink:href\s*=\s*["\']([^"\']+)["\']', content)
        if not match:
            raise ValueError("Schema reference <link:schemaRef> not found in file.")
    except Exception as e:
         raise ValueError(f"Could not read or find schemaRef in {file_path}: {e}")

    entry_filename = Path(match.group(1)).name
    path_options = TAXONOMY_REGISTRY.get(entry_filename)
    if not path_options:
        raise ValueError(f"Unknown taxonomy entry file '{entry_filename}' in TAXONOMY_REGISTRY.")

    if len(path_options) == 1 and "_default_" in path_options:
        return project_root / path_options["_default_"]
    else:
        company = db_session.query(CompanyMaster).filter(CompanyMaster.ticker == ticker).first()
        industry = company.classification.basic_industry_name if company and company.classification else "_default_"
        package_path = project_root / path_options.get(industry, path_options["_default_"])
        if not package_path.exists():
            raise FileNotFoundError(f"Resolved taxonomy package path does not exist: {package_path}")
        return package_path

def parse_xbrl_asset(asset_id: int, session: SQLAlchemySession):
    """
    Parses a single XBRL asset using the arelle.api.Session and saves the result.
    """
    try:
        link = session.query(JobAssetLink).filter_by(asset_id=asset_id).first()
        if not link: raise RuntimeError(f"No JobAssetLink row for asset_id: {asset_id}")
        job = session.get(IngestionJob, link.job_id)
        asset = session.get(RawDataAsset, asset_id)
        if not job or not asset: raise RuntimeError("Missing Job or Asset row.")

        file_path, ticker = asset.storage_location, job.ticker
        logging.info(f"   Context: Ticker={ticker}, FY={job.fiscal_year}, Q{job.quarter}")

        taxonomy_pkg_path = get_taxonomy_package_path(file_path, ticker, session)
        logging.info(f"   Using taxonomy package: {taxonomy_pkg_path}")

        # ensure taxonomy XSD files are available to Arelle by symlinking into the instance directory
        instance_dir = Path(file_path).parent
        for taxon_file in taxonomy_pkg_path.rglob("*.xsd"):
            link_path = instance_dir / taxon_file.name
            if not link_path.exists():
                link_path.symlink_to(taxon_file)

        # also symlink all taxonomy linkbase XML files
        for taxon_file in taxonomy_pkg_path.rglob("*.xml"):
            link_path = instance_dir / taxon_file.name
            if not link_path.exists():
                link_path.symlink_to(taxon_file)

        # ensure Arelle core schema and linkbase plugins are available by symlinking to instance dir
        default_plugins = Path(arelle.__file__).parent / "plugins"
        for plugin_file in default_plugins.rglob("*"):
            link_path = instance_dir / plugin_file.name
            if not link_path.exists():
                link_path.symlink_to(plugin_file)

        # === REFACTORED TO USE arelle.api.Session ===
        options = RuntimeOptions(
            entrypointFile=file_path,
            packages=[str(taxonomy_pkg_path)],
            keepOpen=True,
            logFormat="[%(messageCode)s] %(message)s", # Arelle's internal log format
            validate=False
        )
        
        log_handler = StructuredMessageLogHandler()
        
        with Session() as arelle_session:
            arelle_session.run(options, logHandler=log_handler)
            models = arelle_session.get_models()
            model = models[0] if models else None

        if not model or not getattr(model, "facts", None):
            # Get detailed logs from the handler if parsing fails
            error_logs = []
            try:
                error_logs = [msg.message for msg in log_handler.get_messages()]
            except Exception:
                if hasattr(log_handler, "messages"):
                    error_logs = log_handler.messages
            raise RuntimeError(f"Arelle failed to parse facts. Log: {error_logs}")

        # Your proven context discovery logic
        fy, qtr = job.fiscal_year, job.quarter
        if qtr == 1: end_date = date(fy, 6, 30)
        elif qtr == 2: end_date = date(fy, 9, 30)
        elif qtr == 3: end_date = date(fy, 12, 31)
        else: end_date = date(fy + 1, 3, 31)

        def _period_end(ctx):
            if getattr(ctx, "instantDatetime", None): return ctx.instantDatetime.date()
            if getattr(ctx, "endDatetime", None): return ctx.endDatetime.date()
            return None

        candidate_contexts = [ctx for ctx in model.contexts.values() if _period_end(ctx) and abs((_period_end(ctx) - end_date)).days <= 1]
        primary_duration_ctxs = [c for c in candidate_contexts if getattr(c, "scenario", None) is None and getattr(c, "instantDatetime", None) is None and getattr(c, "startDatetime", None)]
        primary_instant_ctxs = [c for c in candidate_contexts if getattr(c, "scenario", None) is None and getattr(c, "instantDatetime", None)]
        
        target_ids = set()
        if primary_duration_ctxs:
            shortest_duration_ctx = min(primary_duration_ctxs, key=lambda c: (c.endDatetime - c.startDatetime).days)
            target_ids.add(shortest_duration_ctx.id)
        target_ids.update({c.id for c in primary_instant_ctxs})

        if not target_ids: raise RuntimeError(f"Could not find any primary contexts for {end_date}.")
        logging.info(f"   Identified primary quarter contexts: {target_ids}")

        # -------------------------
        # Fact extraction WITH unit metadata
        # -------------------------
        # Build a unit lookup: unit id -> resolved measure text
        unit_map = {u.id: resolve_unit_measure(u) for u in model.units.values()}

        parsed_data: dict[str, object] = {}
        for fact in model.facts:
            if fact.contextID not in target_ids:
                continue
            concept = getattr(fact, "concept", None)
            if concept is None or getattr(concept, "qname", None) is None:
                # Fallback: keep raw value keyed by whatever name Arelle exposes
                name = getattr(fact, "qname", None)
                name = getattr(name, "localName", None) or getattr(fact, "concept", None) or "UnknownConcept"
                parsed_data[str(name)] = fact.value
                continue

            # Names
            qn = concept.qname
            name = qn.localName
            qname_prefixed = getattr(qn, "prefixedName", None) or name
            qname_ns = getattr(qn, "namespaceURI", None) or ""
            qname_clark = f"{{{qname_ns}}}{name}" if qname_ns else name

            # Datatype hints
            ctype = getattr(concept, "type", None)
            type_qn = getattr(ctype, "qname", None)
            data_type = getattr(type_qn, "prefixedName", None) or (str(type_qn) if type_qn else None)
            base_xbrli_type = getattr(concept, "baseXbrliType", None)

            # Numeric vs non-numeric
            is_numeric = bool(getattr(concept, "isNumeric", False))

            if is_numeric:
                unit_id = getattr(fact, "unitID", None)
                decimals_attr = getattr(fact, "decimals", None)
                decimals_str = str(decimals_attr) if decimals_attr is not None else None
                unit_measure = unit_map.get(unit_id) if unit_id else None

                obj = {
                    "value": fact.value,
                    "contextRef": fact.contextID,
                    # Preserve source exactly
                    "original_unitRef": unit_id,
                    "original_decimals": decimals_str,
                    # Mirror into current parsed fields (we are not normalizing here)
                    "unitRef": unit_id,
                    "unit_measure": unit_measure,
                    "decimals": decimals_str,
                    # Concept identifiers
                    "qname": qname_prefixed,
                    "qname_clark": qname_clark,
                    "data_type": data_type,
                    "base_xbrli_type": base_xbrli_type,
                    # Signal when filer omitted unit on a numeric fact
                    "missing_unit": unit_id is None
                }
                parsed_data[name] = obj
            else:
                # Non-numeric facts: keep simple scalar plus identifiers for traceability
                parsed_data[name] = {
                    "value": fact.value,
                    "contextRef": fact.contextID,
                    "qname": qname_prefixed,
                    "qname_clark": qname_clark,
                    "data_type": data_type,
                    "base_xbrli_type": base_xbrli_type
                }

        # Promote document-level metadata
        rounding_level = parsed_data.pop("LevelOfRoundingUsedInFinancialStatements", None)
        presentation_currency = parsed_data.pop("DescriptionOfPresentationCurrency", None)

        # Preserve legacy top-level 'unit' field for backward-compatibility and add primary_context_ids
        content = {
            "source_period_end": end_date.isoformat(),
            "presentation_currency": presentation_currency,
            "rounding_level": rounding_level,
            "unit": rounding_level,  # legacy compatibility
            "primary_context_ids": sorted(list(target_ids)),
            **parsed_data,
        }

        doc_data = {
            "asset_id": asset_id,
            "parser_version": PARSER_VERSION,
            "parse_status": 'PARSED_OK',
            "content": content,
        }
        create_parsed_document(doc_data)
        logging.info(f"✅ Successfully parsed and stored result for Asset ID: {asset_id}")

    except Exception as e:
        logging.error(f"❌ An error occurred parsing Asset ID {asset_id}: {e}", exc_info=False)
        doc_data = {"asset_id": asset_id, "parser_version": PARSER_VERSION, "parse_status": 'PARSING_ERROR', "error_details": str(e)}
        create_parsed_document(doc_data)
        logging.error(f"   Created PARSING_ERROR record for Asset ID: {asset_id}")

def run_parser_batch():
    """
    Finds and processes all unprocessed XBRL assets in the database.
    """
    logging.info(f"--- Starting XBRL Parser Batch Run v{PARSER_VERSION} ---")
    session = get_session()
    try:
        unprocessed_assets = session.query(RawDataAsset.asset_id)\
            .outerjoin(ParsedDocument, RawDataAsset.asset_id == ParsedDocument.asset_id)\
            .filter(RawDataAsset.source_type == 'XBRL_FILE')\
            .filter(ParsedDocument.asset_id == None)\
            .all()

        asset_ids_to_process = [r[0] for r in unprocessed_assets]

        if not asset_ids_to_process:
            logging.info("No new XBRL assets to process. Exiting.")
            return

        logging.info(f"Found {len(asset_ids_to_process)} unprocessed XBRL assets. Starting batch.")
        
        for asset_id in asset_ids_to_process:
            logging.info(f"--- Processing Asset ID: {asset_id} ---")
            try:
                parse_xbrl_asset(asset_id, session)
            except Exception as e:
                logging.critical(f"A critical, unhandled error in main loop for asset_id {asset_id}: {e}", exc_info=True)

        logging.info("--- Batch run completed successfully. ---")
    finally:
        session.close()

if __name__ == '__main__':
    run_parser_batch()