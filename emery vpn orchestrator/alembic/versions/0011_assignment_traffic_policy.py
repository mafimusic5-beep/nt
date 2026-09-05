"""Add current per-device traffic policy.

Revision ID: 0011_assignment_traffic_policy
Revises: 0010_device_bound_vless_gate
Create Date: 2026-09-05
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0011_assignment_traffic_policy"
down_revision: Union[str, Sequence[str], None] = "0010_device_bound_vless_gate"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("vpn_assignments") as batch_op:
        batch_op.add_column(
            sa.Column(
                "traffic_policy",
                sa.String(length=16),
                nullable=False,
                server_default="international",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("vpn_assignments") as batch_op:
        batch_op.drop_column("traffic_policy")
