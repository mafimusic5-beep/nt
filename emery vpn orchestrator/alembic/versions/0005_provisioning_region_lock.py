"""Prevent duplicate automatic purchases per region.

Revision ID: 0005_provisioning_region_lock
Revises: 0004_comfort_capacity_defaults
Create Date: 2026-08-10
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0005_provisioning_region_lock"
down_revision: Union[str, Sequence[str], None] = "0004_comfort_capacity_defaults"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "vpn_nodes",
        sa.Column("provisioning_lock_key", sa.String(length=32), nullable=True),
    )
    op.create_index(
        "uq_vpn_nodes_provisioning_lock_key",
        "vpn_nodes",
        ["provisioning_lock_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_vpn_nodes_provisioning_lock_key", table_name="vpn_nodes")
    op.drop_column("vpn_nodes", "provisioning_lock_key")
