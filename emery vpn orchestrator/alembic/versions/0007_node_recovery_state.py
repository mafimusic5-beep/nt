"""Persist recovery-agent state and its per-node lease.

Revision ID: 0007_node_recovery_state
Revises: 0006_plan_device_limits
Create Date: 2026-08-10
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0007_node_recovery_state"
down_revision: Union[str, Sequence[str], None] = "0006_plan_device_limits"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("vpn_nodes") as batch_op:
        batch_op.add_column(
            sa.Column("provider_server_id", sa.String(length=128), nullable=False, server_default="")
        )
        batch_op.add_column(
            sa.Column("ssh_host_key", sa.Text(), nullable=False, server_default="")
        )
        batch_op.add_column(
            sa.Column("consecutive_health_failures", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("recovery_status", sa.String(length=32), nullable=False, server_default="idle")
        )
        batch_op.add_column(sa.Column("recovery_lock_until", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("last_healthy_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("last_recovery_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(
            sa.Column("last_recovery_action", sa.String(length=32), nullable=False, server_default="")
        )
        batch_op.add_column(
            sa.Column("last_recovery_error", sa.Text(), nullable=False, server_default="")
        )


def downgrade() -> None:
    with op.batch_alter_table("vpn_nodes") as batch_op:
        batch_op.drop_column("last_recovery_error")
        batch_op.drop_column("last_recovery_action")
        batch_op.drop_column("last_recovery_at")
        batch_op.drop_column("last_healthy_at")
        batch_op.drop_column("recovery_lock_until")
        batch_op.drop_column("recovery_status")
        batch_op.drop_column("consecutive_health_failures")
        batch_op.drop_column("ssh_host_key")
        batch_op.drop_column("provider_server_id")
