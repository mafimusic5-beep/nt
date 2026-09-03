"""Journal IONOS purchases before calling the provider.

Revision ID: 0011_ionos_cloud_jobs
Revises: 0010_device_bound_vless_gate
"""
from alembic import op
import sqlalchemy as sa

revision = "0011_ionos_cloud_jobs"
down_revision = "0010_device_bound_vless_gate"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ionos_provision_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("node_id", sa.Integer(), sa.ForeignKey("vpn_nodes.id"), nullable=False, unique=True),
        sa.Column("phase", sa.String(32), nullable=False, server_default="created"),
        sa.Column("resource_name", sa.String(100), nullable=False, unique=True),
        sa.Column("config_json", sa.Text(), nullable=False),
        sa.Column("posted_operations", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("datacenter_id", sa.String(36), nullable=False, server_default=""),
        sa.Column("lan_id", sa.String(16), nullable=False, server_default=""),
        sa.Column("server_id", sa.String(36), nullable=False, server_default=""),
        sa.Column("bootstrap_host_private_key", sa.Text(), nullable=False, server_default=""),
        sa.Column("bootstrap_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(128), nullable=False, server_default=""),
        sa.Column("lease_key", sa.String(32), nullable=True, unique=True),
        sa.Column("lease_token", sa.String(36), nullable=False, server_default=""),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    # Losing the purchase journal could order duplicate paid resources after a
    # rollback. Require an operator to reconcile it instead of silently dropping it.
    if op.get_bind().execute(sa.text("SELECT COUNT(*) FROM ionos_provision_jobs")).scalar():
        raise RuntimeError("Reconcile and archive IONOS jobs before downgrading; no servers were deleted.")
    op.drop_table("ionos_provision_jobs")
