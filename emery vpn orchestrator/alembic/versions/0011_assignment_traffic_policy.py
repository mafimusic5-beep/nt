"""Reserved revision: traffic policy is runtime-only and is never persisted.

Revision ID: 0011_assignment_traffic_policy
Revises: 0010_device_bound_vless_gate
Create Date: 2026-09-05
"""

from typing import Sequence, Union


revision: str = "0011_assignment_traffic_policy"
down_revision: Union[str, Sequence[str], None] = "0010_device_bound_vless_gate"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
