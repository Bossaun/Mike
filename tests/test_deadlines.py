from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from goldway.deadlines import resolve_deadline


REFERENCE = datetime(2026, 10, 14, 23, 30, tzinfo=ZoneInfo("Europe/Moscow"))


@pytest.mark.parametrize("text,expected", [
    ("Срок: 2026-10-20", "2026-10-20"),
    ("Подготовить до 20.10.2026", "2026-10-20"),
    ("Сегодня", "2026-10-14"),
    ("До завтра", "2026-10-15"),
    ("К послезавтра", "2026-10-16"),
    ("Через 1 день", "2026-10-15"),
    ("через 2 дня", "2026-10-16"),
    ("через 5 дней", "2026-10-19"),
    ("через 365 дней", "2027-10-14"),
    ("Завтра, то есть 2026-10-15", "2026-10-15"),
    ("2026-10-20 / 20.10.2026", "2026-10-20"),
    ("2026-10-20, срок 2026-10-20", "2026-10-20"),
    ("Не забудь отправить до завтра", "2026-10-15"),
])
def test_supported_evidence(text, expected):
    assert resolve_deadline(text, REFERENCE) == expected


@pytest.mark.parametrize("text", [
    "", "Срок не указан", "Как получится", "в пятницу", "до следующего вторника",
    "до пн", "20.10", "до 20 октября", "20/10/2026", "2026-10", "10.2026",
    "2026-02-30", "29.02.2026", "00.10.2026", "31.04.2026", "2026-13-20",
    "2026-00-10", "0000-10-10", "завтра или послезавтра", "2026-10-20 и 21.10.2026",
    "завтра, 2026-10-20", "2026-10-20, а ещё 21.10", "2026-02-30, завтра",
    "не завтра", "НЕ ЗАВТРА", "не до завтра", "наверное завтра", "возможно 2026-10-20",
    "ориентировочно 20.10.2026", "примерно через 3 дня", "раньше чем завтра",
    "позже 2026-10-20", "после завтра", "не уверен, завтра", "Завтра?",
    "постараюсь до завтра", "планирую завтра", "может завтра",
    "через 0 дней", "через -1 день", "через +1 день", "через 366 дней",
    "через 10000 дней", "через два дня", "через 2 рабочих дня",
    "2026-10-20, через два дня", "2026-10-20, пятница",
    "2026-10-20 или через неделю", "завтра или в следующем месяце",
    "завтра или 17-го", "завтра или 17 числа", "если успею, завтра",
    "надеюсь завтра", "думаю до 2026-10-20", "вряд ли завтра",
])
def test_unknown_invalid_or_uncertain_evidence(text):
    assert resolve_deadline(text, REFERENCE) is None


@pytest.mark.parametrize("reference,text,expected", [
    (datetime(2026, 1, 31, tzinfo=timezone.utc), "завтра", "2026-02-01"),
    (datetime(2026, 12, 31, tzinfo=timezone.utc), "завтра", "2027-01-01"),
    (datetime(2024, 2, 28, tzinfo=timezone.utc), "завтра", "2024-02-29"),
    (datetime(2024, 2, 29, tzinfo=timezone.utc), "завтра", "2024-03-01"),
    (datetime(2025, 2, 28, tzinfo=timezone.utc), "послезавтра", "2025-03-02"),
    (datetime(2023, 12, 31, tzinfo=timezone.utc), "через 60 дней", "2024-02-29"),
    (datetime(2026, 10, 14, 21, 30, tzinfo=timezone.utc).astimezone(ZoneInfo("Europe/Moscow")),
     "сегодня", "2026-10-15"),
    (datetime(2026, 10, 15, 0, 30, tzinfo=timezone.utc).astimezone(ZoneInfo("America/Los_Angeles")),
     "сегодня", "2026-10-14"),
    (datetime(9999, 12, 31, tzinfo=timezone.utc), "завтра", None),
])
def test_calendar_and_local_date_boundaries(reference, text, expected):
    assert resolve_deadline(text, reference) == expected


def test_full_leap_day_is_valid_without_using_current_year():
    assert resolve_deadline("до 29.02.2024", REFERENCE) == "2024-02-29"


def test_naive_reference_is_rejected_instead_of_using_host_timezone():
    with pytest.raises(ValueError, match="timezone-aware"):
        resolve_deadline("завтра", datetime(2026, 10, 14))


def test_non_text_evidence_is_a_caller_error():
    with pytest.raises(TypeError, match="text"):
        resolve_deadline(None, REFERENCE)
