"""add escalations and escalation settings

Revision ID: 7ba10367a237
Revises: 3c9ad03690fd
Create Date: 2026-10-27 00:22:21.205146

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '7ba10367a237'
down_revision: Union[str, Sequence[str], None] = '3c9ad03690fd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Reviewed by hand: (business_id, status, created_at) serves every business-scoped
    # list; interaction_id is covered by the leading column of uq_escalations_interaction_reason.
    op.create_table('escalation_settings',
    sa.Column('business_id', sa.UUID(), nullable=False),
    sa.Column('contact_phone', sa.Text(), nullable=True),
    sa.Column('sms_enabled', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('clarification_threshold', sa.Integer(), server_default=sa.text('2'), nullable=False),
    sa.Column('urgent_keywords', postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("contact_phone IS NULL OR contact_phone ~ '^\\+[1-9][0-9]{6,14}$'", name='ck_escalation_settings_contact_phone'),
    sa.CheckConstraint('cardinality(urgent_keywords) <= 20', name='ck_escalation_settings_keywords'),
    sa.CheckConstraint('clarification_threshold BETWEEN 1 AND 5', name='ck_escalation_settings_threshold'),
    sa.ForeignKeyConstraint(['business_id'], ['businesses.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('business_id')
    )
    op.create_table('escalations',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('business_id', sa.UUID(), nullable=False),
    sa.Column('interaction_id', sa.UUID(), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('urgency', sa.Text(), nullable=False),
    sa.Column('status', sa.Text(), server_default=sa.text("'open'"), nullable=False),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('sms_status', sa.Text(), nullable=False),
    sa.Column('sms_error', sa.Text(), nullable=True),
    sa.Column('sms_sid', sa.Text(), nullable=True),
    sa.Column('notified_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("reason IN ('urgent', 'human_requested', 'complaint', 'change_request', 'booking_unresolved', 'integration_failing', 'clarification_exhausted')", name='ck_escalations_reason'),
    sa.CheckConstraint("sms_status IN ('pending', 'disabled', 'logged', 'skipped', 'accepted', 'queued', 'sending', 'sent', 'delivered', 'undelivered', 'failed')", name='ck_escalations_sms_status'),
    sa.CheckConstraint("status IN ('open', 'resolved')", name='ck_escalations_status'),
    sa.CheckConstraint("urgency IN ('normal', 'high')", name='ck_escalations_urgency'),
    sa.ForeignKeyConstraint(['business_id'], ['businesses.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['interaction_id'], ['interactions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('interaction_id', 'reason', name='uq_escalations_interaction_reason'),
    sa.UniqueConstraint('sms_sid', name='uq_escalations_sms_sid')
    )
    op.create_index('ix_escalations_business_status_created', 'escalations', ['business_id', 'status', 'created_at'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    # Reviewed by hand: (business_id, status, created_at) serves every business-scoped
    # list; interaction_id is covered by the leading column of uq_escalations_interaction_reason.
    op.drop_index('ix_escalations_business_status_created', table_name='escalations')
    op.drop_table('escalations')
    op.drop_table('escalation_settings')
