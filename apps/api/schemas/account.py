"""Request/response models for `DELETE /api/v1/account` (issue #50). Counts and flags only,
never provider text."""

from pydantic import BaseModel, ConfigDict, Field


class AccountDelete(BaseModel):
    # Checked by the service (422 `confirmation_mismatch`), so a wrong or empty phrase is
    # that error and not a generic validation error. Bounded so a huge body is refused.
    confirmation: str = Field(max_length=200)


class AccountDeletionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    calendar_events_cancelled: int
    calendar_events_not_cancelled: int
    # null: no calendar connection. false: Google did not confirm the revoke, so the owner
    # should remove Tether's access in their Google account.
    calendar_token_revoked: bool | None
    # False when the sign-in could not be removed at Clerk: the data IS deleted, and the
    # same request can be repeated to try the sign-in again.
    sign_in_deleted: bool
