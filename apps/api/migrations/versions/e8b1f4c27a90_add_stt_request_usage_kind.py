"""add stt_request usage kind

Revision ID: e8b1f4c27a90
Revises: cfdb6d8aba9f
Create Date: 2026-10-27 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e8b1f4c27a90'
down_revision: Union[str, Sequence[str], None] = 'cfdb6d8aba9f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_KINDS_BEFORE = (
    "'gemini_request', 'gemini_tokens', 'tts_request', 'call', 'call_seconds', 'sms', "
    "'calendar_call', 'billing_call', 'telephony_api', 'failed_request'"
)
_KINDS_AFTER = (
    "'gemini_request', 'gemini_tokens', 'tts_request', 'stt_request', 'call', 'call_seconds', "
    "'sms', 'calendar_call', 'billing_call', 'telephony_api', 'failed_request'"
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


def downgrade() -> None:
    """Downgrade schema."""
    # Rows of the removed kind would violate the narrower constraint.
    op.execute("DELETE FROM usage_counters WHERE kind = 'stt_request'")
    op.execute("DELETE FROM usage_events WHERE event_type = 'stt_request'")
    _usage_kinds(_KINDS_BEFORE)
