"""TradeSet closed-family extension (sentinel)

Revision ID: 0007_tradeset_artifact_type
Revises: 0006_workspaces_last_accessed
Create Date: 2026-05-12

Phase 1 PR 12.  Closed-family extension sentinel for the TradeSet
artifact type.

Why this is a no-op DDL migration
---------------------------------
The ``copilot_state.artifact_metadata`` table's ``artifact_type``
column has no DB CHECK constraint enforcing the closed-family set —
closure is enforced by the Pydantic layer in ``state.schemas`` and
the registration tuple in ``state.artifact_store`` (a single source
of truth in Python, per the discipline established in
``state_schema.md``).

This migration carries:

  - ZERO schema changes.  Empty upgrade + empty downgrade.
  - A revision-id ANCHOR pinning the closed-family-extension PR's
    landing in the migration chain.  The pinned alembic head in
    ``tests/state/test_migrations.py`` and the head-check tests
    fail loudly if a later PR adds another migration without
    explicitly opting in to the chain.

If a future PR adds a NEW artifact type, it follows the same
pattern: one Pydantic-layer addition + one no-op sentinel
migration.  This keeps the closed family auditable from the
migration history alone.

Documented in ``docs/architecture/state_schema.md`` (PR 4) under
"closed-family discipline" — the discipline says closed-family
extensions are explicit, reviewable PRs.  This file IS that
explicitness for the TradeSet addition.
"""

from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = "0007_tradeset_artifact_type"
down_revision: Union[str, None] = "0006_workspaces_last_accessed"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """No-op.  Closed-family extension is enforced at the Python
    layer; the migration sentinel exists so the chain records the
    explicit addition."""
    pass


def downgrade() -> None:
    """No-op symmetric to upgrade."""
    pass
