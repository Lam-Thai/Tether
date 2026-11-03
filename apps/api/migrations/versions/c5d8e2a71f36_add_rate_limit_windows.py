"""add rate_limit_windows

Revision ID: c5d8e2a71f36
Revises: a3c5e7f91b24
Create Date: 2026-10-29 14:30:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c5d8e2a71f36'
down_revision: Union[str, Sequence[str], None] = 'a3c5e7f91b24'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # No business FK on purpose: the key's subject can be a client IP, and rows expire
    # on their own (purged after a day).
    op.create_table(
        'rate_limit_windows',
        sa.Column('key', sa.String(length=200), nullable=False),
        sa.Column('window_start', sa.DateTime(timezone=True), nullable=False),
        sa.Column('count', sa.Integer(), nullable=False),
        sa.CheckConstraint('count >= 0', name='ck_rate_limit_windows_count'),
        sa.PrimaryKeyConstraint('key', 'window_start'),
    )
    # The purge scans by age.
    op.create_index(
        'ix_rate_limit_windows_window_start', 'rate_limit_windows', ['window_start']
    )


def downgrade() -> None:
    op.drop_index('ix_rate_limit_windows_window_start', table_name='rate_limit_windows')
    op.drop_table('rate_limit_windows')
