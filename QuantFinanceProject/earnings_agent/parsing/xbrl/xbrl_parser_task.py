"""
Parse a single XBRL filing and extract quarter facts.
FINAL version using the CntlrCmdLine().run(options) execution pattern.
"""
import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

# --- Main Arelle components from source file ---
from arelle.CntlrCmdLine import CntlrCmdLine
from arelle.RuntimeOptions import RuntimeOptions

# ---------------------------------------------------------------------------
# project imports
# ---------------------------------------------------------------------------
project_root = Path(__file__).resolve().parents[3]
sys.path.append(str(project_root))

from earnings_agent.parsing.xbrl.taxonomy_config import TAXONOMY_REGISTRY
from earnings_agent.storage.database import get_session
from earnings_agent.storage.models import (
    RawDataAsset,
    JobAssetLink,
    IngestionJob,
    CompanyMaster,
)

# ---------------------------------------------------------------------------
def get_taxonomy_package_path(file_path: str, ticker: str, db_session):
    """Determines the correct local taxonomy package path for a given filing."""
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read(8192)
        match = re.search(r'<link:schemaRef[^>]*\s+xlink:href\s*=\s*["\']([^"\']+)["\']', content)
        if match:
            href = match.group(1)
        else:
            raise ValueError("Schema reference not found")
    except Exception:
         raise ValueError(f"Could not find a <link:schemaRef> in the file: {file_path}")

    entry = Path(href).name
    path_opts = TAXONOMY_REGISTRY.get(entry)
    if not path_opts:
        raise ValueError(f"Unknown taxonomy entry file in TAXONOMY_REGISTRY: {entry}")

    if len(path_opts) == 1 and "_default_" in path_opts:
        return project_root / path_opts["_default_"]
    else:
        co = db_session.query(CompanyMaster).filter(CompanyMaster.ticker == ticker).first()
        industry = co.classification.basic_industry_name if co and co.classification else "_default_"
        pkg_path = project_root / path_opts.get(industry, path_opts["_default_"])
        if not pkg_path.exists():
            raise FileNotFoundError(f"Taxonomy package not found at: {pkg_path}")
        return pkg_path

# ---------------------------------------------------------------------------
def parse_xbrl_asset(asset_id: int):
    print("=" * 80)
    print(f"🚀 Starting Parse for Asset ID: {asset_id}")
    print("=" * 80)

    arelle_controller = None
    db_session = get_session()
    try:
        link = db_session.query(JobAssetLink).filter_by(asset_id=asset_id).first()
        if not link: raise RuntimeError("No JobAssetLink row.")

        job   = db_session.get(IngestionJob, link.job_id)
        asset = db_session.get(RawDataAsset, asset_id)
        if not job or not asset: raise RuntimeError("Missing Job or Asset row.")

        file_path = asset.storage_location
        ticker    = job.ticker
        fy, qtr   = job.fiscal_year, job.quarter
        print(f"   Context: Ticker={ticker}, FY={fy}, Q{qtr}")

        taxonomy_pkg_path = get_taxonomy_package_path(file_path, ticker, db_session)
        print(f"1. Identified taxonomy package: {taxonomy_pkg_path}")

        # --- SYMLINK TAXONOMY FILES INTO INSTANCE FOLDER -------------------
        from pathlib import Path
        import os

        instance_dir = Path(file_path).parent
        # Recursively link every file in the taxonomy package so schemaRef hrefs resolve
        for taxon_file in Path(taxonomy_pkg_path).rglob("*"):
            if taxon_file.is_file():
                link_path = instance_dir / taxon_file.name
                if not link_path.exists():
                    try:
                        os.symlink(taxon_file, link_path)
                    except FileExistsError:
                        pass
        # -------------------------------------------------------------------

        arelle_controller = CntlrCmdLine(logFileName='logToBuffer')

        options = RuntimeOptions(
            entrypointFile=file_path,
            packages=[str(taxonomy_pkg_path)],
            keepOpen=True,
        )

        print("2. Running Arelle controller...")
        arelle_controller.run(options)
        
        model = arelle_controller.modelManager.modelXbrl
        if not model or not model.facts:
            log_text = arelle_controller.logHandler.getText()
            raise RuntimeError(f"Arelle failed to parse facts. Log:\n{log_text}")

        if qtr == 1: end_date = date(fy, 6, 30)
        elif qtr == 2: end_date = date(fy, 9, 30)
        elif qtr == 3: end_date = date(fy, 12, 31)
        else: end_date = date(fy + 1, 3, 31)

        # ------------------- FINALIZED LOGIC STARTS HERE -------------------

        print(f"3. Scanning contexts for {end_date} …")

        def _period_end(ctx):
            """Helper function to get the end date from a context."""
            if getattr(ctx, "instantDatetime", None): return ctx.instantDatetime.date()
            if getattr(ctx, "endDatetime", None): return ctx.endDatetime.date()
            return None

        # --- Find all contexts that end on the target quarter's end_date ---
        candidate_contexts = []
        for ctx in model.contexts.values():
            pe = _period_end(ctx)
            if pe in (end_date, end_date + timedelta(days=1)):
                candidate_contexts.append(ctx)

        if not candidate_contexts:
            raise RuntimeError(f"No context found ending {end_date} (+/-1 day).")

        # --- Separate primary contexts into DURATION and INSTANT lists ---
        primary_duration_contexts = []
        primary_instant_contexts = []
        for ctx in candidate_contexts:
            # A primary (non-dimensional) context is one WITHOUT a <scenario> element.
            if getattr(ctx, "scenario", None) is None:
                if getattr(ctx, "instantDatetime", None) is None:
                    # To be a valid duration context, it must have both start and end dates
                    if getattr(ctx, "startDatetime", None) and getattr(ctx, "endDatetime", None):
                        primary_duration_contexts.append(ctx)
                else:
                    primary_instant_contexts.append(ctx)

        # --- Identify the correct context IDs to target ---
        target_ids = set()

        # Find the single duration context with the shortest period (this will be the quarter)
        if primary_duration_contexts:
            def get_duration_days(c):
                return (c.endDatetime.date() - c.startDatetime.date()).days
            
            shortest_duration_ctx = min(primary_duration_contexts, key=get_duration_days)
            target_ids.add(shortest_duration_ctx.id)

        # Add all primary instant contexts found for that end date
        for ctx in primary_instant_contexts:
            target_ids.add(ctx.id)

        if not target_ids:
            raise RuntimeError(f"Could not find any primary contexts for {end_date}.")

        print(f"   Identified primary quarter contexts: {target_ids}")

        # --- Extract facts using only the precisely identified primary context IDs ---
        parsed = {fact.concept.qname.localName: fact.value for fact in model.facts if fact.contextID in target_ids}
        if "LevelOfRoundingUsedInFinancialStatements" in parsed:
            unit = parsed.pop("LevelOfRoundingUsedInFinancialStatements")
            parsed = {"unit": unit, **parsed}

        return parsed

    except Exception as exc:
        import traceback
        print("❌  ERROR:", exc)
        traceback.print_exc()
        return None
    finally:
        if arelle_controller:
            arelle_controller.close()
        if db_session:
            db_session.close()

if __name__ == "__main__":
    import re
    argp = argparse.ArgumentParser()
    argp.add_argument("--asset-id", type=int, required=True)
    res = parse_xbrl_asset(argp.parse_args().asset_id)
    if res:
        print("\n" + "=" * 80)
        print("✅ PARSING COMPLETE — JSON")
        print("=" * 80)
        print(json.dumps(res, indent=4))