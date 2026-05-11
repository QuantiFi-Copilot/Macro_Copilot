"""initial copilot_state schema (sessions, turns, artifacts, dags, workspaces)

Revision ID: 0001_initial_copilot_state
Revises:
Create Date: 2026-05-12

Creates the 12 tables that make up the Phase 0 state foundation, under
the `copilot_state` Postgres schema (separate from `macro_data` which
holds market data).

Topology of the 12 tables (dependency order for CREATE):

  application_version   (independent — pins git commit + deploy time)
  methodology_versions  (independent — pins each tool's YAML content)
  artifact_metadata     (independent at FK level; .lineage references
                         hashes textually, .methodology_version_ids is
                         a plain int[] not a FK array)
  dags                  (independent — content-addressed DAG topology)
  dag_nodes             (FK -> dags.hash, FK? -> artifact_metadata.hash)
  dag_edges             (FK -> dags.hash)
  sessions              (independent — one row per conversation thread)
  turns                 (FK -> sessions.id, FK? -> dags.hash via dag_id)
  message_events        (FK -> turns.id — append-only streamed events)
  workspaces            (FK -> dags.hash, self-FK -> workspaces.id)
  workspace_variants    (FK -> workspaces.id, FK -> dags.hash)
  working_set           (FK -> sessions.id, FK -> artifact_metadata.hash,
                         FK -> turns.id × 2)

DROP order is the exact reverse.

See docs/architecture/state_schema.md for the design rationale.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "0001_initial_copilot_state"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCHEMA = "copilot_state"


# ============================================================================
# CONSTANTS
# ============================================================================
# SHA-256 hex digest length.  Used for artifact_metadata.hash, dags.hash,
# methodology_versions.yaml_content_hash, dag_nodes.artifact_hash, and
# (working_set / workspace_variants) hash columns.
HASH_LEN = 64

# Git commit SHA-1 hex digest length.  Used for application_version.git_commit.
GIT_SHA_LEN = 40


# ============================================================================
# UPGRADE
# ============================================================================
def upgrade() -> None:
    # ------------------------------------------------------------------
    # SCHEMA NAMESPACE
    # ------------------------------------------------------------------
    # env.py already creates this schema before context.run_migrations
    # so the alembic_version table can be created inside it.  We
    # repeat the CREATE here so offline-mode SQL emit is self-contained:
    # piping `alembic upgrade head --sql` to a fresh DB will produce a
    # full bootstrap script.
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")

    # ------------------------------------------------------------------
    # 1. application_version
    # ------------------------------------------------------------------
    # Pins which code revision was active when an artifact was produced.
    # Phase 0 PR 4 surfaces this so a future "replay byte-identical at
    # six months" check can detect cases where lineage hashes match but
    # the producing code has changed (a meaningful divergence signal).
    op.create_table(
        "application_version",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("git_commit", sa.String(length=GIT_SHA_LEN), nullable=False),
        sa.Column(
            "deployed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.CheckConstraint(
            f"length(git_commit) = {GIT_SHA_LEN}",
            name="ck_application_version_git_commit_len",
        ),
        sa.UniqueConstraint(
            "git_commit", name="uq_application_version_git_commit"
        ),
        schema=SCHEMA,
    )
    # ``BigInteger`` + ``primary_key=True`` already triggers
    # ``autoincrement=True`` in SQLAlchemy → emitted as ``BIGSERIAL`` in
    # the PostgreSQL dialect, which is what we want.  No manual
    # identity-attachment ALTER needed.

    # ------------------------------------------------------------------
    # 2. methodology_versions
    # ------------------------------------------------------------------
    # One row per distinct YAML content snapshot.  Dedup'd by
    # yaml_content_hash so re-loading the same YAML across deploys
    # does not bloat the table.  Used by artifact_metadata to pin
    # which methodology version each artifact used; this is what
    # makes "open the workspace under its original methodology
    # six months later" work — the YAML body is captured here, not
    # just its path.
    op.create_table(
        "methodology_versions",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "yaml_path",
            sa.Text(),
            nullable=False,
            comment=(
                "Path the YAML was loaded from at recording time. "
                "Metadata-only; NOT in the dedup key."
            ),
        ),
        sa.Column(
            "yaml_content_hash",
            sa.String(length=HASH_LEN),
            nullable=False,
            comment="SHA-256 hex digest of the canonical YAML content.",
        ),
        sa.Column(
            "yaml_content",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment=(
                "Parsed YAML as JSONB so replay-under-original-methodology "
                "can reconstruct the ToolConfig without filesystem access."
            ),
        ),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            f"length(yaml_content_hash) = {HASH_LEN}",
            name="ck_methodology_versions_hash_len",
        ),
        sa.UniqueConstraint(
            "yaml_content_hash", name="uq_methodology_versions_content_hash"
        ),
        schema=SCHEMA,
    )

    # ------------------------------------------------------------------
    # 3. artifact_metadata
    # ------------------------------------------------------------------
    # Content-addressed by hash.  Payload lives EITHER inline (small
    # artifacts, JSONB column) OR in object storage (large artifacts,
    # payload_uri column) — never both.  The CHECK constraint pins
    # that invariant.
    op.create_table(
        "artifact_metadata",
        sa.Column(
            "hash",
            sa.String(length=HASH_LEN),
            primary_key=True,
            comment="SHA-256 hex digest, content-addressed.",
        ),
        sa.Column(
            "artifact_type",
            sa.String(length=64),
            nullable=False,
            comment=(
                "One of the closed-family artifact types: Series, EventSet, "
                "Panel, SeriesSet, WindowedPanel, ScalarMetric, "
                "RankedResult, TradeSet (Phase 1).  NOT enforced via CHECK "
                "at the DB layer — the closed family lives in "
                "shared.artifacts.types and is enforced by Pydantic."
            ),
        ),
        sa.Column(
            "units",
            sa.String(length=32),
            nullable=True,
            comment="e.g. BPS, PERCENT, RATIO; null if not unit-bearing.",
        ),
        sa.Column(
            "frequency",
            sa.String(length=32),
            nullable=True,
            comment="e.g. DAILY, WEEKLY, MONTHLY; null if not time-indexed.",
        ),
        sa.Column(
            "row_count",
            sa.Integer(),
            nullable=True,
            comment="Row count for tabular payloads; null if not tabular.",
        ),
        sa.Column(
            "byte_size",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
            comment=(
                "Serialized payload size in bytes.  Used by the "
                "inline-vs-object-storage decision and by GC accounting."
            ),
        ),
        sa.Column(
            "payload_uri",
            sa.Text(),
            nullable=True,
            comment=(
                "Object-storage URI (e.g. s3://bucket/hash.parquet) for "
                "large artifacts.  Mutually exclusive with inline_payload."
            ),
        ),
        sa.Column(
            "inline_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment=(
                "Inline JSONB payload for small artifacts (default cap: "
                "100 rows or 8KB).  Mutually exclusive with payload_uri."
            ),
        ),
        sa.Column(
            "lineage",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment=(
                "The Lineage chain (closed-family LineageStep list) "
                "serialized as JSON.  Always non-null because every "
                "artifact has at least one step."
            ),
        ),
        sa.Column(
            "methodology_version_ids",
            postgresql.ARRAY(sa.BigInteger()),
            nullable=True,
            comment=(
                "Array of methodology_versions.id values that "
                "contributed to this artifact.  Plain int[] (not FK array) "
                "because Postgres does not natively enforce FK on array "
                "elements; integrity is application-level."
            ),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            f"length(hash) = {HASH_LEN}", name="ck_artifact_metadata_hash_len"
        ),
        sa.CheckConstraint(
            "(payload_uri IS NULL) <> (inline_payload IS NULL)",
            name="ck_artifact_metadata_payload_exactly_one",
        ),
        sa.CheckConstraint(
            "byte_size >= 0", name="ck_artifact_metadata_byte_size_nonneg"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_artifact_metadata_artifact_type",
        "artifact_metadata",
        ["artifact_type"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_artifact_metadata_created_at",
        "artifact_metadata",
        ["created_at"],
        schema=SCHEMA,
    )

    # ------------------------------------------------------------------
    # 4. dags
    # ------------------------------------------------------------------
    op.create_table(
        "dags",
        sa.Column(
            "hash",
            sa.String(length=HASH_LEN),
            primary_key=True,
            comment="SHA-256 hex digest of the DAG topology + node params.",
        ),
        sa.Column(
            "topology",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment=(
                "Full DAG shape as JSON (nodes + edges + slot bindings). "
                "The structured dag_nodes / dag_edges tables below are "
                "the canonical normalized form; this column carries the "
                "full JSON for fast round-trip without N joins."
            ),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            f"length(hash) = {HASH_LEN}", name="ck_dags_hash_len"
        ),
        schema=SCHEMA,
    )

    # ------------------------------------------------------------------
    # 5. dag_nodes  (FK -> dags.hash)
    # ------------------------------------------------------------------
    op.create_table(
        "dag_nodes",
        sa.Column(
            "dag_hash",
            sa.String(length=HASH_LEN),
            sa.ForeignKey(
                f"{SCHEMA}.dags.hash",
                ondelete="CASCADE",
                name="fk_dag_nodes_dag",
            ),
            nullable=False,
        ),
        sa.Column(
            "node_id",
            sa.Text(),
            nullable=False,
            comment="Caller-assigned node identifier (unique within a DAG).",
        ),
        sa.Column(
            "kind",
            sa.String(length=32),
            nullable=False,
            comment="primitive | operator (closed family at app layer).",
        ),
        sa.Column(
            "name",
            sa.String(length=128),
            nullable=False,
            comment=(
                "Tool / operator name "
                "(e.g. calculate_ois_curve_spread_tool, align_series)."
            ),
        ),
        sa.Column(
            "params",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "artifact_hash",
            sa.String(length=HASH_LEN),
            sa.ForeignKey(
                f"{SCHEMA}.artifact_metadata.hash",
                ondelete="RESTRICT",
                name="fk_dag_nodes_artifact",
            ),
            nullable=True,
            comment=(
                "Hash of the artifact this node produced, if executed. "
                "Null for unexecuted nodes in a workspace plan."
            ),
        ),
        sa.PrimaryKeyConstraint("dag_hash", "node_id", name="pk_dag_nodes"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_dag_nodes_artifact_hash",
        "dag_nodes",
        ["artifact_hash"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_dag_nodes_kind_name", "dag_nodes", ["kind", "name"], schema=SCHEMA
    )

    # ------------------------------------------------------------------
    # 6. dag_edges  (FK -> dags.hash)
    # ------------------------------------------------------------------
    op.create_table(
        "dag_edges",
        sa.Column(
            "dag_hash",
            sa.String(length=HASH_LEN),
            sa.ForeignKey(
                f"{SCHEMA}.dags.hash",
                ondelete="CASCADE",
                name="fk_dag_edges_dag",
            ),
            nullable=False,
        ),
        sa.Column("from_node", sa.Text(), nullable=False),
        sa.Column("to_node", sa.Text(), nullable=False),
        sa.Column(
            "slot_name",
            sa.String(length=64),
            nullable=False,
            comment=(
                "Which input slot on `to_node` this edge populates "
                "(e.g. 'signal', 'target', 'lhs', 'rhs')."
            ),
        ),
        sa.PrimaryKeyConstraint(
            "dag_hash", "from_node", "to_node", "slot_name", name="pk_dag_edges"
        ),
        schema=SCHEMA,
    )

    # ------------------------------------------------------------------
    # 7. sessions
    # ------------------------------------------------------------------
    op.create_table(
        "sessions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            comment="Session UUID — stable URL handle for a conversation.",
        ),
        sa.Column(
            "user_id",
            sa.String(length=128),
            nullable=True,
            comment=(
                "Application-defined user identifier.  Nullable in V0 "
                "(single-org assumption); becomes required when "
                "multi-tenancy lands."
            ),
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "archived_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment=(
                "Set when the session is archived (soft-delete). "
                "Active sessions have archived_at IS NULL."
            ),
        ),
        sa.Column(
            "label",
            sa.Text(),
            nullable=True,
            comment="User-given session label for display.",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_sessions_user_active",
        "sessions",
        ["user_id"],
        postgresql_where=sa.text("archived_at IS NULL"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_sessions_started_at",
        "sessions",
        ["started_at"],
        schema=SCHEMA,
    )

    # ------------------------------------------------------------------
    # 8. turns  (FK -> sessions.id, FK? -> dags.hash)
    # ------------------------------------------------------------------
    op.create_table(
        "turns",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                f"{SCHEMA}.sessions.id",
                ondelete="CASCADE",
                name="fk_turns_session",
            ),
            nullable=False,
        ),
        sa.Column(
            "sequence_no",
            sa.Integer(),
            nullable=False,
            comment="Monotonic per-session turn ordering, starting at 1.",
        ),
        sa.Column(
            "user_message",
            sa.Text(),
            nullable=False,
        ),
        sa.Column(
            "assistant_response",
            sa.Text(),
            nullable=True,
            comment="Null while the turn is still in flight.",
        ),
        sa.Column(
            "dag_id",
            sa.String(length=HASH_LEN),
            sa.ForeignKey(
                f"{SCHEMA}.dags.hash",
                ondelete="SET NULL",
                name="fk_turns_dag",
            ),
            nullable=True,
            comment="The DAG produced for this turn, if any.",
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="running",
            comment="Turn lifecycle: running | completed | failed | cancelled.",
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.CheckConstraint(
            "status IN ('running','completed','failed','cancelled')",
            name="ck_turns_status",
        ),
        sa.UniqueConstraint(
            "session_id", "sequence_no", name="uq_turns_session_sequence"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_turns_session_status",
        "turns",
        ["session_id", "status"],
        schema=SCHEMA,
    )

    # ------------------------------------------------------------------
    # 9. message_events  (FK -> turns.id)
    # ------------------------------------------------------------------
    # Append-only stream record of every event the orchestrator emitted
    # over the WebSocket during a turn.  Used for replay, debugging,
    # and auditability of the LLM interaction.
    op.create_table(
        "message_events",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "turn_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                f"{SCHEMA}.turns.id",
                ondelete="CASCADE",
                name="fk_message_events_turn",
            ),
            nullable=False,
        ),
        sa.Column(
            "sequence_no",
            sa.Integer(),
            nullable=False,
            comment="Monotonic per-turn event ordering, starting at 1.",
        ),
        sa.Column(
            "event_type",
            sa.String(length=64),
            nullable=False,
            comment=(
                "Free-form to admit forward evolution; the orchestrator's "
                "current 13-value enum is documented in "
                "orchestrator/events.py.  A future PR may pin a CHECK "
                "once the vocabulary stabilizes."
            ),
        ),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "emitted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "turn_id",
            "sequence_no",
            name="uq_message_events_turn_sequence",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_message_events_event_type",
        "message_events",
        ["event_type"],
        schema=SCHEMA,
    )

    # ------------------------------------------------------------------
    # 10. workspaces  (FK -> dags.hash, self-FK)
    # ------------------------------------------------------------------
    op.create_table(
        "workspaces",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "name",
            sa.Text(),
            nullable=True,
            comment=(
                "User-given workspace label (e.g. 'tips_2y_v3').  Nullable "
                "for auto-named system workspaces."
            ),
        ),
        sa.Column(
            "dag_hash",
            sa.String(length=HASH_LEN),
            sa.ForeignKey(
                f"{SCHEMA}.dags.hash",
                ondelete="RESTRICT",
                name="fk_workspaces_dag",
            ),
            nullable=False,
            comment=(
                "The primary DAG this workspace renders.  Variants live "
                "in workspace_variants."
            ),
        ),
        sa.Column(
            "focus_node",
            sa.Text(),
            nullable=True,
            comment="UI focus pointer; not load-bearing for replay.",
        ),
        sa.Column(
            "parent_workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                f"{SCHEMA}.workspaces.id",
                ondelete="SET NULL",
                name="fk_workspaces_parent",
            ),
            nullable=True,
            comment=(
                "For forked workspaces — points at the workspace this one "
                "was branched from.  SET NULL on parent delete to "
                "preserve fork history."
            ),
        ),
        sa.Column(
            "schema_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
            comment=(
                "Workspace data-format version.  Distinct from the "
                "alembic_version table (which tracks DB schema).  Readers "
                "refuse versions ahead of their code; rolling forward is "
                "a data migration, not a DDL migration."
            ),
        ),
        sa.Column(
            "created_by",
            sa.String(length=128),
            nullable=True,
            comment="Application-defined user identifier.",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
            comment="Set by app code on UPDATE; no DB-level trigger.",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_workspaces_name",
        "workspaces",
        ["name"],
        postgresql_where=sa.text("name IS NOT NULL"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_workspaces_parent",
        "workspaces",
        ["parent_workspace_id"],
        postgresql_where=sa.text("parent_workspace_id IS NOT NULL"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_workspaces_created_by",
        "workspaces",
        ["created_by"],
        postgresql_where=sa.text("created_by IS NOT NULL"),
        schema=SCHEMA,
    )

    # ------------------------------------------------------------------
    # 11. workspace_variants  (FK -> workspaces.id, FK -> dags.hash)
    # ------------------------------------------------------------------
    op.create_table(
        "workspace_variants",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                f"{SCHEMA}.workspaces.id",
                ondelete="CASCADE",
                name="fk_workspace_variants_workspace",
            ),
            nullable=False,
        ),
        sa.Column(
            "variant_dag_hash",
            sa.String(length=HASH_LEN),
            sa.ForeignKey(
                f"{SCHEMA}.dags.hash",
                ondelete="RESTRICT",
                name="fk_workspace_variants_dag",
            ),
            nullable=False,
        ),
        sa.Column(
            "override_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment=(
                "What changed between the parent workspace's DAG and this "
                "variant's DAG.  Shape is application-defined and may "
                "evolve; not pinned by a CHECK constraint in V0."
            ),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_workspace_variants_workspace",
        "workspace_variants",
        ["workspace_id"],
        schema=SCHEMA,
    )

    # ------------------------------------------------------------------
    # 12. working_set  (FK -> sessions, FK -> artifact_metadata, FK x2 -> turns)
    # ------------------------------------------------------------------
    # Per-session map of {name -> artifact_hash}.  Append-mostly: when a
    # user re-binds a name, the prior row is RETIRED (retired_at_turn
    # set), not deleted, so prior turns that resolved the name still
    # resolve to the historical hash.
    op.create_table(
        "working_set",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                f"{SCHEMA}.sessions.id",
                ondelete="CASCADE",
                name="fk_working_set_session",
            ),
            nullable=False,
        ),
        sa.Column(
            "name",
            sa.Text(),
            nullable=False,
            comment=(
                "User-facing or auto-generated name (e.g. 'tips_2y_v1', "
                "'turn_3_result')."
            ),
        ),
        sa.Column(
            "artifact_hash",
            sa.String(length=HASH_LEN),
            sa.ForeignKey(
                f"{SCHEMA}.artifact_metadata.hash",
                ondelete="RESTRICT",
                name="fk_working_set_artifact",
            ),
            nullable=False,
        ),
        sa.Column(
            "introduced_at_turn",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                f"{SCHEMA}.turns.id",
                ondelete="RESTRICT",
                name="fk_working_set_introduced_at_turn",
            ),
            nullable=False,
        ),
        sa.Column(
            "retired_at_turn",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                f"{SCHEMA}.turns.id",
                ondelete="RESTRICT",
                name="fk_working_set_retired_at_turn",
            ),
            nullable=True,
            comment=(
                "Set when the name is re-bound to a different artifact. "
                "Active entries have retired_at_turn IS NULL."
            ),
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_working_set_session",
        "working_set",
        ["session_id"],
        schema=SCHEMA,
    )
    # Partial unique index: at most one ACTIVE row per (session_id, name).
    # Retired rows do not participate so historical re-bindings are
    # allowed.  This is the integrity constraint that makes name
    # resolution unambiguous in a live session.
    op.create_index(
        "uq_working_set_session_name_active",
        "working_set",
        ["session_id", "name"],
        unique=True,
        postgresql_where=sa.text("retired_at_turn IS NULL"),
        schema=SCHEMA,
    )


# ============================================================================
# DOWNGRADE
# ============================================================================
def downgrade() -> None:
    """Drop everything created by upgrade(), in reverse FK order.

    The schema itself is dropped LAST, with CASCADE, to clean up the
    alembic_version table that env.py keeps inside `copilot_state` and
    any objects an extension may have left behind.  After this runs,
    `copilot_state` is gone entirely.
    """
    # Reverse FK order — children before parents.
    op.drop_index(
        "uq_working_set_session_name_active",
        table_name="working_set",
        schema=SCHEMA,
    )
    op.drop_index(
        "ix_working_set_session", table_name="working_set", schema=SCHEMA
    )
    op.drop_table("working_set", schema=SCHEMA)

    op.drop_index(
        "ix_workspace_variants_workspace",
        table_name="workspace_variants",
        schema=SCHEMA,
    )
    op.drop_table("workspace_variants", schema=SCHEMA)

    op.drop_index(
        "ix_workspaces_created_by", table_name="workspaces", schema=SCHEMA
    )
    op.drop_index("ix_workspaces_parent", table_name="workspaces", schema=SCHEMA)
    op.drop_index("ix_workspaces_name", table_name="workspaces", schema=SCHEMA)
    op.drop_table("workspaces", schema=SCHEMA)

    op.drop_index(
        "ix_message_events_event_type",
        table_name="message_events",
        schema=SCHEMA,
    )
    op.drop_table("message_events", schema=SCHEMA)

    op.drop_index("ix_turns_session_status", table_name="turns", schema=SCHEMA)
    op.drop_table("turns", schema=SCHEMA)

    op.drop_index("ix_sessions_started_at", table_name="sessions", schema=SCHEMA)
    op.drop_index("ix_sessions_user_active", table_name="sessions", schema=SCHEMA)
    op.drop_table("sessions", schema=SCHEMA)

    op.drop_table("dag_edges", schema=SCHEMA)

    op.drop_index("ix_dag_nodes_kind_name", table_name="dag_nodes", schema=SCHEMA)
    op.drop_index(
        "ix_dag_nodes_artifact_hash", table_name="dag_nodes", schema=SCHEMA
    )
    op.drop_table("dag_nodes", schema=SCHEMA)

    op.drop_table("dags", schema=SCHEMA)

    op.drop_index(
        "ix_artifact_metadata_created_at",
        table_name="artifact_metadata",
        schema=SCHEMA,
    )
    op.drop_index(
        "ix_artifact_metadata_artifact_type",
        table_name="artifact_metadata",
        schema=SCHEMA,
    )
    op.drop_table("artifact_metadata", schema=SCHEMA)

    op.drop_table("methodology_versions", schema=SCHEMA)

    op.drop_table("application_version", schema=SCHEMA)

    # NOTE: we do NOT drop the `copilot_state` schema itself.  Two
    # reasons:
    #   1. Alembic's `alembic_version` table lives inside this schema
    #      (set via `version_table_schema=COPILOT_STATE_SCHEMA` in
    #      env.py).  Dropping the schema here would delete that table,
    #      and Alembic's post-downgrade UPDATE on it would then fail.
    #   2. The schema namespace itself was created by env.py, not by a
    #      migration.  Downgrades should undo what THEIR migration did,
    #      not what env.py did.
    #
    # After this downgrade runs the schema sits empty except for
    # alembic_version, which is the correct end state for "downgrade
    # to base".  To fully tear down the schema namespace (e.g. to
    # reset a dev DB), drop it manually:
    #
    #   DROP SCHEMA copilot_state CASCADE;
