"""Resolve a deliberately small set of explicit Russian deadline expressions.

The caller must supply the cited deadline evidence, rather than a whole meeting
or its metadata. Unsupported, uncertain and conflicting expressions require a
human clarification; this function never supplies a missing year.
"""

import re
from datetime import date, datetime, timedelta


_ISO = re.compile(r"(?<![\w])\d{4}-\d{2}-\d{2}(?![\w])")
_DMY = re.compile(r"(?<![\w])\d{2}\.\d{2}\.\d{4}(?![\w])")
_RELATIVE = re.compile(r"\b(сегодня|завтра|послезавтра)\b")
_IN_DAYS = re.compile(r"\bчерез\s+([+-]?\d+)\s+(день|дня|дней)\b")
_WEEKDAY = re.compile(
    r"\b(?:понедельник[а-яё]*|вторник[а-яё]*|сред[ауеы][а-яё]*|"
    r"четверг[а-яё]*|пятниц[а-яё]*|суббот[а-яё]*|воскресень[а-яё]*|"
    r"пн|вт|ср|чт|пт|сб|вс)\b"
)
_UNCERTAIN = re.compile(
    r"\b(?:наверно|наверное|возможно|вероятно|предположительно|"
    r"ориентировочно|примерно|приблизительно|около|раньше|ранее|позже|"
    r"позднее|после|может|попробую|постараюсь|планирую|планируем|"
    r"если|надеюсь|думаю|вроде)\b|"
    r"\bне\s+(?:(?:на|к|до)\s+)?(?:сегодня|завтра|послезавтра|через|\d)|"
    r"\bне\s+(?:уверен[а-яё]*|точн[а-яё]*|знаю)\b|\bвряд\s+ли\b"
)
_PARTIAL = re.compile(
    r"(?<![\w])\d{1,2}[./-]\d{1,2}(?:[./-]\d{1,4})?(?![\w])|"
    r"(?<![\w])\d{1,2}[./-]\d{4}(?![\w])|\b\d{1,2}(?:-го|\s+числа)\b|"
    r"\b\d{1,2}\s+(?:январ[а-яё]*|феврал[а-яё]*|март[а-яё]*|"
    r"апрел[а-яё]*|ма[йяе]|июн[а-яё]*|июл[а-яё]*|август[а-яё]*|"
    r"сентябр[а-яё]*|октябр[а-яё]*|ноябр[а-яё]*|декабр[а-яё]*)\b"
)
_UNSUPPORTED_DURATION = re.compile(
    r"\b(?:через|спустя)\s+[^,.!?]{0,30}\b(?:день|дня|дней|"
    r"недел[а-яё]*|месяц[а-яё]*|год[а-яё]*|сутк[а-яё]*|час[а-яё]*)\b|"
    r"\b(?:на|в|до)\s+(?:эт[а-яё]*|следующ[а-яё]*|будущ[а-яё]*)\s+"
    r"(?:недел[а-яё]*|месяц[а-яё]*|год[а-яё]*)\b"
)


def resolve_deadline(text: str, reference: datetime) -> str | None:
    """Return one confirmed ISO date, or ``None`` when clarification is needed.

    Relative words use ``reference.date()`` in its supplied time zone. A naive
    reference is a caller error and raises ``ValueError``; no host time zone or
    current clock is consulted. Only full YYYY-MM-DD/DD.MM.YYYY dates and
    today/tomorrow/day-after-tomorrow/``через N дней`` (1..365) are supported.
    """
    if not isinstance(reference, datetime) or reference.utcoffset() is None:
        raise ValueError("Deadline reference must be a timezone-aware datetime")
    if not isinstance(text, str):
        raise TypeError("Deadline evidence must be text")
    value = " ".join(text.casefold().split())
    if not value or "?" in value or _UNCERTAIN.search(value) or _WEEKDAY.search(value):
        return None

    candidates: set[date] = set()
    remaining = value
    for pattern, kind in ((_ISO, "iso"), (_DMY, "dmy")):
        for match in pattern.finditer(value):
            try:
                if kind == "iso":
                    candidate = date.fromisoformat(match.group())
                else:
                    day, month, year = map(int, match.group().split("."))
                    candidate = date(year, month, day)
            except ValueError:
                return None
            candidates.add(candidate)
        remaining = pattern.sub(" ", remaining)

    if _PARTIAL.search(remaining):
        return None

    try:
        for match in _RELATIVE.finditer(value):
            offset = {"сегодня": 0, "завтра": 1, "послезавтра": 2}[match.group(1)]
            candidates.add(reference.date() + timedelta(days=offset))
        for match in _IN_DAYS.finditer(value):
            number = match.group(1)
            if not number.isdecimal() or len(number) > 3 or not 1 <= int(number) <= 365:
                return None
            candidates.add(reference.date() + timedelta(days=int(number)))
    except (ValueError, OverflowError):
        return None

    # Recognise an unsupported duration as ambiguity, even alongside a date.
    if _UNSUPPORTED_DURATION.search(_IN_DAYS.sub(" ", value)):
        return None
    if len(candidates) != 1:
        return None
    return next(iter(candidates)).isoformat()
