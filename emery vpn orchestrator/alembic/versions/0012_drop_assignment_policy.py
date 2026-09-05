"""Remove any legacy persisted traffic-policy state.

Revision ID: 0012_drop_assignment_policy
Revises: 0011_assignment_traffic_policy
Create Date: 2026-09-05
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0012_drop_assignment_policy"
down_revision: Union[str, Sequence[str], None] = "0011_assignment_traffic_policy"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_policy_column() -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(
        column["name"] == "traffic_policy"
        for column in inspector.get_columns("vpn_assignments")
    )


def upgrade() -> None:
    if _has_policy_column():
        with op.batch_alter_table("vpn_assignments") as batch_op:
            batch_op.drop_column("traffic_policy")

    # An intermediate development build wrote the selected mode to audit
    # details. Remove those rows so backend storage contains no policy history.
    op.execute(
        sa.text(
            "DELETE FROM audit_logs WHERE action = 'vpn_traffic_policy_applied'"
        )
    )


def downgrade() -> None:
    # Runtime policy is intentionally not restored to persistent storage.
    pass
