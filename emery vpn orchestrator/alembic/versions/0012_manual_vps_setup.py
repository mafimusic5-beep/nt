"""Journal setup of manually purchased VPSs without any billing API.

Revision ID: 0012_manual_vps_setup
Revises: 0011_ionos_cloud_jobs
"""
from alembic import op
import sqlalchemy as sa

revision = "0012_manual_vps_setup"
down_revision = "0011_ionos_cloud_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "manual_vps_setup_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("node_id", sa.Integer(), sa.ForeignKey("vpn_nodes.id"), nullable=False, unique=True),
        sa.Column("endpoint", sa.String(64), nullable=False, unique=True),
        sa.Column("hostname", sa.String(200), nullable=False, unique=True),
        sa.Column("phase", sa.String(32), nullable=False, server_default="preflight"),
        sa.Column("config_json", sa.Text(), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("bootstrap_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(128), nullable=False, server_default=""),
        sa.Column("lease_token", sa.String(36), nullable=False, server_default=""),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    if op.get_bind().execute(sa.text("SELECT COUNT(*) FROM manual_vps_setup_jobs")).scalar():
        raise RuntimeError("Archive manual VPS jobs before downgrading; no VPS was removed.")
    op.drop_table("manual_vps_setup_jobs")
