"""agents start inactive

Slice 16: a new agent is inactive until the owner activates it through the readiness
checklist (`POST /agent/activate`). Only the column's default changes; every existing
row keeps its current value.

Revision ID: b6df7d153ce0
Revises: e8b1f4c27a90
Create Date: 2026-10-28 22:01:36.836139

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'b6df7d153ce0'
down_revision: Union[str, Sequence[str], None] = 'e8b1f4c27a90'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column("agents", "active", server_default="false")


def downgrade() -> None:
    """Downgrade schema."""
    # Existing rows are untouched either way; only what a new row starts as changes back.
    op.alter_column("agents", "active", server_default="true")
