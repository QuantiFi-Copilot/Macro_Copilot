import os
from pathlib import Path
from google.cloud import storage

# --- CONFIGURATION ---
BUCKET_NAME = "quantifi-fx-data-sacha"


def push_playbooks_to_gcp():
    """Sync local YAML playbooks from multiple agent folders to the GCP bucket."""

    current_dir = Path(__file__).parent
    project_root = current_dir.parent

    gcp_key_path = project_root / "secure_keys" / "library-extractor-key.json"

    playbook_dirs = [
        project_root / "rates_agent" / "playbooks",
        project_root / "fx_agent" / "playbooks",
    ]

    if not gcp_key_path.exists():
        print(f"Error: Could not find GCP key at {gcp_key_path}")
        return

    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(gcp_key_path)

    try:
        client = storage.Client()
        bucket = client.bucket(BUCKET_NAME)
    except Exception as e:
        print(f"Failed to authenticate with GCP: {e}")
        return

    all_playbook_files = []

    for playbooks_dir in playbook_dirs:
        if not playbooks_dir.exists():
            print(f"[WARNING] Playbooks directory not found: {playbooks_dir}")
            continue

        print(f"Scanning for playbooks in: {playbooks_dir}")
        files = list(playbooks_dir.glob("*.yml")) + list(playbooks_dir.glob("*.yaml"))
        all_playbook_files.extend(files)

    if not all_playbook_files:
        print("No YAML playbooks found to upload.")
        return

    # Deduplicate by filename just in case
    unique_files = {}
    for file_path in all_playbook_files:
        unique_files[file_path.name] = file_path

    playbook_files = list(unique_files.values())

    print(f"Found {len(playbook_files)} playbook(s). Syncing to GCP bucket '{BUCKET_NAME}'...")

    success_count = 0
    for file_path in playbook_files:
        try:
            blob_name = f"playbooks/{file_path.name}"
            blob = bucket.blob(blob_name)

            blob.upload_from_filename(str(file_path))
            print(f"  [SUCCESS] Uploaded {file_path.name} -> gs://{BUCKET_NAME}/{blob_name}")
            success_count += 1
        except Exception as e:
            print(f"  [FAILED] Could not upload {file_path.name}: {e}")

    print(f"\nSync complete: {success_count}/{len(playbook_files)} playbooks pushed to GCP.")


if __name__ == "__main__":
    push_playbooks_to_gcp()
