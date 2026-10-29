"""account deletion: identity_call usage kind, businesses.owner_id cascades

Revision ID: a3c5e7f91b24
Revises: b6df7d153ce0
Create Date: 2026-10-29 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a3c5e7f91b24'
down_revision: Union[str, Sequence[str], None] = 'b6df7d153ce0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_KINDS_BEFORE = (
    "'gemini_request', 'gemini_tokens', 'tts_request', 'stt_request', 'call', 'call_seconds', "
    "'sms', 'calendar_call', 'billing_call', 'telephony_api', 'failed_request'"
)
_KINDS_AFTER = (
    "'gemini_request', 'gemini_tokens', 'tts_request', 'stt_request', 'call', 'call_seconds', "
    "'sms', 'calendar_call', 'billing_call', 'telephony_api', 'identity_call', "
    "'failed_request'"
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
    _usage_kinds(_KINDS_AFTER)
    # Deleting an account deletes the user row; their business (and, through it, every
    # tenant table, all already ON DELETE CASCADE) goes with it.
    op.drop_constraint('businesses_owner_id_fkey', 'businesses', type_='foreignkey')
    op.create_foreign_key(
        'businesses_owner_id_fkey',
        'businesses',
        'users',
        ['owner_id'],
        ['id'],
        ondelete='CASCADE',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('businesses_owner_id_fkey', 'businesses', type_='foreignkey')
    op.create_foreign_key(
        'businesses_owner_id_fkey', 'businesses', 'users', ['owner_id'], ['id']
    )
    # Rows of the removed kind would violate the narrower constraint.
    op.execute("DELETE FROM usage_counters WHERE kind = 'identity_call'")
    op.execute("DELETE FROM usage_events WHERE event_type = 'identity_call'")
    _usage_kinds(_KINDS_BEFORE)
