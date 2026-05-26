"""utils/render_wirp_universe.py — render & push the EFFECTIVE wirp playbook
(work order B2, D-wirp, ADR 0009 §3).

WIRP's universe is meeting-dated and grows as the central-bank calendar
extends, so it is NOT hand-listed: the git ``wirp.yml`` seed ships an EMPTY
``universe:`` and declares only the ``wirp:`` config (metrics, region prefixes,
optional horizon). This script reads the central-bank-meeting calendar from
``macro_data.event_calendar`` — the calendar D-cb ingested — and renders the
effective ``wirp.yml``: one universe row per meeting, the synthetic
per-meeting WIRP instrument (ADR 0009 §1). It uploads that rendered artifact to
``gs://<bucket>/playbooks/wirp.yml``.

This is the OTR rendered-universe pattern (ADR 0007 §7) reused: config flows
down (git seed -> render -> GCS), facts flow up as data (D-cb ->
event_calendar -> here). The git seed is NEVER machine-edited.
``push_playbooks.py`` skips the WIRP seed so the empty universe never clobbers
this rendered output.

Run this LOCALLY (it needs the database). With NO horizon in the seed's
``wirp:`` section the render is UNBOUNDED — every meeting in
``event_calendar`` — which is exactly what the Stage-B coverage probe consumes
(ADR 0009 §6). Once Stage D sets the verified ``forward_horizon_days`` /
``past_horizon_days`` in the seed, the render is bounded to the verified
coverage band.

Provenance (ADR 0009 §3 / P4): every rendered file carries a header recording
the seed hash, the rendered-body hash, the ``event_calendar`` meeting
snapshot, the horizon applied and the render time, so the universe behind any
extraction run is auditable / replayable.
"""

from __future__ import annotations

import hashlib
import os
import sys
from datetime import date, datetime, timedelta
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


def _sha256_text(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def playbook_has_wirp_section(playbook: Dict[str, Any]) -> bool:
    """True iff the playbook declares a ``wirp:`` section (ADR 0009 §2) — the
    marker that makes it a WIRP playbook."""
    return isinstance(playbook.get("wirp"), dict)


def _as_date(value: Any) -> date:
    """Coerce a DB ``release_date`` to a ``datetime.date``. A reflected DATE
    column yields a ``date`` already; an ISO string is parsed defensively."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def load_central_bank_meetings(engine) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Return ``(meetings, snapshot)``.

    ``meetings`` — one dict per ``event_category = 'central_bank_meeting'`` row
    in ``event_calendar`` (the calendar D-cb ingested): ``central_bank``,
    ``country``, ``currency``, ``release_date``. ``snapshot`` — the row count
    and latest ``created_at`` of that calendar slice, for the rendered file's
    provenance header.
    """
    query = text(
        """
        SELECT central_bank, country, currency, release_date
        FROM macro_data.event_calendar
        WHERE event_category = 'central_bank_meeting'
        ORDER BY central_bank, release_date
        """
    )
    snapshot_query = text(
        """
        SELECT COUNT(*) AS n, MAX(created_at) AS latest
        FROM macro_data.event_calendar
        WHERE event_category = 'central_bank_meeting'
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(query).mappings().all()
        snap = conn.execute(snapshot_query).mappings().first()

    meetings = [dict(r) for r in rows]
    snapshot = {
        "event_calendar_meeting_rows": int(snap["n"]) if snap and snap["n"] is not None else 0,
        "event_calendar_latest_created_at": (
            snap["latest"].isoformat() if snap and snap["latest"] is not None else None
        ),
    }
    return meetings, snapshot


def build_wirp_universe(
    meetings: List[Dict[str, Any]],
    wirp_config: Dict[str, Any],
    as_of: date,
) -> List[Dict[str, Any]]:
    """Build the WIRP universe — one synthetic instrument per central-bank
    meeting (ADR 0009 §1, §3).

    Each row carries the typed ``instrument_master`` columns (``maturity_date``
    = the meeting date), the ``attributes``-bound identity (``central_bank``,
    ``meeting_date``, ``wirp_region_prefix``, ``wirp_meeting_token``) and the
    four source Bloomberg tickers (``wirp_ticker_fr`` … — source-ticker
    provenance, ADR 0009 §1).

    ``wirp_config`` is the seed's ``wirp:`` section. A meeting outside the
    ``forward_horizon_days`` / ``past_horizon_days`` band (when set) is dropped;
    with neither set the render is unbounded. A meeting whose ``central_bank``
    is not in ``region_prefixes`` is skipped with a warning — no WIRP ticker
    can be built for it. Output is sorted ``(central_bank, meeting_date)`` for
    deterministic rendering (P4).
    """
    region_prefixes = wirp_config.get("region_prefixes") or {}
    metrics = [
        m
        for m in (wirp_config.get("metrics") or [])
        if isinstance(m, dict) and m.get("code") and m.get("available", True)
    ]

    fwd = wirp_config.get("forward_horizon_days")
    past = wirp_config.get("past_horizon_days")
    fwd_limit = as_of + timedelta(days=int(fwd)) if fwd is not None else None
    past_limit = as_of - timedelta(days=int(past)) if past is not None else None

    rows: List[Dict[str, Any]] = []
    for meeting in meetings:
        cb = meeting.get("central_bank")
        raw_date = meeting.get("release_date")
        if not cb or raw_date is None:
            continue
        mdate = _as_date(raw_date)

        if fwd_limit is not None and mdate > fwd_limit:
            continue
        if past_limit is not None and mdate < past_limit:
            continue

        prefix = region_prefixes.get(cb)
        if not prefix:
            print(
                f"  [WARNING] central_bank {cb!r} (meeting {mdate.isoformat()}) "
                "is not in wirp.region_prefixes — no WIRP ticker can be built; "
                "meeting skipped."
            )
            continue

        token = mdate.strftime("%b%Y").upper()  # e.g. 2026-06-17 -> JUN2026
        row: Dict[str, Any] = {
            "ticker": f"WIRP:{cb}:{mdate.isoformat()}",  # synthetic vendor_ticker
            "instrument_type": "wirp_meeting",
            "curve_family": "WIRP",
            "country": meeting.get("country"),
            "currency": meeting.get("currency"),
            "maturity_date": mdate,             # typed instrument_master column
            "central_bank": cb,                 # -> attributes
            "meeting_date": mdate.isoformat(),  # -> attributes (ISO mirror)
            "wirp_region_prefix": prefix,       # -> attributes
            "wirp_meeting_token": token,        # -> attributes
        }
        for metric in metrics:
            code = str(metric["code"])
            row[f"wirp_ticker_{code.lower()}"] = f"{prefix}{code} {token} Index"
        rows.append(row)

    rows.sort(key=lambda r: (r["central_bank"], r["meeting_date"]))
    return rows


def _horizon_description(wirp_config: Dict[str, Any]) -> str:
    fwd = wirp_config.get("forward_horizon_days")
    past = wirp_config.get("past_horizon_days")
    if fwd is None and past is None:
        return "unbounded (every event_calendar meeting)"
    return f"forward_horizon_days={fwd} past_horizon_days={past}"


def render_wirp_playbook(
    seed_path: Path, engine, as_of: date | None = None
) -> Tuple[str, Dict[str, Any]]:
    """Render the effective WIRP playbook for one seed file.

    Returns ``(rendered_text, provenance)``. ``rendered_text`` is the complete
    file content (provenance header + YAML body) ready to upload.
    """
    as_of = as_of or date.today()
    seed_text = seed_path.read_text(encoding="utf-8")
    playbook = yaml.safe_load(seed_text) or {}

    wirp_config = playbook.get("wirp")
    if not isinstance(wirp_config, dict):
        raise ValueError(
            f"{seed_path.name} has no `wirp:` section — not a WIRP playbook."
        )

    meetings, snapshot = load_central_bank_meetings(engine)
    universe = build_wirp_universe(meetings, wirp_config, as_of)
    playbook["universe"] = universe

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
        "event_calendar_meeting_rows": snapshot["event_calendar_meeting_rows"],
        "event_calendar_latest_created_at": snapshot["event_calendar_latest_created_at"],
        "universe_count": len(universe),
        "horizon": _horizon_description(wirp_config),
        "rendered_at": rendered_at.strftime("%Y-%m-%d %H:%M:%S"),
        "as_of_date": as_of.isoformat(),
    }

    header_lines = [
        "# " + "=" * 76,
        "# RENDERED EFFECTIVE WIRP PLAYBOOK — MACHINE ARTIFACT — DO NOT EDIT, DO NOT COMMIT",
        "# Generated by utils/render_wirp_universe.py (work order B2, D-wirp, ADR 0009).",
        "#",
        "# The git source of truth is the SEED wirp.yml; this file is its effective",
        "# expansion (one universe row per central-bank meeting in event_calendar) and",
        "# is regenerated on every push. The renderer never edits the seed.",
        "#",
        f"#   seed_playbook                 : {provenance['seed_playbook']}",
        f"#   seed_playbook_sha256          : {provenance['seed_playbook_sha256']}",
        f"#   rendered_body_sha256          : {provenance['rendered_body_sha256']}",
        f"#   event_calendar_meeting_rows   : {provenance['event_calendar_meeting_rows']}",
        f"#   event_calendar_latest_created : {provenance['event_calendar_latest_created_at']}",
        f"#   universe_count                : {provenance['universe_count']}",
        f"#   horizon                       : {provenance['horizon']}",
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
    """Render every WIRP-section playbook and (optionally) upload it.

    Returns the number of playbooks rendered. A local copy is dropped under
    ``utils/temp_rendered/`` for inspection; the artifact is uploaded straight
    to ``gs://<bucket>/playbooks/``.
    """
    playbooks_dir = _PROJECT_ROOT / "rates_agent" / "playbooks"
    out_dir = Path(__file__).resolve().parent / "temp_rendered"
    out_dir.mkdir(exist_ok=True)

    engine = get_db_engine()
    bucket = _gcp_bucket() if upload else None
    if upload and bucket is None:
        print("[ABORT] No GCS bucket — cannot upload rendered WIRP playbook.")
        return 0

    rendered_count = 0
    for seed_path in sorted(playbooks_dir.glob("*.yml")) + sorted(playbooks_dir.glob("*.yaml")):
        try:
            playbook = yaml.safe_load(seed_path.read_text(encoding="utf-8")) or {}
        except Exception as exc:  # noqa: BLE001
            print(f"  [WARNING] Could not parse {seed_path.name}: {exc}")
            continue
        if not playbook_has_wirp_section(playbook):
            continue

        rendered_text, provenance = render_wirp_playbook(seed_path, engine)
        local_copy = out_dir / seed_path.name
        local_copy.write_text(rendered_text, encoding="utf-8")
        print(
            f"  [RENDERED] {seed_path.name}: "
            f"universe={provenance['universe_count']} row(s) "
            f"from {provenance['event_calendar_meeting_rows']} meeting(s) "
            f"[horizon: {provenance['horizon']}]"
        )

        if upload and bucket is not None:
            blob_name = f"playbooks/{seed_path.name}"
            bucket.blob(blob_name).upload_from_filename(str(local_copy))
            print(f"  [SUCCESS] Uploaded effective WIRP playbook -> gs://{BUCKET_NAME}/{blob_name}")

        rendered_count += 1

    if rendered_count == 0:
        print("[INFO] No WIRP-section playbooks found — nothing to render.")
    return rendered_count


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Render & push the effective WIRP playbook (ADR 0009 §3)."
    )
    parser.add_argument(
        "--no-upload",
        action="store_true",
        help="Render to utils/temp_rendered/ only; do not upload to GCS.",
    )
    args = parser.parse_args()
    render_and_push(upload=not args.no_upload)
