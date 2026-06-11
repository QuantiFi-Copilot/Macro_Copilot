"""workspaces.run_audit — open-DAG audit sidecar (Phase D / D9)

Revision ID: 0010_workspace_run_audit
Revises: 0009_workspace_bound_slot_values
Create Date: 2026-06-11

Orchestration-upgrade Phase D (design decision D9): persist the
open-DAG pipeline's intent-audit chain alongside the workspace so the
build page can render "what I understood / checked / fixed" —
decomposition + ``expected_answer_shape`` + per-check verdicts + the
bounded self-correction ``recompose_trace``.

P4 determinism safety
---------------------
This is a NON-HASHED SIDECAR by construction: ``run_audit`` lives on
the ``workspaces`` row (referencing metadata), never inside the
content-addressed substrate.  ``dags.hash`` / ``dag_nodes`` /
``artifact_metadata`` hashes are computed before the workspace row
exists and never read this column — adding (or later dropping) it
cannot change any content hash or replay result.

Column
------
``run_audit`` — JSONB blob, nullable.  Shape (versioned):

    {
      "schema_version": 1,
      "intent_chain": <IntentChain.model_dump(mode="json")>,
      "expected_answer_shape": <str | null>,
      "recompose_trace": [<RecomposeStep.model_dump(mode="json")>, ...]
    }

NULL is the signal "this workspace pre-dates the audit sidecar or was
persisted by a lane that has no IntentChain" (template lane,
direct-fetch persistence).  Renderers treat NULL as "no audit
available" — never an error.

Index discipline
----------------
No index.  The blob is read only as part of the workspace-detail row
fetch; no JSON-path filters in V1.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0010_workspace_run_audit"
down_revision: Union[str, None] = "0009_workspace_bound_slot_values"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCHEMA = "copilot_state"


def upgrade() -> None:
    op.add_column(
        "workspaces",
        sa.Column(
            "run_audit",
            sa.dialects.postgresql.JSONB(),
            nullable=True,
            comment=(
                "Open-DAG intent-audit sidecar (Phase D / D9): the "
                "IntentChain + expected_answer_shape + recompose_trace "
                "captured at persist time so the build page can render "
                "'what I understood / checked / fixed'.  Non-hashed "
                "metadata — NEVER part of any content-addressed "
                "identity (P4).  NULL on pre-audit rows and on lanes "
                "without an IntentChain (template / direct-fetch)."
            ),
        ),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("workspaces", "run_audit", schema=SCHEMA)
