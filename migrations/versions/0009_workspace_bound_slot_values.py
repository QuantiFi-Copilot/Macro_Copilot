"""workspaces.bound_slot_values + template_id

Revision ID: 0009_workspace_bound_slot_values
Revises: 0008_backtest_archetype
Create Date: 2026-05-13

Phase 3 PR B.  Adds two nullable columns to ``copilot_state.workspaces``
so the Build UI's fork-with-overrides path can faithfully re-bind a
template at variant-creation time without reverse-engineering
slot_values from the persisted DAG topology.

Why this is here
----------------
PR A's ``persist_dag_from_workflow_result`` writes per-node params
into ``dag_nodes.params``, but those are post-binding params —
they're sufficient to *replay* an artifact byte-identically (PR A's
goal) but NOT to *fork* a workspace with one slot changed.  Forking
requires the ORIGINAL bound slot_values dict so PR B can apply a
patch and re-run ``template.bind(patched_values)`` against the
same template.

Columns
-------
``template_id`` — short string echoing ``WorkflowTemplate.template_id``.
    The substrate's template registry is the resolution source at
    fork time; storing the id is sufficient because templates are
    immutable once registered.  Nullable so legacy workspaces
    (pre-PR-B) keep working — their UI just hides the fork
    affordance.

``bound_slot_values`` — JSONB blob of the dict the user / LLM passed
    to ``template.bind(...)`` to produce this workspace's DAG.
    Carries every slot value verbatim so a future fork can apply a
    partial override.  Nullable for the same back-compat reason.

Index discipline
----------------
No index on either column.  ``bound_slot_values`` is JSONB and
queried only as part of the workspace row read (no JSON-path
filters in V1); ``template_id`` is read but never filtered on
in V1.  PR C may add a partial index keyed on ``template_id``
when the variant-comparison surface starts grouping by template.

Forward-compat
--------------
NULL is the signal "this workspace pre-dates the fork-with-overrides
substrate."  The fork endpoint's handler returns 409 Conflict on
NULL ``bound_slot_values`` with a diagnostic message rather than
guessing; the Build UI's fork button is disabled for those rows.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0009_workspace_bound_slot_values"
down_revision: Union[str, None] = "0008_backtest_archetype"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCHEMA = "copilot_state"


def upgrade() -> None:
    op.add_column(
        "workspaces",
        sa.Column(
            "template_id",
            sa.String(length=128),
            nullable=True,
            comment=(
                "WorkflowTemplate.template_id this workspace's DAG was "
                "produced by.  NULL on pre-PR-B rows; PR B's fork-with-"
                "overrides endpoint requires non-NULL.  Read-only on "
                "the workspace row — varying templates produce "
                "different DAG hashes, so the column is logically "
                "constant per workspace lifetime."
            ),
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "workspaces",
        sa.Column(
            "bound_slot_values",
            sa.dialects.postgresql.JSONB(),
            nullable=True,
            comment=(
                "Slot values the user / LLM passed to template.bind() "
                "to produce this workspace.  Persisted so the fork-"
                "with-overrides endpoint can re-bind with a patch.  "
                "NULL on legacy workspaces — fork UI is disabled for "
                "those rows."
            ),
        ),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("workspaces", "bound_slot_values", schema=SCHEMA)
    op.drop_column("workspaces", "template_id", schema=SCHEMA)
