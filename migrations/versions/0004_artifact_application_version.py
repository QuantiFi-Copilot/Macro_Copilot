"""link artifact_metadata to application_version

Revision ID: 0004_artifact_app_version
Revises: 0003_langgraph_checkpoint_schema
Create Date: 2026-05-12

Phase 0 PR 9.  Adds ``artifact_metadata.application_version_id`` —
a nullable FK to ``application_version.id`` recording which code
revision produced each artifact.

Why this is a separate migration (not in 0001)
----------------------------------------------
The PR 4 schema (revision 0001) created ``application_version`` and
``methodology_versions`` as standalone tables.  No artifact wrote
to either: PR 7's artifact-store wired methodology_version_ids
(BIGINT[]) but the column was left nullable + unpopulated because
nothing in PR 7 had a methodology_version_id to put there.  PR 8
did not touch artifact production.

PR 9 lights both columns up: ``state.methodology_versions`` and
``state.artifact_store`` are extended to register the YAML on
load, pin the version_id on each PrimitiveStep, and populate
both arrays at put time.  The new ``application_version_id``
column added here completes the picture — every artifact now
records BOTH which YAML versions contributed AND which code
revision produced it.

Why nullable
------------
- The column is added AFTER PR 7 / PR 8 wrote artifacts without
  it.  A NOT NULL on existing rows would require a backfill that
  the schema layer cannot author honestly (we genuinely do not
  know which git commit produced those rows).
- Tests + CLI paths that run without a git checkout (e.g. CI
  builds outside the repo, container images without git) cannot
  resolve a commit SHA.  The artifact store registers an
  ``"unknown"`` sentinel row in ``application_version`` for those
  cases and stamps that id; tests can assert the sentinel is
  used rather than failing the run.

ON DELETE RESTRICT
------------------
Matches ``methodology_version_ids`` semantics: deleting an
``application_version`` row would orphan the artifact's provenance
trail.  RESTRICT forces the operator to deal with the cascade
explicitly (almost always a mistake to attempt).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0004_artifact_app_version"
down_revision: Union[str, None] = "0003_langgraph_checkpoint_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCHEMA = "copilot_state"


def upgrade() -> None:
    """Add ``application_version_id`` FK column to artifact_metadata."""
    op.add_column(
        "artifact_metadata",
        sa.Column(
            "application_version_id",
            sa.BigInteger(),
            sa.ForeignKey(
                f"{SCHEMA}.application_version.id",
                ondelete="RESTRICT",
                name="fk_artifact_metadata_application_version",
            ),
            nullable=True,
            comment=(
                "FK to application_version.id pinning which code revision "
                "produced this artifact.  Nullable for backwards-compat with "
                "PR 7 / PR 8 rows that pre-date PR 9; new puts always set it."
            ),
        ),
        schema=SCHEMA,
    )
    # Partial index — only NOT-NULL rows participate.  Used by the
    # workspace replay route to surface the produced-under-commit and
    # diff against the current commit.
    op.create_index(
        "ix_artifact_metadata_application_version",
        "artifact_metadata",
        ["application_version_id"],
        postgresql_where=sa.text("application_version_id IS NOT NULL"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_artifact_metadata_application_version",
        table_name="artifact_metadata",
        schema=SCHEMA,
    )
    op.drop_constraint(
        "fk_artifact_metadata_application_version",
        "artifact_metadata",
        schema=SCHEMA,
        type_="foreignkey",
    )
    op.drop_column(
        "artifact_metadata",
        "application_version_id",
        schema=SCHEMA,
    )
