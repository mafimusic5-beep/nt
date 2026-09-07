"""Release TCP ports held by revoked VPN assignment rows.

Revision ID: 0013_release_revoked_assignment_ports
Revises: 0012_drop_assignment_policy
Create Date: 2026-09-07
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0013_release_revoked_assignment_ports"
down_revision: Union[str, Sequence[str], None] = "0012_drop_assignment_policy"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Revoked rows are retained for history and possible subject reactivation,
    # but their former positive TCP ports must be reusable. The database has a
    # unique (node_id, client_port) constraint, so move historical rows into a
    # per-assignment negative sentinel namespace outside the valid TCP range.
    op.execute(
        sa.text(
            "UPDATE vpn_assignments "
            "SET client_port = -id "
            "WHERE status = 'revoked' AND client_port > 0"
        )
    )


def downgrade() -> None:
    # The original positive TCP port is intentionally not reconstructed: once a
    # credential is revoked that port may already have been safely reused.
    pass
