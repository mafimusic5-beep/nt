"""Add node orchestration fields.

Revision ID: 0002_node_orchestration_fields
Revises: 0001_initial_schema
Create Date: 2026-03-23
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0002_node_orchestration_fields"
down_revision: Union[str, Sequence[str], None] = "0001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("vpn_nodes", sa.Column("health_status", sa.String(length=32), nullable=False, server_default="unknown"))
    op.add_column("vpn_nodes", sa.Column("load_score", sa.Integer(), nullable=False, server_default="1000"))
    op.add_column("vpn_nodes", sa.Column("priority", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("vpn_nodes", sa.Column("capacity_clients", sa.Integer(), nullable=False, server_default="100"))
    op.add_column("vpn_nodes", sa.Column("bandwidth_limit_mbps", sa.Integer(), nullable=False, server_default="1000"))
    op.add_column("vpn_nodes", sa.Column("current_clients", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("vpn_nodes", sa.Column("per_device_speed_limit_mbps", sa.Integer(), nullable=False, server_default="100"))
    op.add_column("vpn_nodes", sa.Column("firstvds_vps_id", sa.String(length=64), nullable=False, server_default=""))
    op.add_column("devices", sa.Column("node_id", sa.Integer(), nullable=True))

    op.create_index("ix_vpn_nodes_health_status", "vpn_nodes", ["health_status"], unique=False)
    op.create_index("ix_vpn_nodes_load_score", "vpn_nodes", ["load_score"], unique=False)
    op.create_index("ix_vpn_nodes_priority", "vpn_nodes", ["priority"], unique=False)
    op.create_index("ix_devices_node_id", "devices", ["node_id"], unique=False)
    op.create_foreign_key("fk_devices_node_id", "devices", "vpn_nodes", ["node_id"], ["id"])


def downgrade() -> None:
    op.drop_constraint("fk_devices_node_id", "devices", type_="foreignkey")
    op.drop_index("ix_devices_node_id", table_name="devices")
    op.drop_index("ix_vpn_nodes_priority", table_name="vpn_nodes")
    op.drop_index("ix_vpn_nodes_load_score", table_name="vpn_nodes")
    op.drop_index("ix_vpn_nodes_health_status", table_name="vpn_nodes")

    op.drop_column("devices", "node_id")
    op.drop_column("vpn_nodes", "firstvds_vps_id")
    op.drop_column("vpn_nodes", "per_device_speed_limit_mbps")
    op.drop_column("vpn_nodes", "current_clients")
    op.drop_column("vpn_nodes", "bandwidth_limit_mbps")
    op.drop_column("vpn_nodes", "capacity_clients")
    op.drop_column("vpn_nodes", "priority")
    op.drop_column("vpn_nodes", "load_score")
    op.drop_column("vpn_nodes", "health_status")
