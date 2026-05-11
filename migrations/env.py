"""Alembic env.py for the Macro Copilot `copilot_state` schema.

Responsibilities:

  1. Resolve the DB URL from environment variables (matching the
     project's existing `database.database.get_db_engine` so dev and
     CI use the same convention).
  2. Tell Alembic where the version-tracking table lives — inside the
     `copilot_state` schema, not the default `public` — so all of
     this layer's state is co-located.
  3. Pin `target_metadata = None` for v1.  Initial migrations are
     hand-written via `op.create_table(...)`; autogenerate is opt-in
     and only meaningful once SQLAlchemy models are introduced
     (deferred — not in PR 4 scope).
  4. Support both online (live DB connection) and offline (SQL-only
     emit) modes.  Offline mode is useful for code-review and for
     bootstrapping a fresh prod DB from a captured SQL dump.

The connection convention here is intentionally the same as
`database.database.get_db_engine`: callers that already have the env
configured for the existing pipeline get migrations working without
extra setup.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool


# Schema everything in this project's state layer lives under.  Pinned
# here so the version table moves with it.
COPILOT_STATE_SCHEMA = "copilot_state"


# Alembic Config object, provides access to the values within the
# .ini file in use.
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


# Set target_metadata to None.  We're hand-writing migrations against
# `op.create_table` rather than reflecting from SQLAlchemy ORM models.
# Once Phase 0 week 3+ introduces typed state-layer models, set this
# to their `MetaData` and `--autogenerate` becomes useful.
target_metadata = None


def _resolve_db_url() -> str:
    """Build the DB URL from environment variables.

    Same convention as `Macro_Copilot/database/database.py:get_db_engine`:
      DB_USER       (default: quantuser — DEV only; CI / prod override)
      DB_PASSWORD   (default: myStrongPass — DEV only; CI / prod override)
      DB_HOST       (default: localhost)
      DB_PORT       (default: 5433 — matches docker-compose tsdb mapping)
      DB_NAME       (default: macrodata)

    The defaults are deliberately the project's existing dev defaults.
    Production deployments MUST override every value via env vars; the
    defaults are not safe outside a sandboxed local Docker network.
    """
    user = os.getenv("DB_USER", "quantuser")
    password = os.getenv("DB_PASSWORD", "myStrongPass")
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5433")
    db_name = os.getenv("DB_NAME", "macrodata")
    return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{db_name}"


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    Emits SQL to the standard output instead of executing it against a
    live database.  Useful for generating a deployable SQL dump for a
    fresh prod DB.

    Example:
      alembic upgrade head --sql > schema.sql
    """
    url = _resolve_db_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # All Alembic-managed state (version table) lives in the
        # copilot_state schema so it co-locates with the rest of this
        # layer.  Macro_data is untouched.
        version_table="alembic_version",
        version_table_schema=COPILOT_STATE_SCHEMA,
        # Include the schema name in generated DDL so emitted SQL is
        # fully qualified and unambiguous when applied to a fresh DB.
        include_schemas=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    Connects to the database with the URL from `_resolve_db_url`,
    applies pending migrations, and updates the alembic_version row
    inside the copilot_state schema.
    """
    # Override the placeholder URL in alembic.ini with the
    # env-resolved one.  This is the load-bearing line that makes
    # `alembic upgrade head` work from the project root in any
    # environment.
    config_section = config.get_section(config.config_ini_section, {})
    config_section["sqlalchemy.url"] = _resolve_db_url()

    connectable = engine_from_config(
        config_section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        # Ensure the schema exists BEFORE Alembic tries to create its
        # version table inside it.  Without this, the very first
        # `alembic upgrade head` fails because version_table_schema
        # points at a non-existent schema.
        from sqlalchemy import text as _sa_text  # local import keeps
        # the module's top-level imports minimal for offline mode.
        connection.execute(
            _sa_text(f"CREATE SCHEMA IF NOT EXISTS {COPILOT_STATE_SCHEMA}")
        )
        connection.commit()

        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table="alembic_version",
            version_table_schema=COPILOT_STATE_SCHEMA,
            include_schemas=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
