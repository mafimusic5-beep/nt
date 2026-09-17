"""Minimize persisted user activity metadata.

Revision ID: 0016_privacy_data_minimization
Revises: 0015_cap_node_capacity_15
Create Date: 2026-09-17
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0016_privacy_data_minimization"
down_revision: Union[str, Sequence[str], None] = "0015_cap_node_capacity_15"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # User-facing activity is not an audit product. Keep admin/system audit
    # records, but remove previously accumulated user-action history.
    op.execute(sa.text("DELETE FROM audit_logs WHERE actor_type = 'user'"))

    # Device entitlement only needs pseudonymous slot membership. Historical
    # online timestamps and client-provided labels are not needed for service.
    op.execute(
        sa.text(
            "UPDATE devices "
            "SET last_seen_at = NULL, "
            "device_name = 'Android-устройство', "
            "platform = 'android'"
        )
    )


def downgrade() -> None:
    # Deleted/minimized user metadata must not be reconstructed.
    pass
