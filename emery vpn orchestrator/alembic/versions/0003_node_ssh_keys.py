"""Store per-node SSH keys and metadata.

Revision ID: 0003_node_ssh_keys
Revises: 0002_node_orchestration_fields
Create Date: 2026-03-26
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0003_node_ssh_keys"
down_revision: Union[str, Sequence[str], None] = "0002_node_orchestration_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("vpn_nodes", sa.Column("ssh_private_key", sa.Text(), nullable=False, server_default=""))
    op.add_column("vpn_nodes", sa.Column("ssh_public_key", sa.Text(), nullable=False, server_default=""))
    op.add_column("vpn_nodes", sa.Column("ssh_key_fingerprint", sa.String(length=128), nullable=False, server_default=""))
    op.add_column("vpn_nodes", sa.Column("ssh_key_status", sa.String(length=32), nullable=False, server_default="missing"))


def downgrade() -> None:
    op.drop_column("vpn_nodes", "ssh_key_status")
    op.drop_column("vpn_nodes", "ssh_key_fingerprint")
    op.drop_column("vpn_nodes", "ssh_public_key")
    op.drop_column("vpn_nodes", "ssh_private_key")
