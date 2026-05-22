"""utils/render_effective_universe.py — render & push the EFFECTIVE playbook
for resolver-enabled playbooks (work order A4-4, ADR 0007).

For a playbook that declares an enabled ``otr_resolution:`` block the universe
is NOT static: as the on-the-run bond rolls after each auction, the previous
OTR bonds accumulate as off-the-run bonds that the cash-bond RV layer still
needs data for. The git playbook holds only the curated SEED universe; the
authoritative record of every bond ever on-the-run is ``macro_data.otr_history``.

This script expands the seed into the EFFECTIVE universe —

    effective universe = seed bonds  ∪  every bond ever in otr_history
                         that has not yet matured

— and uploads that rendered artifact to ``gs://<bucket>/playbooks/``. The git
seed is NEVER modified: config flows down (git -> render -> GCS -> Bloomberg
PC), resolved facts flow up as data (resolver -> otr_history). The rendered
file is a machine artifact and is deliberately not committed.

Run this LOCALLY (it needs the database). ``push_playbooks.py`` skips
resolver-enabled playbooks precisely so this renderer owns their upload and the
static seed never clobbers the rendered effective universe in the bucket.

Provenance (ADR 0007): every rendered file carries a header recording the seed
hash, the rendered-body hash, the otr_history snapshot, and the render time so
the universe used by any extraction run is auditable / replayable.
"""

from __future__ import annotations

import hashlib
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml
from sqlalchemy import text

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from database.database import get_db_engine  # noqa: E402

# --- CONFIGURATION ---
BUCKET_NAME = os.getenv("GCP_BUCKET_NAME", "macro-storage-bucket")
GCP_KEY_FILENAME = os.getenv("GCP_KEY_FILENAME", "library-extractor-key.json")

# The universe-row keys carried over from instrument_master for an
# otr_history-derived bond — the same shape the seed universe rows use.
_UNIVERSE_ROW_KEYS = (
    "instrument_type",
    "curve_family",
    "country",
    "currency",
    "tenor",
)


def _sha256_text(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def playbook_has_resolver(playbook: Dict[str, Any]) -> bool:
    """True iff the playbook declares an enabled ``otr_resolution`` block."""
    block = playbook.get("otr_resolution")
    return isinstance(block, dict) and bool(block.get("enabled", False))


def load_otr_history_bonds(engine) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Return (bonds, snapshot).

    ``bonds`` — one dict per DISTINCT bond ever recorded in ``otr_history``,
    joined to ``instrument_master`` for its identity. ``snapshot`` — metadata
    about the otr_history state used (row count, latest ``created_at``) for the
    rendered file's provenance header.
    """
    query = text(
        """
        SELECT DISTINCT
            i.vendor_ticker,
            i.instrument_type,
            i.curve_family,
            i.country,
            i.currency,
            i.tenor,
            i.maturity_date
        FROM macro_data.otr_history o
        JOIN macro_data.instrument_master i
          ON i.instrument_id = o.otr_instrument_id
        ORDER BY i.country, i.tenor, i.vendor_ticker
        """
    )
    snapshot_query = text(
        "SELECT COUNT(*) AS n, MAX(created_at) AS latest FROM macro_data.otr_history"
    )
    with engine.connect() as conn:
        rows = conn.execute(query).mappings().all()
        snap = conn.execute(snapshot_query).mappings().first()

    bonds = [dict(r) for r in rows]
    snapshot = {
        "otr_history_rows": int(snap["n"]) if snap and snap["n"] is not None else 0,
        "otr_history_latest_created_at": (
            snap["latest"].isoformat() if snap and snap["latest"] is not None else None
        ),
    }
    return bonds, snapshot


def build_effective_universe(
    seed_universe: List[Dict[str, Any]],
    otr_bonds: List[Dict[str, Any]],
    as_of: date,
) -> List[Dict[str, Any]]:
    """Union the seed universe with otr_history-derived bonds.

    Seed rows are kept verbatim (human-curated, authoritative metadata). An
    otr_history bond is appended only when its ``/isin/`` ticker is not already
    a seed row AND it has not matured on/before ``as_of`` — a matured
    off-the-run bond is dropped from the live extraction universe (a NULL
    maturity is kept; absence of data is not a maturity signal).
    """
    universe: List[Dict[str, Any]] = []
    seen: set = set()

    for row in seed_universe:
        if not isinstance(row, dict) or "ticker" in row and row["ticker"] in seen:
            continue
        universe.append(row)
        if "ticker" in row:
            seen.add(row["ticker"])

    for bond in otr_bonds:
        ticker = bond.get("vendor_ticker")
        if not ticker or ticker in seen:
            continue
        maturity = bond.get("maturity_date")
        if maturity is not None:
            mat_date = maturity if isinstance(maturity, date) else None
            if mat_date is not None and mat_date <= as_of:
                continue  # matured (on/before today) off-the-run bond — drop it
        row = {"ticker": ticker}
        for key in _UNIVERSE_ROW_KEYS:
            value = bond.get(key)
            if value is not None:
                row[key] = value
        universe.append(row)
        seen.add(ticker)

    return universe


def render_effective_playbook(
    seed_path: Path, engine, as_of: date | None = None
) -> Tuple[str, Dict[str, Any]]:
    """Render the effective playbook for one seed file.

    Returns ``(rendered_text, provenance)``. ``rendered_text`` is the complete
    file content (provenance header + YAML body) ready to upload.
    """
    as_of = as_of or date.today()
    seed_text = seed_path.read_text(encoding="utf-8")
    playbook = yaml.safe_load(seed_text) or {}

    seed_universe = [r for r in (playbook.get("universe") or []) if isinstance(r, dict)]
    otr_bonds, snapshot = load_otr_history_bonds(engine)
    effective = build_effective_universe(seed_universe, otr_bonds, as_of)
    playbook["universe"] = effective

    body = yaml.safe_dump(
        playbook,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
        width=4096,
    )

    rendered_at = datetime.now()
    provenance = {
        "seed_playbook": seed_path.name,
        "seed_playbook_sha256": _sha256_text(seed_text),
        "rendered_body_sha256": _sha256_text(body),
        "seed_universe_count": len(seed_universe),
        "effective_universe_count": len(effective),
        "otr_history_rows": snapshot["otr_history_rows"],
        "otr_history_latest_created_at": snapshot["otr_history_latest_created_at"],
        "rendered_at": rendered_at.strftime("%Y-%m-%d %H:%M:%S"),
        "as_of_date": as_of.isoformat(),
    }

    header_lines = [
        "# " + "=" * 76,
        "# RENDERED EFFECTIVE PLAYBOOK — MACHINE ARTIFACT — DO NOT EDIT, DO NOT COMMIT",
        "# Generated by utils/render_effective_universe.py (work order A4-4, ADR 0007).",
        "#",
        "# The git source of truth is the SEED playbook; this file is its effective",
        "# expansion (seed universe + every non-matured bond ever in otr_history) and",
        "# is regenerated on every push. The resolver never edits the seed.",
        "#",
        f"#   seed_playbook                 : {provenance['seed_playbook']}",
        f"#   seed_playbook_sha256          : {provenance['seed_playbook_sha256']}",
        f"#   rendered_body_sha256          : {provenance['rendered_body_sha256']}",
        f"#   seed_universe_count           : {provenance['seed_universe_count']}",
        f"#   effective_universe_count      : {provenance['effective_universe_count']}",
        f"#   otr_history_rows              : {provenance['otr_history_rows']}",
        f"#   otr_history_latest_created_at : {provenance['otr_history_latest_created_at']}",
        f"#   as_of_date                    : {provenance['as_of_date']}",
        f"#   rendered_at                   : {provenance['rendered_at']}",
        "# " + "=" * 76,
        "",
    ]
    rendered_text = "\n".join(header_lines) + body
    return rendered_text, provenance


def _gcp_bucket():
    """Authenticate and return the GCS bucket, or None on failure."""
    from google.cloud import storage

    gcp_key_path = _PROJECT_ROOT / "secure_keys" / GCP_KEY_FILENAME
    if not gcp_key_path.exists():
        print(f"[ERROR] Could not find GCP key at {gcp_key_path}")
        return None
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(gcp_key_path)
    try:
        client = storage.Client()
        return client.bucket(BUCKET_NAME)
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] Failed to authenticate with GCP: {exc}")
        return None


def render_and_push(upload: bool = True) -> int:
    """Render every resolver-enabled playbook and (optionally) upload it.

    Returns the number of playbooks rendered. A rendered copy is also written
    next to nothing on disk — the artifact is uploaded straight to GCS; a local
    copy is dropped under ``utils/temp_rendered/`` for inspection.
    """
    playbooks_dir = _PROJECT_ROOT / "rates_agent" / "playbooks"
    out_dir = Path(__file__).resolve().parent / "temp_rendered"
    out_dir.mkdir(exist_ok=True)

    engine = get_db_engine()
    bucket = _gcp_bucket() if upload else None
    if upload and bucket is None:
        print("[ABORT] No GCS bucket — cannot upload rendered playbooks.")
        return 0

    rendered_count = 0
    for seed_path in sorted(playbooks_dir.glob("*.yml")) + sorted(playbooks_dir.glob("*.yaml")):
        try:
            playbook = yaml.safe_load(seed_path.read_text(encoding="utf-8")) or {}
        except Exception as exc:  # noqa: BLE001
            print(f"  [WARNING] Could not parse {seed_path.name}: {exc}")
            continue
        if not playbook_has_resolver(playbook):
            continue

        rendered_text, provenance = render_effective_playbook(seed_path, engine)
        local_copy = out_dir / seed_path.name
        local_copy.write_text(rendered_text, encoding="utf-8")
        print(
            f"  [RENDERED] {seed_path.name}: "
            f"seed={provenance['seed_universe_count']} -> "
            f"effective={provenance['effective_universe_count']} "
            f"(otr_history_rows={provenance['otr_history_rows']})"
        )

        if upload and bucket is not None:
            blob_name = f"playbooks/{seed_path.name}"
            bucket.blob(blob_name).upload_from_filename(str(local_copy))
            print(f"  [SUCCESS] Uploaded effective playbook -> gs://{BUCKET_NAME}/{blob_name}")

        rendered_count += 1

    if rendered_count == 0:
        print("[INFO] No resolver-enabled playbooks found — nothing to render.")
    return rendered_count


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Render & push the effective playbook for resolver-enabled playbooks."
    )
    parser.add_argument(
        "--no-upload",
        action="store_true",
        help="Render to utils/temp_rendered/ only; do not upload to GCS.",
    )
    args = parser.parse_args()
    render_and_push(upload=not args.no_upload)
