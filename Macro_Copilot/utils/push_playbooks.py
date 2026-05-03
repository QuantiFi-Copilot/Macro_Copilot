import os
from pathlib import Path
from google.cloud import storage

# --- CONFIGURATION ---
BUCKET_NAME = "macro-storage-bucket" 

def push_playbooks_to_gcp():
    """Syncs local YAML playbooks to the GCP bucket."""
    
    # --- DYNAMIC PATH RESOLUTION ---
    # Script is in: .../Macro_Copilot/utils/push_playbooks.py
    current_dir = Path(__file__).parent
    project_root = current_dir.parent
    
    # Dynamically find the key and playbooks relative to the project root
    gcp_key_path = project_root / "secure_keys" / "library-extractor-key.json"
    playbooks_dirs = sorted(project_root.glob("*_agent/playbooks"))

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

    if not playbooks_dirs:
        print(f"Error: Could not find any *_agent/playbooks directory under {project_root}")
        return

    print("Scanning for playbooks in:")
    for playbooks_dir in playbooks_dirs:
        print(f"  - {playbooks_dir}")

    # Grab all .yml and .yaml files from every agent-level playbook directory.
    playbook_files = []
    for playbooks_dir in playbooks_dirs:
        playbook_files.extend(playbooks_dir.glob("*.yml"))
        playbook_files.extend(playbooks_dir.glob("*.yaml"))
    playbook_files = sorted(playbook_files)
    
    if not playbook_files:
        print("No YAML playbooks found to upload.")
        return

    print(f"Found {len(playbook_files)} playbook(s). Syncing to GCP bucket '{BUCKET_NAME}'...")

    success_count = 0
    for file_path in playbook_files:
        try:
            agent_name = file_path.parent.parent.name
            # Create the 'playbooks/' folder structure inside the bucket
            blob_name = f"playbooks/{agent_name}/{file_path.name}"
            blob = bucket.blob(blob_name)
            
            # Execute the upload
            blob.upload_from_filename(str(file_path))
            print(f"  [SUCCESS] Uploaded {file_path.name} -> gs://{BUCKET_NAME}/{blob_name}")
            success_count += 1
        except Exception as e:
            print(f"  [FAILED] Could not upload {file_path.name}: {e}")

    print(f"\nSync complete: {success_count}/{len(playbook_files)} playbooks pushed to GCP.")

if __name__ == "__main__":
    push_playbooks_to_gcp()
