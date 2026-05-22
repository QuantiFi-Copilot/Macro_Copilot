"""workspace url slug

Revision ID: 0005_workspace_slug
Revises: 0004_artifact_app_version
Create Date: 2026-05-12

Phase 0 PR 10.  Adds ``workspaces.slug`` — the URL handle for a
workspace.

Why a separate column (not name)
--------------------------------
``name`` is human-readable, mutable, and may contain arbitrary
unicode / spaces / punctuation.  ``slug`` is the URL-routable
counterpart:

  - lowercased,
  - ASCII alphanumerics + ``-`` only,
  - always suffixed with an 8-char hex slice of the workspace's
    UUID so two workspaces sharing a name produce distinct slugs,
  - **stable across renames** — the URL keeps working even when
    the user edits the display name.

The slug is derived ONCE at create time by
``state.workspace_repo.create_workspace`` and never re-derived.

Why nullable
------------
The ``workspaces`` table has existed since PR 4 with no rows
written from production code (workspaces were planned for PR 10
to light up).  In case any test fixture or stray manual INSERT
left rows behind, a NOT NULL would require a backfill the schema
layer cannot author honestly.  ``create_workspace`` always sets
the slug; downstream lookup paths refuse rows where it's NULL.

Partial unique index
--------------------
``ix_workspaces_slug_active`` enforces slug uniqueness only when
``slug IS NOT NULL``.  Legacy NULL-slug rows do not participate
in the constraint, so the column add is non-blocking on any
existing data.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0005_workspace_slug"
down_revision: Union[str, None] = "0004_artifact_app_version"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCHEMA = "copilot_state"


def upgrade() -> None:
    op.add_column(
        "workspaces",
        sa.Column(
            "slug",
            sa.Text(),
            nullable=True,
            comment=(
                "URL handle for the workspace.  Lowercased ASCII "
                "alphanumerics + '-' only, always suffixed with an "
                "8-char hex slice of the UUID.  Stable across renames "
                "of the display ``name``."
            ),
        ),
        schema=SCHEMA,
    )
    # Partial unique index — only NOT-NULL slugs participate, so
    # legacy / system rows with no slug don't conflict.
    op.create_index(
        "ix_workspaces_slug_active",
        "workspaces",
        ["slug"],
        unique=True,
        postgresql_where=sa.text("slug IS NOT NULL"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workspaces_slug_active",
        table_name="workspaces",
        schema=SCHEMA,
    )
    op.drop_column("workspaces", "slug", schema=SCHEMA)
