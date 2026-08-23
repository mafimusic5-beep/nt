"""Add per-node proof-of-possession gateway endpoints.

Revision ID: 0010_device_bound_vless_gate
Revises: 0009_atomic_device_slots
Create Date: 2026-08-23
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0010_device_bound_vless_gate"
down_revision: Union[str, Sequence[str], None] = "0009_atomic_device_slots"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("vpn_nodes") as batch_op:
        batch_op.add_column(sa.Column("device_gate_host", sa.String(length=255), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("device_gate_port", sa.Integer(), nullable=False, server_default="24443"))
        batch_op.add_column(sa.Column("device_gate_server_name", sa.String(length=255), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("device_gate_spki_sha256", sa.String(length=64), nullable=False, server_default=""))
    with op.batch_alter_table("vpn_assignments") as batch_op:
        # Existing assignments must be reinstalled before they are trusted;
        # their historical dedicated listeners may still be public.
        batch_op.add_column(
            sa.Column(
                "device_gate_enforced",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("vpn_assignments") as batch_op:
        batch_op.drop_column("device_gate_enforced")
    with op.batch_alter_table("vpn_nodes") as batch_op:
        batch_op.drop_column("device_gate_spki_sha256")
        batch_op.drop_column("device_gate_server_name")
        batch_op.drop_column("device_gate_port")
        batch_op.drop_column("device_gate_host")
