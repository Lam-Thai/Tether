"""`compute_slots` and `choose_slots`: the pure core of availability. No I/O.

Windows are built the way `BusinessDirectory` builds them (local wall times in the
business's zone, made real through UTC), so DST cases match production.
"""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from integrations.google_calendar.port import BusyInterval
from services.availability import Slot, SlotPreference, choose_slots, compute_slots
from services.booking_rules import BookingRules
from services.directory import Open

NY = ZoneInfo("America/New_York")
FRIDAY = date(2026, 10, 9)
# 2026-03-08: clocks jump 02:00 -> 03:00. 2026-11-01: 02:00 -> 01:00.
SPRING_FORWARD = date(2026, 3, 8)
FALL_BACK = date(2026, 11, 1)

RULES = BookingRules(
    slot_interval_minutes=15, min_notice_minutes=0, max_days_ahead=60, buffer_minutes=0
)


def _at(day: date, hh_mm: str) -> datetime:
    wall = datetime.combine(day, time.fromisoformat(hh_mm), tzinfo=NY)
    return wall.astimezone(UTC).astimezone(NY)


def _open(day: date, opens: str, closes: str) -> Open:
    return Open(day, _at(day, opens), _at(day, closes))


def _busy(day: date, start: str, end: str) -> BusyInterval:
    return BusyInterval(_at(day, start).astimezone(UTC), _at(day, end).astimezone(UTC))


def _labels(slots: list[Slot] | tuple[Slot, ...]) -> list[str]:
    return [slot.start.strftime("%H:%M") for slot in slots]


def _rules(**changes: int) -> BookingRules:
    values = {
        "slot_interval_minutes": RULES.slot_interval_minutes,
        "min_notice_minutes": RULES.min_notice_minutes,
        "max_days_ahead": RULES.max_days_ahead,
        "buffer_minutes": RULES.buffer_minutes,
    }
    return BookingRules(**(values | changes))


def _slots(window: Open, busy=(), *, minutes: int = 30, rules=RULES, now=None) -> list[Slot]:
    # Default: the day before, so neither the notice nor the horizon cuts anything.
    now = now or window.start.astimezone(UTC) - timedelta(days=1)
    return compute_slots([window], list(busy), timedelta(minutes=minutes), rules, now)


# --- compute_slots ---------------------------------------------------------------


def test_free_day_is_every_interval_that_fits_before_closing():
    slots = _slots(_open(FRIDAY, "09:00", "10:00"), minutes=30)
    assert _labels(slots) == ["09:00", "09:15", "09:30"]
    assert all(slot.end - slot.start == timedelta(minutes=30) for slot in slots)


def test_slots_are_in_the_business_timezone():
    slot = _slots(_open(FRIDAY, "09:00", "10:00"))[0]
    assert slot.start.utcoffset() == timedelta(hours=-4)
    assert slot.start.astimezone(UTC) == datetime(2026, 10, 9, 13, 0, tzinfo=UTC)


def test_overlapping_busy_blocks_are_merged():
    busy = [_busy(FRIDAY, "09:30", "10:30"), _busy(FRIDAY, "10:00", "11:10")]
    slots = _slots(_open(FRIDAY, "09:00", "12:00"), busy, minutes=30)
    # 09:00 fits before 09:30; next free grid point after 11:10 is 11:15.
    assert _labels(slots) == ["09:00", "11:15", "11:30"]


def test_a_slot_may_end_exactly_when_a_busy_block_starts_and_start_when_it_ends():
    busy = [_busy(FRIDAY, "10:00", "10:30")]
    slots = _slots(_open(FRIDAY, "09:30", "11:00"), busy, minutes=30)
    assert _labels(slots) == ["09:30", "10:30"]


def test_busy_block_spanning_the_whole_day_leaves_nothing():
    busy = [BusyInterval(_at(FRIDAY, "00:00"), _at(FRIDAY, "23:59"))]
    assert _slots(_open(FRIDAY, "09:00", "17:00"), busy) == []


def test_busy_blocks_outside_the_window_change_nothing():
    busy = [_busy(FRIDAY, "07:00", "08:00"), _busy(FRIDAY, "18:00", "19:00")]
    assert _labels(_slots(_open(FRIDAY, "09:00", "10:00"), busy)) == ["09:00", "09:15", "09:30"]


def test_service_longer_than_the_window_has_no_slot():
    assert _slots(_open(FRIDAY, "09:00", "10:00"), minutes=90) == []


def test_service_longer_than_the_time_left_after_a_busy_block():
    busy = [_busy(FRIDAY, "09:00", "16:00")]
    assert _slots(_open(FRIDAY, "09:00", "17:00"), busy, minutes=75) == []
    assert _labels(_slots(_open(FRIDAY, "09:00", "17:00"), busy, minutes=60)) == ["16:00"]


def test_grid_is_anchored_at_opening_time():
    slots = _slots(
        _open(FRIDAY, "09:10", "10:00"), minutes=20, rules=_rules(slot_interval_minutes=20)
    )
    assert _labels(slots) == ["09:10", "09:30"]


def test_buffer_is_kept_clear_on_both_sides_of_a_busy_block():
    busy = [_busy(FRIDAY, "10:00", "10:30")]
    slots = _slots(
        _open(FRIDAY, "09:00", "12:00"), busy, minutes=30, rules=_rules(buffer_minutes=15)
    )
    labels = _labels(slots)
    assert "09:15" in labels and "09:30" not in labels  # 09:30-10:00 would touch the buffer
    assert "10:30" not in labels and labels[labels.index("09:15") + 1] == "10:45"


def test_minimum_notice_cuts_off_early_slots_and_snaps_to_the_grid():
    now = _at(FRIDAY, "09:05").astimezone(UTC)
    slots = _slots(
        _open(FRIDAY, "09:00", "11:00"), minutes=30, rules=_rules(min_notice_minutes=60), now=now
    )
    # Earliest start 10:05 -> next grid point 10:15.
    assert _labels(slots) == ["10:15", "10:30"]


def test_minimum_notice_past_closing_leaves_nothing():
    now = _at(FRIDAY, "16:30").astimezone(UTC)
    assert (
        _slots(_open(FRIDAY, "09:00", "17:00"), rules=_rules(min_notice_minutes=60), now=now) == []
    )


def test_slots_never_start_in_the_past():
    now = _at(FRIDAY, "09:20").astimezone(UTC)
    assert _labels(_slots(_open(FRIDAY, "09:00", "10:00"), now=now)) == ["09:30"]


def test_horizon_excludes_slots_beyond_max_days_ahead():
    now = _at(FRIDAY - timedelta(days=2), "09:30").astimezone(UTC)
    window = _open(FRIDAY, "09:00", "11:00")
    assert _slots(window, rules=_rules(max_days_ahead=1), now=now) == []
    # The horizon is an instant: exactly two days after `now` is still bookable.
    assert _labels(_slots(window, rules=_rules(max_days_ahead=2), now=now)) == [
        "09:00",
        "09:15",
        "09:30",
    ]


def test_spring_forward_window_has_its_real_length():
    # 01:00-04:00 local on the spring-forward day is two real hours.
    window = _open(SPRING_FORWARD, "01:00", "04:00")
    slots = _slots(window, minutes=60, rules=_rules(slot_interval_minutes=30))
    assert _labels(slots) == ["01:00", "01:30", "03:00"]
    assert all(
        slot.end.astimezone(UTC) - slot.start.astimezone(UTC) == timedelta(minutes=60)
        for slot in slots
    )


def test_fall_back_window_has_its_real_length():
    # 01:00-03:00 local on the fall-back day is three real hours; 01:00 happens twice.
    window = _open(FALL_BACK, "01:00", "03:00")
    slots = _slots(window, minutes=60, rules=_rules(slot_interval_minutes=60))
    assert [slot.start.astimezone(UTC).hour for slot in slots] == [5, 6, 7]
    assert _labels(slots) == ["01:00", "01:00", "02:00"]
    assert [slot.start.utcoffset() for slot in slots] == [
        timedelta(hours=-4),
        timedelta(hours=-5),
        timedelta(hours=-5),
    ]


def test_several_windows_are_combined_in_order():
    windows = [_open(FRIDAY, "09:00", "09:30"), _open(FRIDAY + timedelta(days=1), "10:00", "10:30")]
    now = windows[0].start.astimezone(UTC) - timedelta(days=1)
    slots = compute_slots(windows, [], timedelta(minutes=30), RULES, now)
    assert [(s.start.date(), s.start.strftime("%H:%M")) for s in slots] == [
        (FRIDAY, "09:00"),
        (FRIDAY + timedelta(days=1), "10:00"),
    ]


def test_naive_now_is_refused():
    with pytest.raises(ValueError):
        compute_slots(
            [_open(FRIDAY, "09:00", "10:00")],
            [],
            timedelta(minutes=30),
            RULES,
            datetime(2026, 1, 1),
        )


def test_non_positive_duration_is_refused():
    with pytest.raises(ValueError):
        _slots(_open(FRIDAY, "09:00", "10:00"), minutes=0)


# --- choose_slots ----------------------------------------------------------------


def _day_slots() -> list[Slot]:
    return _slots(
        _open(FRIDAY, "09:00", "19:00"), minutes=30, rules=_rules(slot_interval_minutes=60)
    )


def test_no_preference_spreads_three_options_across_the_day():
    offered, matched = choose_slots(_day_slots(), SlotPreference(FRIDAY))
    assert matched is True
    assert _labels(offered) == ["09:00", "13:00", "18:00"]


def test_fewer_slots_than_the_limit_are_all_offered():
    slots = _day_slots()[:2]
    offered, _ = choose_slots(slots, SlotPreference(FRIDAY))
    assert _labels(offered) == ["09:00", "10:00"]


@pytest.mark.parametrize(
    ("time_of_day", "expected"),
    [
        ("morning", ["09:00", "10:00", "11:00"]),
        ("afternoon", ["12:00", "14:00", "16:00"]),
        ("evening", ["17:00", "18:00"]),
    ],
)
def test_time_of_day_filters_before_spreading(time_of_day, expected):
    offered, matched = choose_slots(_day_slots(), SlotPreference(FRIDAY, time_of_day=time_of_day))
    assert matched is True
    assert _labels(offered) == expected


def test_around_a_time_offers_the_nearest_in_order():
    offered, matched = choose_slots(_day_slots(), SlotPreference(FRIDAY, around=time(14, 20)))
    assert matched is True
    assert _labels(offered) == ["13:00", "14:00", "15:00"]


def test_around_a_time_within_a_time_of_day():
    preference = SlotPreference(FRIDAY, time_of_day="morning", around=time(15, 0))
    offered, _ = choose_slots(_day_slots(), preference)
    assert _labels(offered) == ["09:00", "10:00", "11:00"]


def test_no_slot_in_the_preferred_part_of_the_day_falls_back_to_the_whole_day():
    morning_only = _day_slots()[:3]
    offered, matched = choose_slots(morning_only, SlotPreference(FRIDAY, time_of_day="evening"))
    assert matched is False
    assert _labels(offered) == ["09:00", "10:00", "11:00"]


def test_choose_from_nothing_is_nothing():
    assert choose_slots([], SlotPreference(FRIDAY)) == ((), True)
