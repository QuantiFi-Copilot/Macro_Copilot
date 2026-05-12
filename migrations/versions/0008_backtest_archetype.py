"""backtest workflow-archetype closed-family extension (sentinel)

Revision ID: 0008_backtest_archetype
Revises: 0007_tradeset_artifact_type
Create Date: 2026-05-12

Phase 1 PR 19.  Closed-family extension sentinel for the new
``backtest`` workflow archetype (the 5th member of the closed
``WorkflowArchetype`` Literal set in ``shared/workflow/template.py``).

Why this is a no-op DDL migration
---------------------------------
The ``WorkflowArchetype`` set is enforced at the Python layer by
the closed Literal + the matching ``WORKFLOW_ARCHETYPES`` tuple in
``shared/workflow/template.py``.  No DB column carries the archetype
name today; future tables that store archetype tags will reference
that single source of truth.

This migration carries:

  - ZERO schema changes.  Empty upgrade + empty downgrade.
  - A revision-id ANCHOR pinning the closed-family-extension PR's
    landing in the migration chain.  Same audit-trail discipline as
    PR 12's TradeSet sentinel (migration 0007).

If a future PR adds a NEW archetype it follows the same pattern:
one Literal + tuple edit in template.py + one no-op sentinel
migration here.  The closed family stays auditable from the
migration history alone.

Documented in ``docs/architecture/state_schema.md`` under
"closed-family discipline".
"""

from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = "0008_backtest_archetype"
down_revision: Union[str, None] = "0007_tradeset_artifact_type"
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
