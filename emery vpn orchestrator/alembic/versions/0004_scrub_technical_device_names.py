"""Replace technical device model strings with generic user-facing aliases.

Revision ID: 0004_scrub_technical_device_names
Revises: 0003_node_ssh_keys
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0004_scrub_technical_device_names"
down_revision: Union[str, Sequence[str], None] = "0003_node_ssh_keys"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE devices
        SET device_name = 'Android-устройство'
        WHERE trim(device_name) = ''
           OR lower(device_name) LIKE '%sdk_gphone%'
           OR lower(device_name) LIKE '%google sdk%'
           OR lower(device_name) LIKE '%android sdk built for%'
           OR lower(device_name) LIKE '%generic_x86%'
           OR lower(device_name) LIKE '%x86_64%'
           OR lower(device_name) LIKE '%arm64-v8a%'
           OR lower(device_name) LIKE '%emulator%'
        """
    )


def downgrade() -> None:
    # Original technical model strings were intentionally discarded and cannot be restored.
    pass
