"""Cap persisted VPN node capacity at 15 active clients.

Revision ID: 0015_cap_node_capacity_15
Revises: 0014_cap_pool_speed_50
Create Date: 2026-09-16
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0015_cap_node_capacity_15"
down_revision: Union[str, Sequence[str], None] = "0014_cap_pool_speed_50"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Do not disconnect existing assignments during migration. Lowering the
    # scheduling capacity immediately prevents any new admission above 15;
    # an already over-capacity node drains naturally as assignments are
    # released/reconciled.
    op.execute(
        sa.text(
            "UPDATE vpn_nodes "
            "SET capacity_clients = 15 "
            "WHERE capacity_clients > 15"
        )
    )


def downgrade() -> None:
    # Previous operator-specific capacities cannot be reconstructed safely.
    pass
