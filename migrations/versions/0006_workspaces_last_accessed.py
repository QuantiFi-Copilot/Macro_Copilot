"""workspaces last_accessed_at

Revision ID: 0006_workspaces_last_accessed
Revises: 0005_workspace_slug
Create Date: 2026-05-12

Phase 0 PR 11.  Adds ``workspaces.last_accessed_at`` — a nullable
timestamp recording the most recent time the workspace was opened
via ``GET /api/v1/workspace/{slug}``.

Why this column exists
----------------------
Two reasons, in order of importance:

  1. **Migration-framework verification.**  Phase 0 closes by
     proving that the Alembic substrate handles a forward
     migration cleanly: upgrade → INSERT → downgrade → re-upgrade
     leaves data intact.  This migration is the target the
     verification test exercises (``tests/integration/
     test_migration_framework.py``).  A small, reversible column
     add is the right shape for that test.

  2. **Workspace GC scaffolding.**  A future GC sweep will reclaim
     stale workspaces by age.  ``last_accessed_at`` is the column
     the sweep keys on.  This PR ships the column NULLable and
     unused; the sweep that populates + reads it lives in Phase 1
     or later.

The column is annotated as deliberate dead weight in the
``state_schema.md`` doc so a future reader doesn't grep for
writers and conclude it's broken.

Nullable
--------
Existing workspace rows (if any) and rows produced by current
code paths leave ``last_accessed_at`` NULL.  The partial index
``ix_workspaces_last_accessed_active`` filters NULLs out, so
the addition is non-blocking on any existing data.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0006_workspaces_last_accessed"
down_revision: Union[str, None] = "0005_workspace_slug"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCHEMA = "copilot_state"


def upgrade() -> None:
    op.add_column(
        "workspaces",
        sa.Column(
            "last_accessed_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment=(
                "Most recent open time for the workspace.  Populated by "
                "GET /api/v1/workspace/{slug} when the workspace GC "
                "sweep lands (Phase 1+).  NULL on pre-PR-11 rows and "
                "on rows whose code path has not yet been wired to "
                "stamp this column."
            ),
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_workspaces_last_accessed_active",
        "workspaces",
        ["last_accessed_at"],
        postgresql_where=sa.text("last_accessed_at IS NOT NULL"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workspaces_last_accessed_active",
        table_name="workspaces",
        schema=SCHEMA,
    )
    op.drop_column("workspaces", "last_accessed_at", schema=SCHEMA)
