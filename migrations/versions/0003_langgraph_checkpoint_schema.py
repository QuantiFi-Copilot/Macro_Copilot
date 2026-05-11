"""langgraph_checkpoint schema namespace

Revision ID: 0003_langgraph_checkpoint_schema
Revises: 0002_noop_sentinel
Create Date: 2026-05-12

Creates an empty `langgraph_checkpoint` Postgres schema namespace.  The
LangGraph framework's `AsyncPostgresSaver.setup()` (called from
`api/dependencies.py` at app startup) creates the framework-internal
tables (`checkpoints`, `checkpoint_blobs`, `checkpoint_writes`,
`checkpoint_migrations`) inside this schema, NOT here.

Why we own the schema namespace but not its tables
---------------------------------------------------
Our Alembic discipline (PR 4) says all DDL in `copilot_state` goes through
Alembic.  LangGraph's checkpoint tables intentionally live in a SEPARATE
schema because:

  1. Their shape can change between `langgraph-checkpoint-postgres`
     library versions.  If we mirrored those tables in Alembic, our
     migration would lag the library and produce schema drift.
  2. The library's `setup()` is the canonical bootstrap path; manually
     replicating it would create two sources of truth.
  3. Keeping them in a dedicated schema means our `copilot_state` test
     `test_creates_all_expected_tables` (which asserts exact set
     equality) stays clean as LangGraph evolves.

What this migration does
------------------------
Creates the empty `langgraph_checkpoint` schema namespace so that:

  - The schema exists before `AsyncPostgresSaver(pool).setup()` runs at
    app startup.  Without the schema, `setup()` would either fail (if
    the connection's search_path can't find anywhere to put tables) or
    silently fall back to `public` (defeating the isolation goal).
  - The connection pool's `search_path=langgraph_checkpoint,public` is
    a well-defined target — the schema is guaranteed to exist when the
    first connection is acquired.

Downgrade does not drop the schema.  Two reasons:

  1. Symmetric with `0001_initial_copilot_state.downgrade()` —
     schemas managed via env.py / library `setup()` are not
     Alembic's responsibility to tear down.
  2. If LangGraph's `setup()` has populated tables inside it, dropping
     the schema would silently destroy checkpoint state.  Operator
     action (manual `DROP SCHEMA langgraph_checkpoint CASCADE`) is the
     correct way to fully wipe it.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0003_langgraph_checkpoint_schema"
down_revision: Union[str, None] = "0002_noop_sentinel"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


LANGGRAPH_SCHEMA = "langgraph_checkpoint"


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {LANGGRAPH_SCHEMA}")


def downgrade() -> None:
    # See module docstring — we deliberately do NOT drop the schema.
    # If a downgrade really wants to wipe it, the operator should run
    # `DROP SCHEMA langgraph_checkpoint CASCADE` manually.
    pass
