"""Store the immutable device limit on each sellable plan.

Revision ID: 0006_plan_device_limits
Revises: 0005_provisioning_region_lock
Create Date: 2026-08-10
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0006_plan_device_limits"
down_revision: Union[str, Sequence[str], None] = "0005_provisioning_region_lock"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("plans") as batch_op:
        batch_op.add_column(
            sa.Column("devices_limit", sa.Integer(), nullable=False, server_default="5")
        )


def downgrade() -> None:
    with op.batch_alter_table("plans") as batch_op:
        batch_op.drop_column("devices_limit")
