"""noop sentinel for round-trip migration testing

Revision ID: 0002_noop_sentinel
Revises: 0001_initial_copilot_state
Create Date: 2026-05-12

A deliberate no-op revision that exists for one purpose: the
forward/backward migration test in ``tests/state/test_migrations.py``
exercises a 2-step upgrade and a 2-step downgrade so that the test
covers Alembic's ability to chain revisions, not just a single initial
revision.

A single-migration head means the test could only validate
``base → 0001 → base``.  With this sentinel in place the test
additionally validates ``0001 → 0002 → 0001`` and the full
``base → 0001 → 0002 → 0001 → base`` round trip.

This file should NEVER acquire real DDL.  When the next real migration
is added (Phase 0 week 3+: LangGraph PostgresSaver checkpoint table,
etc.), it should be a new revision file that points at this one as
its ``down_revision`` — leaving this no-op in the chain forever as a
permanent test fixture.

If a future migration legitimately needs to come BEFORE this sentinel
in the chain (rare; unusual), it should be inserted by hand-editing
the ``down_revision`` fields rather than removing this file.
"""
from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = "0002_noop_sentinel"
down_revision: Union[str, None] = "0001_initial_copilot_state"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """No-op.  Exists for the round-trip migration test."""


def downgrade() -> None:
    """No-op.  Exists for the round-trip migration test."""
