"""An explicit Moscow label confirms only the existing exact IANA zone."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from goldway.reminder_times import resolve_recurrence, resolve_reminder_at


def reference(zone):
    return datetime(2026, 10, 16, 18, 0, tzinfo=zone)


@pytest.mark.parametrize('qualifier', ['по Москве', 'мск', 'МСК', 'ПО МОСКВЕ'])
def test_moscow_clock_label_preserves_message_zone(qualifier):
    zone = ZoneInfo('Europe/Moscow')
    result = resolve_reminder_at(f'Напомни завтра в 19:07 {qualifier}', reference(zone))
    assert result == datetime(2026, 10, 17, 19, 7, tzinfo=zone)
    assert result.tzinfo is zone


@pytest.mark.parametrize('qualifier', ['по Москве', 'мск'])
def test_live_recurring_phrase_preserves_existing_moscow_rule(qualifier):
    result = resolve_recurrence(
        f'Создай регулярную задачу проверить карточки каждый день в 19:07 {qualifier}',
        reference(ZoneInfo('Europe/Moscow')))
    assert result == {'frequency': 'daily', 'weekdays': list(range(7)),
                      'time': '19:07', 'timezone': 'Europe/Moscow'}


@pytest.mark.parametrize('zone', [ZoneInfo('UTC'), timezone.utc, ZoneInfo('Europe/Riga'),
                                  ZoneInfo('Europe/Minsk'), timezone(timedelta(hours=3))])
@pytest.mark.parametrize('qualifier', ['по Москве', 'мск'])
def test_moscow_label_cannot_override_or_infer_zone(zone, qualifier):
    assert resolve_reminder_at(f'Завтра в 19:07 {qualifier}', reference(zone)) is None
    assert resolve_recurrence(f'Каждый день в 19:07 {qualifier}', reference(zone)) is None


@pytest.mark.parametrize('qualifier', [
    'UTC', 'GMT', 'Europe/Moscow', 'Europe/Riga', '+03:00', '-02:00',
    'по московскому времени', 'мск UTC', 'по Москве GMT',
    'по Москве +03:00', 'мск Europe/Riga', 'по Москве или UTC',
    'по Москве мск', 'не мск', 'не по Москве', 'мск+3', 'по Москве-2',
])
def test_unsupported_or_ambiguous_qualifiers_stay_rejected(qualifier):
    ref = reference(ZoneInfo('Europe/Moscow'))
    assert resolve_reminder_at(f'Завтра в 19:07 {qualifier}', ref) is None
    assert resolve_recurrence(f'Каждый день в 19:07 {qualifier}', ref) is None


@pytest.mark.parametrize('text', ['Завтра в 19:07 мск?', 'Примерно завтра в 19:07 по Москве',
                                  'Завтра в 19:07 или 20:00 мск', 'Не напомни завтра в 19:07 мск'])
def test_moscow_label_does_not_relax_uncertainty_or_clock_conflicts(text):
    assert resolve_reminder_at(text, reference(ZoneInfo('Europe/Moscow'))) is None


def test_original_unqualified_clock_and_duration_are_unchanged():
    ref = reference(ZoneInfo('Europe/Moscow'))
    assert resolve_reminder_at('Напомни в 19:07', ref) == ref.replace(hour=19, minute=7)
    assert resolve_reminder_at('Напомни через 30 минут', ref) == ref + timedelta(minutes=30)
    assert resolve_recurrence('Каждый день в 19:07', ref)['timezone'] == 'Europe/Moscow'


@pytest.mark.parametrize('qualifier', [
    'не в мск', 'ни мск', 'ни в мск', 'не в по Москве', 'ни по Москве',
    'мск +3', 'мск + 3', 'мск -2', 'по Москве +3',
    '+3 мск', '-2 по Москве', 'по Москве UTC', 'GMT мск',
])
def test_negative_and_adjacent_offset_labels_cannot_be_erased(qualifier):
    ref = reference(ZoneInfo('Europe/Moscow'))
    assert resolve_reminder_at(f'Напомни завтра в 19:07 {qualifier}', ref) is None
    assert resolve_recurrence(f'Каждый день в 19:07 {qualifier}', ref) is None


def test_unrelated_words_are_not_timezone_restrictions():
    ref = reference(ZoneInfo('Europe/Moscow'))
    assert resolve_reminder_at('По задаче про карточки напомни завтра в 19:07 мск', ref)
    assert resolve_recurrence('Проверять карточки по инструкции каждый день в 19:07 по Москве', ref)
