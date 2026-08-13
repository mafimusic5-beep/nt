"""Set conservative public-pool capacity defaults.

Revision ID: 0004_comfort_capacity_defaults
Revises: 0003_node_ssh_keys
Create Date: 2026-08-10
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0004_comfort_capacity_defaults"
down_revision: Union[str, Sequence[str], None] = "0003_node_ssh_keys"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Preserve already over-capacity nodes so a migration never disconnects
    # existing users. They can be drained and corrected by an operator later.
    op.execute(
        "UPDATE vpn_nodes SET capacity_clients = 20 "
        "WHERE capacity_clients = 100 AND current_clients <= 20"
    )
    op.execute(
        "UPDATE vpn_nodes SET bandwidth_limit_mbps = 600 "
        "WHERE bandwidth_limit_mbps = 1000"
    )
    op.execute(
        "UPDATE vpn_nodes SET per_device_speed_limit_mbps = 30 "
        "WHERE per_device_speed_limit_mbps = 100"
    )
    with op.batch_alter_table("vpn_nodes") as batch_op:
        batch_op.alter_column(
            "capacity_clients",
            existing_type=sa.Integer(),
            server_default="20",
        )
        batch_op.alter_column(
            "bandwidth_limit_mbps",
            existing_type=sa.Integer(),
            server_default="600",
        )
        batch_op.alter_column(
            "per_device_speed_limit_mbps",
            existing_type=sa.Integer(),
            server_default="30",
        )


def downgrade() -> None:
    with op.batch_alter_table("vpn_nodes") as batch_op:
        batch_op.alter_column(
            "capacity_clients",
            existing_type=sa.Integer(),
            server_default="100",
        )
        batch_op.alter_column(
            "bandwidth_limit_mbps",
            existing_type=sa.Integer(),
            server_default="1000",
        )
        batch_op.alter_column(
            "per_device_speed_limit_mbps",
            existing_type=sa.Integer(),
            server_default="100",
        )
