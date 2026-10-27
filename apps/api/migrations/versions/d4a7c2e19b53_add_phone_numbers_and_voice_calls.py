"""add phone numbers and voice calls

Revision ID: d4a7c2e19b53
Revises: 7ba10367a237
Create Date: 2026-10-27 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'd4a7c2e19b53'
down_revision: Union[str, Sequence[str], None] = '7ba10367a237'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_KINDS_BEFORE = (
    "'gemini_request', 'gemini_tokens', 'tts_request', 'call', 'call_seconds', 'sms', "
    "'calendar_call', 'billing_call', 'failed_request'"
)
_KINDS_AFTER = (
    "'gemini_request', 'gemini_tokens', 'tts_request', 'call', 'call_seconds', 'sms', "
    "'calendar_call', 'billing_call', 'telephony_api', 'failed_request'"
)


def _usage_kinds(kinds: str) -> None:
    op.drop_constraint('ck_usage_counters_kind', 'usage_counters', type_='check')
    op.create_check_constraint('ck_usage_counters_kind', 'usage_counters', f"kind IN ({kinds})")
    op.drop_constraint('ck_usage_events_event_type', 'usage_events', type_='check')
    op.create_check_constraint(
        'ck_usage_events_event_type', 'usage_events', f"event_type IN ({kinds})"
    )


def upgrade() -> None:
    """Upgrade schema."""
    # Reviewed by hand: business_id indexed for the owner's list; the two UNIQUE columns
    # back the inbound-call lookup (`To`) and the association (`PN...` sid).
    op.create_table('phone_numbers',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('business_id', sa.UUID(), nullable=False),
    sa.Column('provider', sa.String(length=16), server_default='twilio', nullable=False),
    sa.Column('phone_number', sa.String(length=16), nullable=False),
    sa.Column('provider_number_sid', sa.String(length=34), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('last_error', sa.String(length=32), nullable=True),
    sa.Column('verified_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("last_error IS NULL OR last_error IN ('voice_url_mismatch', 'status_callback_mismatch', 'number_missing', 'unavailable')", name='ck_phone_numbers_last_error'),
    sa.CheckConstraint("phone_number ~ '^\\+[1-9][0-9]{6,14}$'", name='ck_phone_numbers_phone_number'),
    sa.CheckConstraint("provider IN ('twilio')", name='ck_phone_numbers_provider'),
    sa.CheckConstraint("status IN ('connected', 'misconfigured')", name='ck_phone_numbers_status'),
    sa.ForeignKeyConstraint(['business_id'], ['businesses.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('phone_number'),
    sa.UniqueConstraint('provider_number_sid')
    )
    op.create_index(op.f('ix_phone_numbers_business_id'), 'phone_numbers', ['business_id'], unique=False)
    op.create_foreign_key(
        'interactions_phone_number_id_fkey',
        'interactions',
        'phone_numbers',
        ['phone_number_id'],
        ['id'],
        ondelete='SET NULL',
    )
    op.create_index(
        'uq_interactions_voice_external_call_id',
        'interactions',
        ['external_call_id'],
        unique=True,
        postgresql_where=sa.text("channel = 'voice'"),
    )
    # Reviewed by hand: lookups are by (interaction_id, turn), covered by the UNIQUE.
    op.create_table('voice_turns',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('business_id', sa.UUID(), nullable=False),
    sa.Column('interaction_id', sa.UUID(), nullable=False),
    sa.Column('turn', sa.Integer(), nullable=False),
    sa.Column('utterance', sa.Text(), nullable=True),
    sa.Column('status', sa.Text(), server_default='pending', nullable=False),
    sa.Column('claim', sa.UUID(), nullable=False),
    sa.Column('heartbeat_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('pending', 'answered', 'failed')", name='ck_voice_turns_status'),
    sa.CheckConstraint('turn >= 1', name='ck_voice_turns_turn'),
    sa.ForeignKeyConstraint(['business_id'], ['businesses.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['interaction_id'], ['interactions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('interaction_id', 'turn', name='uq_voice_turns_interaction_turn')
    )
    op.create_index(op.f('ix_voice_turns_business_id'), 'voice_turns', ['business_id'], unique=False)
    _usage_kinds(_KINDS_AFTER)


def downgrade() -> None:
    """Downgrade schema."""
    # Rows of the new kind would violate the old constraint.
    op.execute("DELETE FROM usage_counters WHERE kind = 'telephony_api'")
    op.execute("DELETE FROM usage_events WHERE event_type = 'telephony_api'")
    _usage_kinds(_KINDS_BEFORE)
    op.drop_index(op.f('ix_voice_turns_business_id'), table_name='voice_turns')
    op.drop_table('voice_turns')
    op.drop_index(
        'uq_interactions_voice_external_call_id',
        table_name='interactions',
        postgresql_where=sa.text("channel = 'voice'"),
    )
    op.drop_constraint('interactions_phone_number_id_fkey', 'interactions', type_='foreignkey')
    op.drop_index(op.f('ix_phone_numbers_business_id'), table_name='phone_numbers')
    op.drop_table('phone_numbers')
