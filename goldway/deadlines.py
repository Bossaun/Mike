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
_DAY_UNIT = r"(?:день|дня|дней)"
_CALENDAR_DAYS = rf"(?:календарн(?:ый|ых|ого)\s+)?{_DAY_UNIT}"
_IN_DAYS = re.compile(rf"\bчерез\s+([+-]?\d+)\s+{_CALENDAR_DAYS}\b")
_EXPLICIT_DAYS = re.compile(
    rf"\b(?:срок(?:\s+(?:выполнения|исполнения))?|в\s+течение)\b"
    rf"\s*(?:[:=—–]\s*)?([+-]?\d+)\s+{_CALENDAR_DAYS}\b"
)
_DURATION_UNIT = (
    r"(?:день|дня|дней|недел[а-яё]*|месяц[а-яё]*|год[а-яё]*|"
    r"сутк[а-яё]*|час[а-яё]*)"
)
_EXPLICIT_DURATION = re.compile(
    rf"\b(?:срок|в\s+течение)\b[^!?;]{{0,80}}\b{_DURATION_UNIT}\b"
)
_DURATION_QUALIFIER = re.compile(
    r"\b(?:не|нет|минимум|максимум|рабоч[а-яё]*|будн[а-яё]*|отмен[а-яё]*)\b"
)
_OTHER_ANCHOR = re.compile(
    r"\s*(?:[,—–]\s*)?(?:с|со|от|до|после|начиная|считая|отсчитывая|отсч[её]т)\b"
)
_ALTERNATE_START = re.compile(
    r"\b(?:отсч[её]т[а-яё]*|отсчит[а-яё]*|отсчитыв[а-яё]*|начиная|"
    r"считая|считать|считайте)\b|"
    r"\bначал[а-яё]*\s+срок[а-яё]*\b|"
    r"\b(?:с|со|от|до)\s+(?:\d|(?:[а-яё]+\s+){0,3}"
    r"(?:дат[а-яё]*|момент[а-яё]*|день|дня|начал[а-яё]*|окончан[а-яё]*|"
    r"получен[а-яё]*|поступлен[а-яё]*|подписан[а-яё]*|оплат[а-яё]*|"
    r"поставк[а-яё]*|заказ[а-яё]*|отгрузк[а-яё]*|согласован[а-яё]*|"
    r"сегодня|завтра|послезавтра)\b)"
)
_INCOMPLETE_ALTERNATIVE = re.compile(r"\b(?:или|либо)\s+[\w+-]")
_WEEKDAY = re.compile(
    r"\b(?:понедельник[а-яё]*|вторник[а-яё]*|сред[ауеы][а-яё]*|"
    r"четверг[а-яё]*|пятниц[а-яё]*|суббот[а-яё]*|воскресень[а-яё]*|"
    r"пн|вт|ср|чт|пт|сб|вс)\b"
)
_WEEKDAY_FORMS = {
    'понедельник': 0, 'понедельника': 0, 'понедельнику': 0, 'пн': 0,
    'вторник': 1, 'вторника': 1, 'вторнику': 1, 'вт': 1,
    'среда': 2, 'среду': 2, 'среды': 2, 'среде': 2, 'ср': 2,
    'четверг': 3, 'четверга': 3, 'четвергу': 3, 'чт': 3,
    'пятница': 4, 'пятницу': 4, 'пятницы': 4, 'пятнице': 4, 'пт': 4,
    'суббота': 5, 'субботу': 5, 'субботы': 5, 'субботе': 5, 'сб': 5,
    'воскресенье': 6, 'воскресенья': 6, 'воскресенью': 6, 'вс': 6,
}
_EXPLICIT_WEEKDAY = re.compile(
    r'\b(?:к|до|в|во|на)\s+(?P<day>'
    + '|'.join(sorted(_WEEKDAY_FORMS, key=len, reverse=True)) + r')\b'
)
_NEGATED_WEEKDAY = re.compile(
    r'\b(?:не|ни|нет|нельзя|невозмож[а-яё]*|невыполним[а-яё]*|'
    r'отмен[а-яё]*|неактуал[а-яё]*|неверн[а-яё]*)\b'
)
_TRAILING_WEEKDAY_QUALIFIER = re.compile(
    r'\s+(?:(?:именно|только)\s+)?(?:'
    r'(?:следующ|будущ|прошл|текущ|ближайш|кажд)[а-яё]*|'
    r'этой|эту|этого|этому|этом|этот(?=\s*[,.;!?]|\s*$))\b'
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


def is_relative_deadline(text: str) -> bool:
    """Identify source-date-dependent evidence, including invalid durations.

    This signal does not confirm a deadline. Callers without a known original
    message date must also reject uncertain or unsupported relative phrases.
    """
    if not isinstance(text, str):
        raise TypeError("Deadline evidence must be text")
    value = " ".join(text.casefold().split())
    return bool(_RELATIVE.search(value) or re.search(r"\b(?:через|спустя)\b", value)
                or _EXPLICIT_DURATION.search(value) or _WEEKDAY.search(value))


def resolve_deadline(text: str, reference: datetime) -> str | None:
    """Return one confirmed ISO date, or ``None`` when clarification is needed.

    Relative words use ``reference.date()`` in its supplied time zone. A naive
    reference is a caller error and raises ``ValueError``; no host time zone or
    current clock is consulted. Only full YYYY-MM-DD/DD.MM.YYYY dates and
    today/tomorrow/day-after-tomorrow and explicit calendar-day durations
    (``через N дней``, ``срок N дней``, ``в течение N дней``, 1..365) are supported.
    Durations start on the original message date supplied by the caller.
    Explicit unqualified weekdays (к пятнице, до пн) use the nearest matching
    local date, including today. Qualified/negated weekdays need clarification.
    """
    if not isinstance(reference, datetime) or reference.utcoffset() is None:
        raise ValueError("Deadline reference must be a timezone-aware datetime")
    if not isinstance(text, str):
        raise TypeError("Deadline evidence must be text")
    value = " ".join(text.casefold().split())
    if not value or "?" in value or _UNCERTAIN.search(value):
        return None

    weekdays = list(_EXPLICIT_WEEKDAY.finditer(value))
    if _WEEKDAY.search(_EXPLICIT_WEEKDAY.sub(' ', value)):
        return None
    if weekdays:
        assertion = re.sub(r'\bне\s+забуд(?:ь|ьте)\b', '', value)
        if (_NEGATED_WEEKDAY.search(assertion) or _INCOMPLETE_ALTERNATIVE.search(value)
                or any(_TRAILING_WEEKDAY_QUALIFIER.match(value[m.end():]) for m in weekdays)):
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
        for match in weekdays:
            offset = (_WEEKDAY_FORMS[match['day']] - reference.weekday()) % 7
            candidates.add(reference.date() + timedelta(days=offset))
        for match in _RELATIVE.finditer(value):
            offset = {"сегодня": 0, "завтра": 1, "послезавтра": 2}[match.group(1)]
            candidates.add(reference.date() + timedelta(days=offset))
        durations = [*_IN_DAYS.finditer(value), *_EXPLICIT_DAYS.finditer(value)]
        # The source message date is the only supported starting point. Inspect
        # the whole citation: another start may precede the duration or appear
        # in parentheses/a later sentence.
        if durations and _ALTERNATE_START.search(value):
            return None
        for match in durations:
            number = match.group(1)
            if not number.isdecimal() or len(number) > 3 or not 1 <= int(number) <= 365:
                return None
            if _OTHER_ANCHOR.match(value[match.end():]):
                return None
            candidates.add(reference.date() + timedelta(days=int(number)))
    except (ValueError, OverflowError):
        return None

    # Recognise an unsupported duration as ambiguity, even alongside a date.
    duration_remainder = _EXPLICIT_DAYS.sub(" ", _IN_DAYS.sub(" ", value))
    if (_UNSUPPORTED_DURATION.search(duration_remainder)
            or _EXPLICIT_DURATION.search(duration_remainder)
            or re.search(rf"\b{_DURATION_UNIT}\b", duration_remainder)):
        return None
    if durations or _EXPLICIT_DURATION.search(value):
        # An unparsed second duration or a qualified/negated duration is not a
        # confirmed count of calendar days. A reminder's "не забудь" is benign.
        assertion = re.sub(r"\bне\s+забуд(?:ь|ьте)\b", "", value)
        if _DURATION_QUALIFIER.search(assertion):
            return None
        # Full dates and supported durations have already supplied candidates.
        # A remaining alternative such as "или 3" is still ambiguous even when
        # its author omitted the repeated day unit.
        unparsed = _EXPLICIT_DAYS.sub(" ", _IN_DAYS.sub(" ", remaining))
        if _INCOMPLETE_ALTERNATIVE.search(unparsed):
            return None
    if len(candidates) != 1:
        return None
    return next(iter(candidates)).isoformat()
