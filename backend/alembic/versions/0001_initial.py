"""initial schema

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

ACTIVE = sa.text("status IN ('queued', 'running')")


def upgrade() -> None:
    timestamp = sa.DateTime(timezone=True)
    op.create_table(
        "environments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("owner", sa.String(128), nullable=False),
        sa.Column("template", sa.String(64), nullable=False),
        sa.Column("module_version", sa.String(16), nullable=False),
        sa.Column("size", sa.String(16), nullable=False),
        sa.Column("ttl_hours", sa.Integer, nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("hourly_cost_usd", sa.Numeric(10, 4)),
        sa.Column("outputs", sa.JSON, nullable=False),
        sa.Column("policy_violations", sa.JSON, nullable=False),
        sa.Column("error", sa.Text),
        sa.Column("lock_info", sa.JSON),
        sa.Column("idempotency_key", sa.String(128)),
        sa.Column("created_at", timestamp, nullable=False),
        sa.Column("updated_at", timestamp, nullable=False),
        sa.Column("expires_at", timestamp, nullable=False),
        sa.Column("destroyed_at", timestamp),
        sa.UniqueConstraint(
            "owner", "idempotency_key", name="uq_environments_owner_idempotency_key"
        ),
    )
    op.create_index("ix_environments_owner", "environments", ["owner"])
    op.create_index("ix_environments_status", "environments", ["status"])
    op.create_index("ix_environments_expires_at", "environments", ["expires_at"])

    op.create_table(
        "operations",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "environment_id", sa.String(36), sa.ForeignKey("environments.id"), nullable=False
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("requested_by", sa.String(128), nullable=False),
        sa.Column("error_code", sa.String(32)),
        sa.Column("error", sa.Text),
        sa.Column("lock_id", sa.String(128)),
        sa.Column("created_at", timestamp, nullable=False),
        sa.Column("started_at", timestamp),
        sa.Column("heartbeat_at", timestamp),
        sa.Column("finished_at", timestamp),
    )
    op.create_index("ix_operations_environment_id", "operations", ["environment_id"])
    op.create_index("ix_operations_status", "operations", ["status"])
    op.create_index(
        "uq_operations_one_active_per_environment",
        "operations",
        ["environment_id"],
        unique=True,
        sqlite_where=ACTIVE,
        postgresql_where=ACTIVE,
    )

    op.create_table(
        "log_lines",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "environment_id", sa.String(36), sa.ForeignKey("environments.id"), nullable=False
        ),
        sa.Column("operation_id", sa.Integer, sa.ForeignKey("operations.id"), nullable=False),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column("created_at", timestamp, nullable=False),
        sa.UniqueConstraint("environment_id", "seq", name="uq_log_lines_environment_seq"),
    )


def downgrade() -> None:
    op.drop_table("log_lines")
    op.drop_table("operations")
    op.drop_table("environments")
