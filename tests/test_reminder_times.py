from copy import deepcopy
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from goldway.reminder_times import next_occurrence, resolve_recurrence, resolve_reminder_at


MOSCOW = ZoneInfo("Europe/Moscow")
NEW_YORK = ZoneInfo("America/New_York")
REFERENCE = datetime(2026, 10, 14, 23, 45, tzinfo=MOSCOW)


@pytest.mark.parametrize("text,minutes", [
    ("вернусь через 30 минут", 30),
    ("ещё не сделал, сделаю через 30 минут", 30),
    ("ЕЩЁ НЕ СДЕЛАЛ, СДЕЛАЮ ЧЕРЕЗ 30 МИНУТ", 30),
    ("через полчаса", 30), ("через час", 60),
    ("через 2 часа", 120), ("через 1 минуту", 1),
    ("через 2 минуты", 2), ("через 10 мин", 10),
    ("через 1 час", 60), ("через 24 часа", 1440),
    ("через 1 день", 1440), ("через 7 дней", 10080),
    ("через 168 часов", 10080), ("через 10080 минут", 10080),
    ("счет 500 рублей, вернусь через 30 минут", 30),
    ("через 30 минут, то есть через полчаса", 30),
])
def test_elapsed_durations_use_original_message_time_and_cross_midnight(text, minutes):
    result = resolve_reminder_at(text, REFERENCE)
    expected = REFERENCE.astimezone(timezone.utc) + timedelta(minutes=minutes)
    assert result == expected.astimezone(MOSCOW)
    assert result.tzinfo is MOSCOW


@pytest.mark.parametrize("text,expected", [
    ("завтра в 09:00", datetime(2026, 10, 15, 9, tzinfo=MOSCOW)),
    ("сегодня в 23:59", datetime(2026, 10, 14, 23, 59, tzinfo=MOSCOW)),
    ("в 23:59", datetime(2026, 10, 14, 23, 59, tzinfo=MOSCOW)),
    ("2026-10-16 в 09:00", datetime(2026, 10, 16, 9, tzinfo=MOSCOW)),
    ("16.10.2026 9:00", datetime(2026, 10, 16, 9, tzinfo=MOSCOW)),
    ("завтра, 15.10.2026 в 09:00", datetime(2026, 10, 15, 9, tzinfo=MOSCOW)),
    ("через 30 минут, завтра в 00:15", datetime(2026, 10, 15, 0, 15, tzinfo=MOSCOW)),
])
def test_explicit_calendar_and_agreeing_evidence(text, expected):
    assert resolve_reminder_at(text, REFERENCE) == expected


@pytest.mark.parametrize("text", [
    "", "вернусь", "30", "500 рублей", "стоимость 2 часа 500 рублей",
    "завтра", "сегодня", "2026-10-15", "в 09:00", "сегодня в 09:00",
    "в 23:45", "2026-10-14 23:45", "2025-10-15 10:00",
    "завтра или сегодня в 09:00", "завтра в 09:00 и 10:00",
    "завтра 2026-10-16 в 09:00", "через 30 минут, завтра в 01:00",
    "через 30 минут и через 2 часа", "через 1 час 30 минут",
    "через 30 минут и 10 секунд", "через 30 минут или 60",
    "через 0 минут", "через -1 минуту", "через +1 минуту",
    "через True минут", "через False часов", "через 1.5 часа",
    "через 8 дней", "через 169 часов", "через 10081 минуту",
    "через 9999999999999999999999999999 минут", "через два часа",
    "через минут 30", "через 20-30 минут", "через 30",
    "через 30 минут?", "может через 30 минут", "примерно через полчаса",
    "если успею, через час", "не уверен, через час", "вряд ли через час",
    "не через 30 минут", "не вернусь через 30 минут", "через 30 минут не вернусь",
    "не надо через полчаса", "не завтра в 09:00", "не в 23:59",
    "напомни через 30 минут или позже", "через 30 минут назад",
    "завтра в 25:00", "завтра в 12:60", "завтра в 12:30:00",
    "завтра в 12:30 или 9:0", "2026-02-30 12:00", "29.02.2026 12:00",
    "завтра или 20.10 в 12:00", "20 октября в 12:00", "послезавтра в 12:00",
    "каждый день в 09:00", "каждый понедельник в 09:00",
    "через 30 минут, в следующую неделю", "завтра в пятницу в 09:00",
    "завтра в 09:00, либо в 10.2026", "завтра в 09:00, 2026-11",
    "завтра в 09:00, 17-го", "завтра в 09:00 вечера", "завтра в 09:00 pm",
    "завтра в 09:00 UTC", "завтра в 09:00 Europe/Moscow", "завтра в 09:00 +09:00",
])
def test_unsupported_uncertain_conflicting_and_past_evidence_needs_clarification(text):
    assert resolve_reminder_at(text, REFERENCE) is None


@pytest.mark.parametrize("reference,minutes,expected", [
    (datetime(2026, 3, 8, 1, 45, tzinfo=NEW_YORK), 30,
     datetime(2026, 3, 8, 3, 15, tzinfo=NEW_YORK)),
    (datetime(2026, 11, 1, 1, 45, tzinfo=NEW_YORK, fold=0), 30,
     datetime(2026, 11, 1, 1, 15, tzinfo=NEW_YORK, fold=1)),
    (datetime(2026, 11, 1, 1, 45, tzinfo=NEW_YORK, fold=1), 30,
     datetime(2026, 11, 1, 2, 15, tzinfo=NEW_YORK)),
])
def test_elapsed_durations_keep_elapsed_minutes_through_dst(reference, minutes, expected):
    result = resolve_reminder_at(f"через {minutes} минут", reference)
    assert result.astimezone(timezone.utc) == expected.astimezone(timezone.utc)
    assert result.fold == expected.fold


def test_calendar_gap_is_unknown_and_fold_is_first_occurrence_only():
    assert resolve_reminder_at("завтра в 02:30", datetime(2026, 3, 7, 12, tzinfo=NEW_YORK)) is None
    result = resolve_reminder_at("завтра в 01:30", datetime(2026, 10, 31, 12, tzinfo=NEW_YORK))
    assert result.fold == 0
    assert result.utcoffset() == timedelta(hours=-4)
    assert resolve_reminder_at("сегодня в 01:30", datetime(2026, 11, 1, 1, 0, tzinfo=NEW_YORK, fold=1)) is None


@pytest.mark.parametrize("text,frequency,weekdays", [
    ("каждый день в 09:00", "daily", list(range(7))),
    ("ежедневно в 09:00", "daily", list(range(7))),
    ("каждый понедельник в 09:00", "weekly", [0]),
    ("каждый вторник в 09:00", "weekly", [1]),
    ("каждую среду в 09:00", "weekly", [2]),
    ("каждый четверг в 09:00", "weekly", [3]),
    ("каждую пятницу в 09:00", "weekly", [4]),
    ("каждую субботу в 09:00", "weekly", [5]),
    ("каждое воскресенье в 09:00", "weekly", [6]),
    ("по будням в 09:00", "weekly", [0, 1, 2, 3, 4]),
    ("по выходным в 09:00", "weekly", [5, 6]),
    ("каждый понедельник и пятницу в 09:00", "weekly", [0, 4]),
    ("по пн, ср и пт в 09:00", "weekly", [0, 2, 4]),
    ("по понедельникам и средам в 09:00", "weekly", [0, 2]),
    ("напоминай каждый понедельник и каждый четверг в 09:00", "weekly", [0, 3]),
])
def test_supported_recurrence_rules(text, frequency, weekdays):
    assert resolve_recurrence(text, REFERENCE) == {
        "frequency": frequency, "weekdays": weekdays, "time": "09:00", "timezone": "Europe/Moscow",
    }


@pytest.mark.parametrize("text", [
    "каждый день", "каждый понедельник", "по будням", "в 09:00", "завтра в 09:00",
    "раз в неделю в 09:00", "каждую неделю в 09:00", "каждые 2 дня в 09:00",
    "ежемесячно в 09:00", "через день в 09:00", "по праздникам в 09:00",
    "по будням кроме среды в 09:00", "каждый день в 09:00 кроме праздников",
    "не каждый день в 09:00", "не надо каждый день в 09:00",
    "может каждый понедельник в 09:00", "каждый день в 09:00?",
    "каждый день в 09:00 и 10:00", "каждый день в 25:00", "каждый день в 9:0",
    "каждый день или по будням в 09:00", "каждый день, по будням в 09:00",
    "каждый понедельник раз в 2 недели в 09:00", "каждый день с завтра в 09:00",
    "каждый день с 2026-10-15 в 09:00", "каждый день с 15.10.2026 в 09:00",
    "каждый день, не в выходные, в 09:00", "каждый день по четным числам в 09:00",
    "каждый день в 09:00 UTC", "каждый день в 09:00 вечера",
])
def test_recurrence_requires_one_confirmed_supported_cadence_and_time(text):
    assert resolve_recurrence(text, REFERENCE) is None


def rule(clock="09:00", weekdays=None, zone="Europe/Moscow"):
    return {"frequency": "daily" if weekdays is None else "weekly",
            "weekdays": list(range(7)) if weekdays is None else weekdays,
            "time": clock, "timezone": zone}


def test_recurrence_uses_iana_identity_and_fixed_offset_is_not_invented():
    utc_reference = REFERENCE.astimezone(timezone.utc)
    assert resolve_recurrence("каждый день в 09:00", utc_reference)["timezone"] == "UTC"
    fixed_reference = REFERENCE.astimezone(timezone(timedelta(hours=3)))
    assert resolve_recurrence("каждый день в 09:00", fixed_reference) is None
    assert resolve_reminder_at("через 30 минут", fixed_reference).utcoffset() == timedelta(hours=3)


@pytest.mark.parametrize("after,inclusive,expected", [
    (datetime(2026, 10, 14, 8, 59, tzinfo=MOSCOW), False, datetime(2026, 10, 14, 9, tzinfo=MOSCOW)),
    (datetime(2026, 10, 14, 9, tzinfo=MOSCOW), False, datetime(2026, 10, 15, 9, tzinfo=MOSCOW)),
    (datetime(2026, 10, 14, 9, tzinfo=MOSCOW), True, datetime(2026, 10, 14, 9, tzinfo=MOSCOW)),
    (datetime(2026, 10, 14, 9, 0, 1, tzinfo=MOSCOW), True, datetime(2026, 10, 15, 9, tzinfo=MOSCOW)),
    (datetime(2026, 10, 14, 5, 59, tzinfo=timezone.utc), False, datetime(2026, 10, 14, 9, tzinfo=MOSCOW)),
    (datetime(2026, 12, 31, 23, tzinfo=MOSCOW), False, datetime(2027, 1, 1, 9, tzinfo=MOSCOW)),
    (datetime(2024, 2, 28, 23, tzinfo=MOSCOW), False, datetime(2024, 2, 29, 9, tzinfo=MOSCOW)),
])
def test_daily_calendar_slots_and_inclusive_boundary(after, inclusive, expected):
    assert next_occurrence(rule(), after, inclusive=inclusive) == expected


def test_weekly_calendar_skips_weekend_and_keeps_rule_unchanged():
    schedule = rule(weekdays=[0, 1, 2, 3, 4])
    original = deepcopy(schedule)
    assert next_occurrence(schedule, datetime(2026, 10, 16, 10, tzinfo=MOSCOW)) == datetime(2026, 10, 19, 9, tzinfo=MOSCOW)
    assert schedule == original


@pytest.mark.parametrize("weekdays,expected_day", [(None, 9), ([6], 15)])
def test_nonexistent_dst_slots_are_skipped_not_shifted(weekdays, expected_day):
    schedule = rule("02:30", weekdays=weekdays, zone="America/New_York")
    result = next_occurrence(schedule, datetime(2026, 3, 8, 0, tzinfo=NEW_YORK))
    assert result == datetime(2026, 3, expected_day, 2, 30, tzinfo=NEW_YORK)


def test_skipped_calendar_date_in_iana_zone_is_skipped():
    apia = ZoneInfo("Pacific/Apia")
    result = next_occurrence(rule("09:00", zone="Pacific/Apia"), datetime(2011, 12, 29, 10, tzinfo=apia))
    assert result == datetime(2011, 12, 31, 9, tzinfo=apia)


def test_ambiguous_slot_uses_only_first_fold_and_never_fires_twice():
    schedule = rule("01:30", zone="America/New_York")
    first = datetime(2026, 11, 1, 1, 30, tzinfo=NEW_YORK, fold=0)
    second = datetime(2026, 11, 1, 1, 30, tzinfo=NEW_YORK, fold=1)
    assert next_occurrence(schedule, datetime(2026, 11, 1, 0, tzinfo=NEW_YORK)).fold == 0
    assert next_occurrence(schedule, first, inclusive=True).fold == 0
    expected = datetime(2026, 11, 2, 1, 30, tzinfo=NEW_YORK)
    assert next_occurrence(schedule, first) == expected
    assert next_occurrence(schedule, second, inclusive=True) == expected
    assert next_occurrence(schedule, datetime(2026, 11, 1, 1, 0, tzinfo=NEW_YORK, fold=1)) == expected


@pytest.mark.parametrize("patch", [
    {"frequency": True}, {"frequency": ["daily"]}, {"frequency": "monthly"},
    {"weekdays": []}, {"weekdays": [True]}, {"weekdays": [0.0]},
    {"weekdays": [0, 0]}, {"weekdays": [-1]}, {"weekdays": [7]},
    {"weekdays": "0123456"}, {"weekdays": (0, 1, 2, 3, 4, 5, 6)},
    {"weekdays": [0, 1]}, {"time": "9:00"}, {"time": "24:00"},
    {"time": "09:60"}, {"time": "09:00:00"}, {"time": True},
    {"timezone": None}, {"timezone": True}, {"timezone": "No/Such_Zone"},
    {"timezone": "/etc/localtime"}, {"extra": 1},
    {"timezone": "localtime"}, {"timezone": "posix/Europe/Moscow"},
])
def test_malformed_rules_fail_explicitly(patch):
    schedule = rule()
    schedule.update(patch)
    with pytest.raises(ValueError):
        next_occurrence(schedule, REFERENCE)


@pytest.mark.parametrize("schedule", [None, True, [], {}, {"frequency": "daily"}])
def test_invalid_rule_container_or_missing_fields(schedule):
    with pytest.raises(ValueError):
        next_occurrence(schedule, REFERENCE)


@pytest.mark.parametrize("function", [resolve_reminder_at, resolve_recurrence])
@pytest.mark.parametrize("reference", [None, True, datetime(2026, 10, 14)])
def test_parsers_reject_naive_or_invalid_reference(function, reference):
    with pytest.raises(ValueError, match="timezone-aware"):
        function("через 30 минут", reference)


@pytest.mark.parametrize("function", [resolve_reminder_at, resolve_recurrence])
@pytest.mark.parametrize("text", [None, True, 30])
def test_parsers_reject_non_text_evidence(function, text):
    with pytest.raises(TypeError, match="text"):
        function(text, REFERENCE)


def test_next_slot_rejects_invalid_reference_and_non_bool_inclusive():
    with pytest.raises(ValueError, match="timezone-aware"):
        next_occurrence(rule(), datetime(2026, 10, 14))
    with pytest.raises(ValueError, match="inclusive"):
        next_occurrence(rule(), REFERENCE, inclusive=1)


def test_datetime_range_limits_do_not_wrap_or_use_current_clock():
    end = datetime(9999, 12, 31, 23, 59, tzinfo=timezone.utc)
    assert resolve_reminder_at("через 1 минуту", end) is None
    assert resolve_reminder_at("завтра в 09:00", end) is None
    with pytest.raises(ValueError, match="representable"):
        next_occurrence(rule(zone="UTC"), end)
