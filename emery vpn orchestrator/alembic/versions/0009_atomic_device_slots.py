"""Enforce tariff device limits with immutable database slots.

Revision ID: 0009_atomic_device_slots
Revises: 0008_pool_assignments_and_renewal
Create Date: 2026-08-13
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0009_atomic_device_slots"
down_revision: Union[str, Sequence[str], None] = "0008_pool_assignments_and_renewal"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("devices") as batch_op:
        batch_op.add_column(sa.Column("slot_index", sa.Integer(), nullable=True))

    # Preserve every currently active registration. Slots are deterministic so
    # the migration is repeatable and existing devices keep their precedence.
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            """
            SELECT id, subscription_id
            FROM devices
            WHERE is_active = 1
            ORDER BY subscription_id, created_at, id
            """
        )
    ).fetchall()
    next_slot_by_subscription: dict[int, int] = {}
    for device_id, subscription_id in rows:
        slot_index = next_slot_by_subscription.get(int(subscription_id), 1)
        connection.execute(
            sa.text("UPDATE devices SET slot_index = :slot WHERE id = :device_id"),
            {"slot": slot_index, "device_id": int(device_id)},
        )
        next_slot_by_subscription[int(subscription_id)] = slot_index + 1

    with op.batch_alter_table("devices") as batch_op:
        batch_op.create_unique_constraint(
            "uq_subscription_device_slot",
            ["subscription_id", "slot_index"],
        )


def downgrade() -> None:
    with op.batch_alter_table("devices") as batch_op:
        batch_op.drop_constraint("uq_subscription_device_slot", type_="unique")
        batch_op.drop_column("slot_index")
