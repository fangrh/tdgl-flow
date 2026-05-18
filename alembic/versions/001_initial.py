"""Initial schema: viewer_sessions + missing runs columns

Revision ID: 001
Revises: None
Create Date: 2026-05-18
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "viewer_sessions",
        sa.Column("session_id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(64), nullable=False, index=True),
        sa.Column("viewer_type", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="PENDING"),
        sa.Column("pod_name", sa.String(128)),
        sa.Column("service_name", sa.String(128)),
        sa.Column("session_url", sa.String(512)),
        sa.Column("active_clients", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_accessed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("error_message", sa.String(1024)),
    )

    op.execute("ALTER TABLE runs ADD COLUMN IF NOT EXISTS mesh_sites JSONB")
    op.execute("ALTER TABLE runs ADD COLUMN IF NOT EXISTS mesh_elements JSONB")
    op.execute("ALTER TABLE runs ADD COLUMN IF NOT EXISTS n_sites INTEGER")
    op.execute("ALTER TABLE runs ADD COLUMN IF NOT EXISTS solver_options JSONB")


def downgrade() -> None:
    op.drop_table("viewer_sessions")
    op.execute("ALTER TABLE runs DROP COLUMN IF EXISTS solver_options")
    op.execute("ALTER TABLE runs DROP COLUMN IF EXISTS n_sites")
    op.execute("ALTER TABLE runs DROP COLUMN IF EXISTS mesh_elements")
    op.execute("ALTER TABLE runs DROP COLUMN IF EXISTS mesh_sites")
