import os
from pathlib import Path

import yaml
from google.cloud import storage

# --- CONFIGURATION ---
BUCKET_NAME = "macro-storage-bucket"


def _has_enabled_resolver(file_path: Path) -> bool:
    """True iff the playbook declares an enabled ``otr_resolution`` block.

    Such playbooks have a DYNAMIC universe (seed + every bond ever on-the-run)
    and are uploaded by ``utils/render_effective_universe.py``, which expands
    the seed against ``otr_history`` first. Pushing the raw seed here would
    clobber that rendered effective universe in the bucket (ADR 0007), so this
    syncer skips them.
    """
    try:
        playbook = yaml.safe_load(file_path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 — a parse failure is not this script's job to surface
        return False
    block = playbook.get("otr_resolution")
    return isinstance(block, dict) and bool(block.get("enabled", False))


def _has_wirp_section(file_path: Path) -> bool:
    """True iff the playbook declares a ``wirp:`` section (ADR 0009).

    A WIRP playbook's universe is GENERATED from the central-bank-meeting
    calendar and uploaded by ``utils/render_wirp_universe.py``, which expands
    the empty seed against ``event_calendar`` first. Pushing the raw seed here
    would clobber that rendered effective universe in the bucket (ADR 0009 §3),
    so — exactly as for resolver-enabled playbooks — this syncer skips it.
    """
    try:
        playbook = yaml.safe_load(file_path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 — a parse failure is not this script's job to surface
        return False
    return isinstance(playbook.get("wirp"), dict)


def push_playbooks_to_gcp():
    """Syncs local YAML playbooks to the GCP bucket."""

    # --- DYNAMIC PATH RESOLUTION ---
    # Script is in: .../Macro_Copilot/utils/push_playbooks.py
    current_dir = Path(__file__).parent
    project_root = current_dir.parent

    # Dynamically find the key and playbooks relative to the project root
    gcp_key_path = project_root / "secure_keys" / "library-extractor-key.json"
    playbooks_dir = project_root / "rates_agent" / "playbooks"

    # Verify the key actually exists before trying to authenticate
    if not gcp_key_path.exists():
        print(f"Error: Could not find GCP key at {gcp_key_path}")
        return

    # Authenticate with GCP
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(gcp_key_path)

    try:
        client = storage.Client()
        bucket = client.bucket(BUCKET_NAME)
    except Exception as e:
        print(f"Failed to authenticate with GCP: {e}")
        return

    if not playbooks_dir.exists():
        print(f"Error: Could not find playbooks directory at {playbooks_dir}")
        return

    print(f"Scanning for playbooks in: {playbooks_dir}")

    # Grab all .yml and .yaml files
    playbook_files = list(playbooks_dir.glob("*.yml")) + list(playbooks_dir.glob("*.yaml"))

    if not playbook_files:
        print("No YAML playbooks found to upload.")
        return

    print(f"Found {len(playbook_files)} playbook(s). Syncing to GCP bucket '{BUCKET_NAME}'...")

    success_count = 0
    skipped_count = 0
    for file_path in playbook_files:
        if _has_enabled_resolver(file_path):
            print(
                f"  [SKIP] {file_path.name} declares an enabled otr_resolution "
                "block — push it via utils/render_effective_universe.py."
            )
            skipped_count += 1
            continue
        if _has_wirp_section(file_path):
            print(
                f"  [SKIP] {file_path.name} declares a wirp: section — "
                "push it via utils/render_wirp_universe.py."
            )
            skipped_count += 1
            continue
        try:
            # Create the 'playbooks/' folder structure inside the bucket
            blob_name = f"playbooks/{file_path.name}"
            blob = bucket.blob(blob_name)

            # Execute the upload
            blob.upload_from_filename(str(file_path))
            print(f"  [SUCCESS] Uploaded {file_path.name} -> gs://{BUCKET_NAME}/{blob_name}")
            success_count += 1
        except Exception as e:
            print(f"  [FAILED] Could not upload {file_path.name}: {e}")

    pushable = len(playbook_files) - skipped_count
    print(
        f"\nSync complete: {success_count}/{pushable} playbooks pushed to GCP"
        f" ({skipped_count} resolver-enabled playbook(s) skipped — render separately)."
    )

if __name__ == "__main__":
    push_playbooks_to_gcp()
