"""`DayHours` validation: local wall-clock windows only."""

from datetime import time

import pytest
from pydantic import ValidationError

from schemas.hours import DayHours


@pytest.mark.parametrize(
    ("opens_at", "closes_at"),
    [("09:00Z", "17:00"), ("09:00", "17:00+02:00"), ("09:00+01:00", "17:00+01:00")],
)
def test_times_with_an_offset_are_a_validation_error_not_a_crash(opens_at, closes_at):
    with pytest.raises(ValidationError, match="leave out any offset"):
        DayHours.model_validate(
            {"day_of_week": 1, "is_closed": False, "opens_at": opens_at, "closes_at": closes_at}
        )


def test_naive_local_times_are_still_accepted():
    day = DayHours.model_validate(
        {"day_of_week": 1, "is_closed": False, "opens_at": "09:00", "closes_at": "17:00"}
    )
    assert (day.opens_at, day.closes_at) == (time(9), time(17))
