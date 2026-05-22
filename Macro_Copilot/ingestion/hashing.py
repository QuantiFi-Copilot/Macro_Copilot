"""ingestion.hashing — deterministic content hash for the parquet dedup gate.

The parquet ingestion pipeline writes one row to ``load_audit`` per ingested
artifact, recording the ``normalized_data_hash`` so that re-uploading the
same economic payload (even with a different ``extracted_at`` timestamp or
git commit) deduplicates cleanly via the ``SKIPPED_DUPLICATE`` audit status.

This module owns the hash function and its helpers in a stand-alone surface
so they can be:

  - imported without pulling in ``google.cloud.storage`` /
    ``sqlalchemy`` / the ``database.database`` module (which is what the
    parent ``ingest_parquet`` module imports at module load),
  - tested for byte-identical output across Python / Pandas / NumPy
    versions in ``tests/state/test_hash_stability.py``.

The hash is intentionally NOT a hash of the raw parquet bytes — it is a
hash of the *economic content* normalized to a canonical form, so a
re-ingestion that differs only in lineage metadata (timestamp, git commit,
extractor version, etc.) deduplicates correctly.

Closes ``docs/technical_debt.md`` item #20 for the ingestion layer.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, List

import pandas as pd


# Columns that vary run-to-run on identical economic content.  Excluded from
# the normalized hash so a re-extract with a different timestamp / git
# commit / playbook hash still produces the same content hash, which is
# what makes the dedup gate work.
NORMALIZED_HASH_EXCLUDED_COLUMNS: frozenset[str] = frozenset({
    "playbook_hash",
    "git_commit_hash",
    "extractor_version",
    "extraction_mode",
    "requested_start_date",
    "requested_end_date",
    "extracted_at",
    "source_file",
    "source_file_name",
    "source_file_hash",
    "normalized_data_hash",
    "load_id",
    "created_at",
    "updated_at",
    "ingested_at",
    "notes",
    "status",
})


def _normalize_hash_value(value: Any) -> str:
    """
    Convert a single cell to a deterministic string representation.

    Rules (applied in order):
      1. ``NaN`` / ``None``                        → empty string.
      2. ``pd.Timestamp``                          → ISO 8601 string.
      3. Other ``.isoformat()``-capable (date,
         datetime, ...)                           → ISO 8601 string.
      4. ``bool``                                   → ``"true"`` / ``"false"``.
      5. NumPy scalar (anything exposing ``.item()``)
                                                    → Python native via
         ``.item()``, then ``str()``.
      6. Anything else                              → ``str(value)``.

    ``str()`` on Python natives (int, float, str, NoneType) is stable
    across Python 3.x; the ``.item()`` unwrap above ensures NumPy scalars
    take that stable path rather than NumPy's own ``__str__`` (which
    can vary by NumPy version).
    """
    if pd.isna(value):
        return ""

    if isinstance(value, pd.Timestamp):
        return value.isoformat()

    if hasattr(value, "isoformat") and not isinstance(value, str):
        try:
            return value.isoformat()
        except Exception:
            pass

    if isinstance(value, bool):
        return "true" if value else "false"

    if hasattr(value, "item"):
        try:
            value = value.item()
        except Exception:
            pass

    return str(value)


def _build_normalized_hash_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """
    Build a normalized DataFrame for semantic dedup hashing.

    Pipeline:
      1. Drop columns in :data:`NORMALIZED_HASH_EXCLUDED_COLUMNS`
         (lineage / run-variant fields).
      2. Apply :func:`_normalize_hash_value` to every cell, producing a
         uniformly ``object``-dtype DataFrame of Python strings.
      3. Reorder columns alphabetically (canonical order).
      4. Sort rows by the canonicalized column tuple (stable mergesort)
         so the hash is invariant to caller-side row order.
    """
    normalized = df.copy()

    keep_columns = [
        c for c in normalized.columns if c not in NORMALIZED_HASH_EXCLUDED_COLUMNS
    ]
    normalized = normalized[keep_columns]

    for col in normalized.columns:
        normalized[col] = normalized[col].map(_normalize_hash_value)

    normalized = normalized.reindex(sorted(normalized.columns), axis=1)
    normalized = normalized.sort_values(
        by=list(normalized.columns), kind="mergesort"
    ).reset_index(drop=True)
    return normalized


def compute_normalized_data_hash(df: pd.DataFrame) -> str:
    """
    Compute a stable SHA256 hash of the economically meaningful DataFrame contents.

    Determinism contract
    --------------------
    The hash must be byte-identical for the same logical dataset across:

      - different Python versions (3.11, 3.12, ...),
      - different Pandas versions (2.0, 2.1, ...),
      - different NumPy versions,
      - different machine architectures (x86_64, arm64).

    The serialization path that guarantees this:

      1. :func:`_build_normalized_hash_dataframe` walks every cell through
         :func:`_normalize_hash_value` which produces a Python ``str`` for
         every non-null entry (``NaN`` → ``""``).  After this pass, the
         DataFrame's dtype is uniformly ``object`` containing Python
         strings only.

      2. Extract the row-wise tuples via ``itertuples`` (pure-Python path,
         no NumPy involvement) and serialize the ``{columns, rows}``
         shape as canonical JSON.  No CSV quoting rules, no float
         formatting, no version-dependent representation choices.

      3. Hash the UTF-8 bytes of that JSON.

    Pinned, cross-version-stable test vectors live in
    ``tests/state/test_hash_stability.py``.
    """
    normalized = _build_normalized_hash_dataframe(df)

    columns: List[str] = list(normalized.columns)
    rows: List[List[str]] = [
        list(row) for row in normalized.itertuples(index=False, name=None)
    ]

    payload = json.dumps(
        {"columns": columns, "rows": rows},
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
