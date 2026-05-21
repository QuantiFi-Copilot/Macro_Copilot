"""utils/backfill_wirp_links.py — link event_calendar central-bank-meeting rows
to their WIRP meeting instrument (work order B2, D-wirp, ADR 0009 §5).

Once WIRP market data is ingested, every ``wirp_meeting`` row in
``instrument_master`` corresponds to exactly one ``central_bank_meeting`` row in
``event_calendar`` — the two share ``(central_bank, meeting_date)``. This step
sets that event row's ``related_instrument_id`` foreign key, which ADR 0004
reserved for "the per-meeting WIRP implied-rate instrument" and the Phase-3
``fomc_surprise`` primitive / FOMC event-study template consume.

It is the WIRP half of the cross-pipeline link. The other half lives in
``database.upsert_event_calendar``: that helper COALESCE-preserves
``related_instrument_id`` (ADR 0009 §5b), so a D-cb event-calendar re-run —
which always re-upserts the central-bank-meeting rows with the column NULL —
cannot wipe a link this step has set.

Design (ADR 0009 §5a):

  * Run it LOCALLY (it needs the database), once per operator cycle, AFTER WIRP
    ingestion. Re-running picks up newly-created WIRP instruments as the
    central-bank calendar extends, and re-establishes any link.
  * It is IDEMPOTENT — the ``IS DISTINCT FROM`` guard makes an already-correct
    link a no-op, so a re-run touches only rows that actually changed.
  * It links ONLY WIRP instruments that genuinely have ``market_data_daily``
    rows. The ingester upserts ``instrument_master`` OUTSIDE its critical
    market-data transaction (``ingest_parquet.py`` — idempotent on
    ``(vendor, vendor_ticker)``), so a WIRP load that fails after that upsert
    can leave orphan ``wirp_meeting`` instruments with no market data. The
    ``EXISTS (market_data_daily …)`` clause makes the backfill self-validating:
    a dangling FK to a data-less orphan is never created.

The join keys (ADR 0009 §1): ``instrument_master`` has no typed ``central_bank``
or ``meeting_date`` column, so the WIRP meeting instrument stores the meeting
date in the typed ``maturity_date`` column and the central bank in
``attributes->>'central_bank'``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List

from sqlalchemy import text

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from database.database import get_db_engine  # noqa: E402


# How many WIRP meeting instruments exist at all.
_COUNT_WIRP_SQL = """
SELECT COUNT(*)
FROM macro_data.instrument_master
WHERE instrument_type = 'wirp_meeting'
"""

# WIRP meeting instruments that carry NO market_data_daily rows — orphans of a
# WIRP load that failed after the (out-of-critical-txn) instrument_master
# upsert. They are deliberately NOT linked; surfaced here as an operator signal.
_ORPHAN_WIRP_SQL = """
SELECT im.instrument_id, im.vendor_ticker
FROM macro_data.instrument_master im
WHERE im.instrument_type = 'wirp_meeting'
  AND NOT EXISTS (
        SELECT 1 FROM macro_data.market_data_daily md
        WHERE md.instrument_id = im.instrument_id)
ORDER BY im.vendor_ticker
"""

# The (event row -> WIRP instrument) links this run WOULD set: a central-bank-
# meeting event row whose related_instrument_id does not yet point at the WIRP
# instrument that shares its (central_bank, meeting_date) and has market data.
_PREVIEW_LINKS_SQL = """
SELECT ec.event_id,
       ec.event_type,
       ec.central_bank,
       ec.release_date,
       ec.related_instrument_id AS current_link,
       im.instrument_id         AS wirp_instrument_id,
       im.vendor_ticker         AS wirp_vendor_ticker
FROM macro_data.event_calendar ec
JOIN macro_data.instrument_master im
  ON im.instrument_type = 'wirp_meeting'
 AND ec.central_bank    = im.attributes->>'central_bank'
 AND ec.release_date    = im.maturity_date
WHERE ec.event_category = 'central_bank_meeting'
  AND EXISTS (
        SELECT 1 FROM macro_data.market_data_daily md
        WHERE md.instrument_id = im.instrument_id)
  AND ec.related_instrument_id IS DISTINCT FROM im.instrument_id
ORDER BY ec.release_date, ec.central_bank
"""

# The backfill itself — the ADR 0009 §5a UPDATE. Idempotent (IS DISTINCT FROM)
# and self-validating (EXISTS market_data_daily).
_BACKFILL_SQL = """
UPDATE macro_data.event_calendar ec
   SET related_instrument_id = im.instrument_id
  FROM macro_data.instrument_master im
 WHERE im.instrument_type = 'wirp_meeting'
   AND ec.event_category  = 'central_bank_meeting'
   AND ec.central_bank    = im.attributes->>'central_bank'
   AND ec.release_date    = im.maturity_date
   AND EXISTS (
         SELECT 1 FROM macro_data.market_data_daily md
         WHERE md.instrument_id = im.instrument_id)
   AND ec.related_instrument_id IS DISTINCT FROM im.instrument_id
"""


def backfill_wirp_links(engine, dry_run: bool = False) -> Dict[str, Any]:
    """Link ``event_calendar`` central-bank-meeting rows to their WIRP
    instrument.

    Returns a summary dict: ``wirp_instruments`` (total ``wirp_meeting`` rows),
    ``orphan_instruments`` (WIRP instruments with no market data — not linked),
    ``pending_links`` (links this run would/did set), ``linked`` (rows actually
    updated; 0 on a dry run), ``dry_run``.

    ``dry_run=True`` runs only the read-side queries and prints the links that
    WOULD be set — no ``UPDATE`` is issued.
    """
    with engine.connect() as conn:
        wirp_total = int(conn.execute(text(_COUNT_WIRP_SQL)).scalar() or 0)

        if wirp_total == 0:
            print(
                "[INFO] No 'wirp_meeting' instruments in instrument_master — "
                "nothing to link. (Run this after WIRP ingestion.)"
            )
            return {
                "wirp_instruments": 0,
                "orphan_instruments": 0,
                "pending_links": 0,
                "linked": 0,
                "dry_run": dry_run,
            }

        orphans: List[Any] = list(
            conn.execute(text(_ORPHAN_WIRP_SQL)).mappings().all()
        )
        pending: List[Any] = list(
            conn.execute(text(_PREVIEW_LINKS_SQL)).mappings().all()
        )

    print(
        f"[INFO] WIRP meeting instruments: {wirp_total} "
        f"({wirp_total - len(orphans)} with market data, "
        f"{len(orphans)} orphan)."
    )
    if orphans:
        print(
            f"  [WARNING] {len(orphans)} WIRP instrument(s) have NO "
            "market_data_daily rows — NOT linked (a WIRP load may have failed "
            "after the instrument_master upsert):"
        )
        for row in orphans:
            print(f"    - {row['vendor_ticker']} (instrument_id={row['instrument_id']})")

    if not pending:
        print("[OK] Every WIRP-linkable central-bank-meeting row is already linked — no change.")
        return {
            "wirp_instruments": wirp_total,
            "orphan_instruments": len(orphans),
            "pending_links": 0,
            "linked": 0,
            "dry_run": dry_run,
        }

    print(f"[INFO] {len(pending)} central-bank-meeting row(s) to (re)link:")
    for row in pending:
        print(
            f"    - {row['central_bank']:<5} {row['release_date']} "
            f"({row['event_type']}, event_id={row['event_id']}): "
            f"related_instrument_id {row['current_link']} -> "
            f"{row['wirp_instrument_id']} [{row['wirp_vendor_ticker']}]"
        )

    if dry_run:
        print("[DRY-RUN] No UPDATE issued. Re-run without --dry-run to apply.")
        return {
            "wirp_instruments": wirp_total,
            "orphan_instruments": len(orphans),
            "pending_links": len(pending),
            "linked": 0,
            "dry_run": True,
        }

    with engine.begin() as conn:
        result = conn.execute(text(_BACKFILL_SQL))
        linked = int(result.rowcount or 0)

    print(f"[SUCCESS] Linked {linked} central-bank-meeting row(s) to their WIRP instrument.")
    if linked != len(pending):
        # The preview and the UPDATE ran on the same data; a divergence would
        # only happen if the DB changed between them — surface it, don't hide it.
        print(
            f"  [WARNING] Preview expected {len(pending)} link(s) but {linked} "
            "row(s) were updated — the database changed mid-run; re-run to confirm."
        )

    return {
        "wirp_instruments": wirp_total,
        "orphan_instruments": len(orphans),
        "pending_links": len(pending),
        "linked": linked,
        "dry_run": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "Link event_calendar central-bank-meeting rows to their WIRP "
            "meeting instrument (ADR 0009 §5). Run locally, after WIRP ingestion."
        )
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report the links that would be set; issue no UPDATE.",
    )
    args = parser.parse_args()

    backfill_wirp_links(get_db_engine(), dry_run=args.dry_run)
