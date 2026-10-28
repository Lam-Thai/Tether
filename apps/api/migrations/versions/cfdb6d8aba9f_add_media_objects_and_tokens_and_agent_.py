"""add media objects and tokens and agent voice

Revision ID: cfdb6d8aba9f
Revises: d4a7c2e19b53
Create Date: 2026-10-27 17:44:11.221694

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'cfdb6d8aba9f'
down_revision: Union[str, Sequence[str], None] = 'd4a7c2e19b53'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Reviewed by hand: business_id, object_id and expires_at indexed; partial unique on the cache key.
    op.create_table('media_objects',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('business_id', sa.UUID(), nullable=False),
    sa.Column('content_type', sa.Text(), nullable=False),
    sa.Column('data', sa.LargeBinary(), nullable=False),
    sa.Column('cache_key', sa.Text(), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("content_type <> ''", name='ck_media_objects_content_type'),
    sa.CheckConstraint('octet_length(data) <= 5242880', name='ck_media_objects_size'),
    sa.ForeignKeyConstraint(['business_id'], ['businesses.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_media_objects_business_id'), 'media_objects', ['business_id'], unique=False)
    op.create_index('ix_media_objects_expires_at', 'media_objects', ['expires_at'], unique=False)
    op.create_index('uq_media_objects_cache_key', 'media_objects', ['business_id', 'cache_key'], unique=True, postgresql_where=sa.text('cache_key IS NOT NULL'))
    op.create_table('media_tokens',
    sa.Column('token_hash', sa.LargeBinary(), nullable=False),
    sa.Column('object_id', sa.UUID(), nullable=False),
    sa.Column('business_id', sa.UUID(), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('octet_length(token_hash) = 32', name='ck_media_tokens_hash_length'),
    sa.ForeignKeyConstraint(['business_id'], ['businesses.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['object_id'], ['media_objects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('token_hash')
    )
    op.create_index(op.f('ix_media_tokens_business_id'), 'media_tokens', ['business_id'], unique=False)
    op.create_index('ix_media_tokens_expires_at', 'media_tokens', ['expires_at'], unique=False)
    op.create_index(op.f('ix_media_tokens_object_id'), 'media_tokens', ['object_id'], unique=False)
    op.add_column('agents', sa.Column('voice', sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    # Reviewed by hand: business_id, object_id and expires_at indexed; partial unique on the cache key.
    op.drop_column('agents', 'voice')
    op.drop_index(op.f('ix_media_tokens_object_id'), table_name='media_tokens')
    op.drop_index('ix_media_tokens_expires_at', table_name='media_tokens')
    op.drop_index(op.f('ix_media_tokens_business_id'), table_name='media_tokens')
    op.drop_table('media_tokens')
    op.drop_index('uq_media_objects_cache_key', table_name='media_objects', postgresql_where=sa.text('cache_key IS NOT NULL'))
    op.drop_index('ix_media_objects_expires_at', table_name='media_objects')
    op.drop_index(op.f('ix_media_objects_business_id'), table_name='media_objects')
    op.drop_table('media_objects')
