"""interaction inbox lifecycle

Interaction status becomes in_progress | completed | failed | abandoned (data
migrated both ways), plus outcome, summary_source and a summary claim lease,
and the indexes the inbox reads through.

Revision ID: 3c9ad03690fd
Revises: bf9fb35cc492
Create Date: 2026-10-25 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3c9ad03690fd'
down_revision: Union[str, Sequence[str], None] = 'bf9fb35cc492'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Drop what mentions the old vocabulary, rewrite the rows, then re-add.
    op.drop_constraint('ck_interactions_status', 'interactions', type_='check')
    op.drop_index('ix_interactions_active_updated_at', table_name='interactions',
                  postgresql_where=sa.text("status = 'active'"))

    op.add_column('interactions', sa.Column('outcome', sa.String(), nullable=True))
    op.add_column('interactions', sa.Column('summary_source', sa.String(), nullable=True))
    op.add_column('interactions', sa.Column('summary_claimed_at', sa.DateTime(timezone=True), nullable=True))

    op.execute("UPDATE interactions SET status = 'in_progress' WHERE status = 'active'")
    op.execute("UPDATE interactions SET status = 'completed' WHERE status = 'ended'")
    # Existing summaries (if any) predate the source column: they were written by a template.
    op.execute("UPDATE interactions SET summary_source = 'template' WHERE summary IS NOT NULL")
    op.execute(
        """
        UPDATE interactions i SET outcome = CASE
            WHEN EXISTS (
                SELECT 1 FROM bookings b
                WHERE b.interaction_id = i.id AND b.business_id = i.business_id
                  AND b.status = 'confirmed'
            ) THEN 'booked'
            WHEN i.state ->> 'requires_human' = 'true' THEN 'escalated'
            ELSE 'answered'
        END
        WHERE i.status <> 'in_progress'
        """
    )

    op.alter_column('interactions', 'status', server_default='in_progress')
    op.create_check_constraint(
        'ck_interactions_status', 'interactions',
        "status IN ('in_progress', 'completed', 'failed', 'abandoned')",
    )
    op.create_check_constraint(
        'ck_interactions_outcome', 'interactions',
        "outcome IS NULL OR outcome IN ('booked', 'answered', 'escalated', 'failed', 'abandoned')",
    )
    op.create_check_constraint(
        'ck_interactions_summary_source', 'interactions',
        "summary_source IS NULL OR summary_source IN ('ai', 'template')",
    )
    op.create_index('ix_interactions_active_updated_at', 'interactions', ['updated_at'],
                    unique=False, postgresql_where=sa.text("status = 'in_progress'"))
    op.create_index('ix_interactions_business_started_id', 'interactions',
                    ['business_id', 'started_at', 'id'], unique=False)
    op.create_index('ix_interactions_business_id_status', 'interactions',
                    ['business_id', 'status'], unique=False)
    op.create_index('ix_interactions_business_id_intent', 'interactions',
                    ['business_id', 'intent'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_interactions_business_id_intent', table_name='interactions')
    op.drop_index('ix_interactions_business_id_status', table_name='interactions')
    op.drop_index('ix_interactions_business_started_id', table_name='interactions')
    op.drop_index('ix_interactions_active_updated_at', table_name='interactions',
                  postgresql_where=sa.text("status = 'in_progress'"))
    op.drop_constraint('ck_interactions_summary_source', 'interactions', type_='check')
    op.drop_constraint('ck_interactions_outcome', 'interactions', type_='check')
    op.drop_constraint('ck_interactions_status', 'interactions', type_='check')

    op.execute("UPDATE interactions SET status = 'active' WHERE status = 'in_progress'")
    op.execute("UPDATE interactions SET status = 'ended' WHERE status <> 'active'")
    op.alter_column('interactions', 'status', server_default='active')
    op.create_check_constraint('ck_interactions_status', 'interactions',
                               "status IN ('active', 'ended')")
    op.create_index('ix_interactions_active_updated_at', 'interactions', ['updated_at'],
                    unique=False, postgresql_where=sa.text("status = 'active'"))

    op.drop_column('interactions', 'summary_claimed_at')
    op.drop_column('interactions', 'summary_source')
    op.drop_column('interactions', 'outcome')
