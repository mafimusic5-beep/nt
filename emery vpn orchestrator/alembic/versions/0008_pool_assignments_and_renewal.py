"""Add per-device VPN assignments and non-destructive renewal planning.

Revision ID: 0008_pool_assignments_and_renewal
Revises: 0007_node_recovery_state
Create Date: 2026-08-10
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0008_pool_assignments_and_renewal"
down_revision: Union[str, Sequence[str], None] = "0007_node_recovery_state"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("vpn_nodes") as batch_op:
        batch_op.add_column(
            sa.Column("contract_id", sa.String(length=128), nullable=False, server_default="")
        )
        batch_op.add_column(sa.Column("paid_until", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(
            sa.Column("renewal_price_eur_cents", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("auto_renew", sa.Boolean(), nullable=False, server_default=sa.true())
        )
        batch_op.add_column(
            sa.Column("renewal_status", sa.String(length=32), nullable=False, server_default="renew")
        )
        batch_op.add_column(
            sa.Column("do_not_renew_reason", sa.String(length=255), nullable=False, server_default="")
        )

    op.create_table(
        "vpn_assignments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("subject_type", sa.String(length=32), nullable=False),
        sa.Column("subject_key", sa.String(length=128), nullable=False),
        sa.Column("entitlement_hash", sa.String(length=128), nullable=False),
        sa.Column("entitlement_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("node_id", sa.Integer(), sa.ForeignKey("vpn_nodes.id"), nullable=False),
        sa.Column("client_uuid", sa.String(length=36), nullable=False),
        sa.Column("client_port", sa.Integer(), nullable=False),
        sa.Column("speed_limit_mbps", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="installing"),
        sa.Column("config_revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("confirmation_token_hash", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("prepare_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("installed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("subject_type", "subject_key", name="uq_vpn_assignment_subject"),
        sa.UniqueConstraint("node_id", "client_port", name="uq_vpn_assignment_node_port"),
        sa.UniqueConstraint("client_uuid", name="uq_vpn_assignments_client_uuid"),
    )
    op.create_index("ix_vpn_assignments_subject_type", "vpn_assignments", ["subject_type"])
    op.create_index("ix_vpn_assignments_node_id", "vpn_assignments", ["node_id"])
    op.create_index("ix_vpn_assignments_status", "vpn_assignments", ["status"])


def downgrade() -> None:
    op.drop_index("ix_vpn_assignments_status", table_name="vpn_assignments")
    op.drop_index("ix_vpn_assignments_node_id", table_name="vpn_assignments")
    op.drop_index("ix_vpn_assignments_subject_type", table_name="vpn_assignments")
    op.drop_table("vpn_assignments")

    with op.batch_alter_table("vpn_nodes") as batch_op:
        batch_op.drop_column("do_not_renew_reason")
        batch_op.drop_column("renewal_status")
        batch_op.drop_column("auto_renew")
        batch_op.drop_column("renewal_price_eur_cents")
        batch_op.drop_column("paid_until")
        batch_op.drop_column("contract_id")
