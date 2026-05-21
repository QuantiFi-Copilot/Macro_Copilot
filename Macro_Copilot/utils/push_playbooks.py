import os
from pathlib import Path

import yaml
from google.cloud import storage

# --- CONFIGURATION ---
# Default bucket is the shared rates-aligned bucket; override at runtime
# via the ``GCP_BUCKET_NAME`` env var (same convention as the
# historical_extractor / incremental_extractor scripts). Useful while
# the FX agent maintains its own bucket during Wave 1 — see
# fx_agent/ROADMAP.md for the migration plan to a single shared bucket.
BUCKET_NAME = os.getenv("GCP_BUCKET_NAME", "macro-storage-bucket")


def _discover_playbook_files(project_root: Path) -> list[Path]:
    """Return every agent-owned playbook in deterministic order."""
    files: list[Path] = []
    for playbooks_dir in sorted(project_root.glob("*_agent/playbooks")):
        files.extend(sorted(playbooks_dir.glob("*.yml")))
        files.extend(sorted(playbooks_dir.glob("*.yaml")))
    return sorted(files)


def _duplicate_playbook_names(files: list[Path]) -> dict[str, list[Path]]:
    """Find names that would collide under the flat GCS playbooks/ prefix."""
    by_name: dict[str, list[Path]] = {}
    for file_path in files:
        by_name.setdefault(file_path.name, []).append(file_path)
    return {name: paths for name, paths in by_name.items() if len(paths) > 1}


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

    # Dynamically find the key relative to the project root
    gcp_key_path = project_root / "secure_keys" / "library-extractor-key.json"

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

    print(f"Scanning for agent playbooks under: {project_root}")
    playbook_files = _discover_playbook_files(project_root)

    if not playbook_files:
        print("No YAML playbooks found to upload.")
        return

    duplicate_names = _duplicate_playbook_names(playbook_files)
    if duplicate_names:
        print("Error: duplicate playbook filenames would overwrite each other in GCS:")
        for name, paths in sorted(duplicate_names.items()):
            locations = ", ".join(str(path.relative_to(project_root)) for path in paths)
            print(f"  - {name}: {locations}")
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
            relative_path = file_path.relative_to(project_root)
            print(
                f"  [SUCCESS] Uploaded {relative_path} -> "
                f"gs://{BUCKET_NAME}/{blob_name}"
            )
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
