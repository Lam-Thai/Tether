"""add faqs and interaction_operations

Revision ID: b6f2a9c41d07
Revises: 17583ea35d8b
Create Date: 2026-10-21 18:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'b6f2a9c41d07'
down_revision: Union[str, Sequence[str], None] = '17583ea35d8b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Must match `models.faq.FAQ_SEARCH_VECTOR_SQL`; copied, not imported, so this
# revision keeps meaning what it meant when it was written.
_FAQ_SEARCH_VECTOR_SQL = (
    "setweight(to_tsvector('english'::regconfig, coalesce(question, '')), 'A') || "
    "setweight(to_tsvector('english'::regconfig, coalesce(answer, '')), 'B') || "
    "setweight(to_tsvector('english'::regconfig, coalesce(category, '')), 'C')"
)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('faqs',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('business_id', sa.UUID(), nullable=False),
    sa.Column('question', sa.Text(), nullable=False),
    sa.Column('answer', sa.Text(), nullable=False),
    sa.Column('category', sa.String(), nullable=True),
    sa.Column('active', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('search_vector', postgresql.TSVECTOR(), sa.Computed(_FAQ_SEARCH_VECTOR_SQL, persisted=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['business_id'], ['businesses.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_faqs_business_id_active', 'faqs', ['business_id', 'active'], unique=False)
    op.create_index('ix_faqs_search_vector', 'faqs', ['search_vector'], unique=False, postgresql_using='gin')
    op.create_table('interaction_operations',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('business_id', sa.UUID(), nullable=False),
    sa.Column('interaction_id', sa.UUID(), nullable=False),
    sa.Column('tool_name', sa.String(), nullable=False),
    sa.Column('arguments', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('result', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('duration_ms', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('succeeded', 'rejected', 'failed')", name='ck_interaction_operations_status'),
    sa.CheckConstraint('duration_ms >= 0', name='ck_interaction_operations_duration'),
    sa.ForeignKeyConstraint(['business_id'], ['businesses.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['interaction_id'], ['interactions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_interaction_operations_business_interaction_created', 'interaction_operations', ['business_id', 'interaction_id', 'created_at'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_interaction_operations_business_interaction_created', table_name='interaction_operations')
    op.drop_table('interaction_operations')
    op.drop_index('ix_faqs_search_vector', table_name='faqs', postgresql_using='gin')
    op.drop_index('ix_faqs_business_id_active', table_name='faqs')
    op.drop_table('faqs')
