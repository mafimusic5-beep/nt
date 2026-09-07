"""Cap persisted VPN node per-device speed at 50 Mbps.

Revision ID: 0014_cap_pool_speed_50
Revises: 0013_release_revoked_assignment_ports
Create Date: 2026-09-07
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0014_cap_pool_speed_50"
down_revision: Union[str, Sequence[str], None] = "0013_release_revoked_assignment_ports"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE vpn_nodes "
            "SET per_device_speed_limit_mbps = 50 "
            "WHERE per_device_speed_limit_mbps > 50"
        )
    )


def downgrade() -> None:
    # The previous values cannot be reconstructed safely.
    pass
