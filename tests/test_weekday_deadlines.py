"""Source-date weekday deadlines; no wall clock, model or live APIs."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from goldway.deadlines import is_relative_deadline, resolve_deadline


REF = datetime(2026, 10, 14, 23, 30, tzinfo=ZoneInfo('Europe/Moscow'))


@pytest.mark.parametrize('text,expected', [
    ('к пятнице', '2026-10-16'), ('до пн', '2026-10-19'),
    ('в пятницу', '2026-10-16'), ('на пятницу', '2026-10-16'),
    ('до четверга', '2026-10-15'), ('к вторнику', '2026-10-20'),
    ('в среду', '2026-10-14'), ('к среде', '2026-10-14'),
    ('во вторник', '2026-10-20'), ('в воскресенье', '2026-10-18'),
    ('к субботе', '2026-10-17'), ('Срок: ДО ПТ.', '2026-10-16'),
    ('Не забудь отправить к пятнице', '2026-10-16'),
    ('к пятнице, то есть 16.10.2026', '2026-10-16'),
    ('К пятнице это будет готово.', '2026-10-16'),
    ('К пятнице этот отчёт будет готов.', '2026-10-16'),
])
def test_nearest_explicit_weekday_includes_today(text, expected):
    assert resolve_deadline(text, REF) == expected
    assert is_relative_deadline(text)


@pytest.mark.parametrize('text', [
    'пятница', 'до следующего вторника', 'на следующую пятницу',
    'к этой пятнице', 'на этой неделе в пятницу',
    'в пятницу следующей недели', 'каждую пятницу',
    'не в пятницу', 'не до пн', 'ни к пятнице', 'к пятнице не надо',
    'срок до пятницы отменён', 'к пятнице срок неактуален',
    'в пятницу?', 'примерно к пятнице', 'после пятницы',
    'к пятнице или 3', 'к пятнице либо завтра',
    'к пятнице, 2026-10-23', 'в пятницу и в субботу',
    'в пятницу или в пятницу', 'через 2 рабочих дня к пятнице',
    'В пятницу следующую.', 'В пятницу именно следующую.',
    'В пятницу нельзя.', 'Сделать к пятнице невозможно.',
    'До пн невыполнимо.', 'К пятнице этой.',
])
def test_ambiguous_negated_qualified_or_conflicting_weekday_needs_clarification(text):
    assert resolve_deadline(text, REF) is None
    assert is_relative_deadline(text)


@pytest.mark.parametrize('ref,text,expected', [
    (datetime(2026, 10, 16, 23, 59, tzinfo=timezone.utc), 'к пятнице', '2026-10-16'),
    (datetime(2026, 10, 17, 0, 1, tzinfo=timezone.utc), 'к пятнице', '2026-10-23'),
    (datetime(2026, 12, 31, tzinfo=timezone.utc), 'до пн', '2027-01-04'),
    (datetime(2024, 2, 28, tzinfo=timezone.utc), 'до чт', '2024-02-29'),
    (datetime(9999, 12, 31, tzinfo=timezone.utc), 'до пн', None),
])
def test_calendar_boundaries(ref, text, expected):
    assert resolve_deadline(text, ref) == expected


def test_same_instant_uses_original_local_date():
    instant = datetime(2026, 10, 16, 21, 30, tzinfo=timezone.utc)
    assert resolve_deadline('к пятнице', instant) == '2026-10-16'
    assert resolve_deadline('к пятнице', instant.astimezone(ZoneInfo('Europe/Moscow'))) == '2026-10-23'
