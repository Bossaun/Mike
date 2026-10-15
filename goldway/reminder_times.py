"""Small, deterministic Russian reminder-time grammar.

All dates use the supplied reference's zone; elapsed durations use UTC arithmetic.
Calendar slots in a DST gap are invalid (recurrences skip them). An ambiguous
calendar slot always means its first occurrence, never both occurrences.
Unsupported, uncertain or conflicting evidence returns ``None``. This module
does not decide whether a message requests a reminder or which task it concerns.
"""

import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


_UTC = timezone.utc
_TIME = re.compile(r"(?<![\w:])\d{1,2}:\d{2}(?![\w:\d])")
_TIME_LIKE = re.compile(r"\d+\s*:\s*\d+")
_UNSUPPORTED_CLOCK_QUALIFIER = re.compile(
    r"\b(?:utc|gmt|мск|[ap]m)\b|\bпо\s+(?:москве|московскому)\b|"
    r"\b[A-Za-z_]+/[A-Za-z_]+\b|\d:\d{2}\s*(?:утра|вечера|ночи|дня)\b|"
    r"[+-]\d{2}:\d{2}\b|\d:\d{2}z\b", re.IGNORECASE
)
_ISO = re.compile(r"(?<![\w])\d{4}-\d{2}-\d{2}(?![\w])")
_DMY = re.compile(r"(?<![\w])\d{2}\.\d{2}\.\d{4}(?![\w])")
_RELATIVE_DAY = re.compile(r"\b(сегодня|завтра)\b")
_DURATION = re.compile(
    r"\bчерез\s+(?:(?P<number>[+-]?\d+)\s+"
    r"(?P<unit>минуту|минуты|минут|мин|часа|часов|час|день|дня|дней)\b|"
    r"(?P<word>полчаса|час)\b)"
)
_UNCERTAIN = re.compile(
    r"\b(?:наверно|наверное|возможно|вероятно|предположительно|"
    r"ориентировочно|примерно|приблизительно|около|раньше|ранее|позже|"
    r"позднее|после|может|попробую|постараюсь|планирую|планируем|"
    r"если|надеюсь|думаю|вроде|или|либо|назад|кроме|исключая)\b|"
    r"\bне\s+(?:уверен[а-я]*|точн[а-я]*|знаю)\b|\bвряд\s+ли\b"
)
_NEGATED_TIME = re.compile(
    r"\bне\s+(?:надо|нужно|буду|смогу|вернусь|сделаю|получится|"
    r"напоминай|напоминать|напомни)\b|"
    r"\b(?:не|ни)\s+(?:(?:надо|нужно|буду|смогу|вернусь|сделаю|напоминай|"
    r"напоминать|напомни)\s+)?(?:(?:в|к|на|до)\s+)?"
    r"(?:через|сегодня|завтра|кажд[а-я]*|ежедневно|по\s+|\d)"
)
_DAY_WORDS = {
    "понедельник": 0, "понедельникам": 0, "пн": 0,
    "вторник": 1, "вторникам": 1, "вт": 1,
    "среда": 2, "среду": 2, "средам": 2, "ср": 2,
    "четверг": 3, "четвергам": 3, "чт": 3,
    "пятница": 4, "пятницу": 4, "пятницам": 4, "пт": 4,
    "суббота": 5, "субботу": 5, "субботам": 5, "сб": 5,
    "воскресенье": 6, "воскресеньям": 6, "вс": 6,
}
_DAY = "(?:" + "|".join(sorted(_DAY_WORDS, key=len, reverse=True)) + ")"
_WEEKDAY = re.compile(r"\b" + _DAY + r"\b")
_SCHEDULE = re.compile(
    r"\b(?:каждый\s+день|ежедневно|по\s+будням|по\s+выходным|"
    r"(?:каждый|каждую|каждое|по)\s+" + _DAY +
    r"(?:\s*(?:,|и)\s*(?:(?:каждый|каждую|каждое|по)\s+)?" + _DAY + r")*)\b"
)
_UNSUPPORTED_TIME = re.compile(
    r"\b(?:через|спустя|послезавтра|полчаса|полчасика|полтора|"
    r"минут[а-я]*|час[а-я]*|секунд[а-я]*|сут[а-я]*|недел[а-я]*|"
    r"месяц[а-я]*|год[а-я]*|кажд[а-я]*|ежедневно|будн[а-я]*|выходн[а-я]*)\b|"
    r"\b(?:эт[а-я]*|следующ[а-я]*|будущ[а-я]*)\s+день\b|"
    r"(?<![\w])\d{1,2}[./-]\d{1,2}(?:[./-]\d{1,4})?(?![\w])|"
    r"(?<![\w])\d{4}-\d{1,2}(?![\w])|"
    r"(?<![\w])\d{1,2}[./-]\d{4}(?![\w])|\b\d{1,2}(?:-го|\s+числа)\b|"
    r"\b\d{1,2}\s+(?:январ[а-я]*|феврал[а-я]*|март[а-я]*|"
    r"апрел[а-я]*|ма[йяе]|июн[а-я]*|июл[а-я]*|август[а-я]*|"
    r"сентябр[а-я]*|октябр[а-я]*|ноябр[а-я]*|декабр[а-я]*)\b"
)
_CADENCE_MODIFIER = re.compile(
    r"\b(?:раз|четн[а-я]*|нечетн[а-я]*|праздник[а-я]*|начиная|заканчивая|"
    r"еженедельн[а-я]*|ежемесячн[а-я]*|иногда|редко)\b"
)


def _aware(value: datetime) -> None:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("Reminder reference must be a timezone-aware datetime")


def _evidence(text: str, reference: datetime) -> str | None:
    _aware(reference)
    if not isinstance(text, str):
        raise TypeError("Reminder evidence must be text")
    value = " ".join(text.casefold().replace("ё", "е").split())
    if (not value or "?" in value or _UNCERTAIN.search(value) or
            _NEGATED_TIME.search(value) or _UNSUPPORTED_CLOCK_QUALIFIER.search(value)):
        return None
    return value


def _clock_times(value: str) -> tuple[set[time], str] | None:
    clocks = set()
    for match in _TIME.finditer(value):
        try:
            hour, minute = map(int, match.group().split(":"))
            clocks.add(time(hour, minute))
        except ValueError:
            return None
    remaining = _TIME.sub(" ", value)
    if _TIME_LIKE.search(remaining):
        return None
    return clocks, remaining


def _calendar_slot(day: date, clock: time, zone) -> datetime | None:
    naive = datetime.combine(day, clock)
    candidate = naive.replace(tzinfo=zone, fold=0)
    try:
        restored = candidate.astimezone(_UTC).astimezone(zone)
    except (OverflowError, ValueError):
        return None
    if restored.replace(tzinfo=None) != naive:
        return None
    return candidate


def resolve_reminder_at(text: str, reference: datetime) -> datetime | None:
    """Resolve an explicit future instant using message time, never wall time.

    Supported durations are ``через N минут/часов/дней`` (1 minute..7 days),
    ``через полчаса`` and ``через час``. Calendar evidence is HH:MM, optionally
    with today/tomorrow or a full YYYY-MM-DD/DD.MM.YYYY date. A bare clock means
    today and is never rolled forward. The result must be later than reference.
    """
    value = _evidence(text, reference)
    if value is None or _SCHEDULE.search(value):
        return None
    parsed = _clock_times(value)
    if parsed is None:
        return None
    clocks, remaining = parsed
    instants: set[datetime] = set()
    for match in _DURATION.finditer(value):
        if match.group("word"):
            minutes = 30 if match.group("word") == "полчаса" else 60
        else:
            number = match.group("number")
            if not number.isdecimal() or len(number) > 5:
                return None
            factor = 1 if match.group("unit").startswith("мин") else (
                60 if match.group("unit").startswith("час") else 1440
            )
            minutes = int(number) * factor
        if not 1 <= minutes <= 7 * 24 * 60:
            return None
        try:
            instants.add(reference.astimezone(_UTC) + timedelta(minutes=minutes))
        except (OverflowError, ValueError):
            return None
    remaining = _DURATION.sub(" ", remaining)

    days: set[date] = set()
    for pattern, kind in ((_ISO, "iso"), (_DMY, "dmy")):
        for match in pattern.finditer(remaining):
            try:
                if kind == "iso":
                    day = date.fromisoformat(match.group())
                else:
                    day_number, month, year = map(int, match.group().split("."))
                    day = date(year, month, day_number)
                days.add(day)
            except ValueError:
                return None
        remaining = pattern.sub(" ", remaining)
    for match in _RELATIVE_DAY.finditer(remaining):
        try:
            days.add(reference.date() + timedelta(days=match.group() == "завтра"))
        except OverflowError:
            return None
    remaining = _RELATIVE_DAY.sub(" ", remaining)
    if _UNSUPPORTED_TIME.search(remaining) or _WEEKDAY.search(remaining):
        return None
    if len(days) > 1 or len(clocks) > 1 or (days and not clocks):
        return None
    if clocks:
        candidate = _calendar_slot(next(iter(days), reference.date()), next(iter(clocks)), reference.tzinfo)
        if candidate is None:
            return None
        instants.add(candidate.astimezone(_UTC))
    if len(instants) != 1:
        return None
    instant = next(iter(instants))
    if instant <= reference.astimezone(_UTC):
        return None
    try:
        return instant.astimezone(reference.tzinfo)
    except (OverflowError, ValueError):
        return None


def _zone_name(reference: datetime) -> str | None:
    if isinstance(reference.tzinfo, ZoneInfo):
        name = reference.tzinfo.key
        return name if _iana_name(name) else None
    if reference.tzinfo is _UTC:
        return "UTC"
    return None


def resolve_recurrence(text: str, reference: datetime) -> dict | None:
    """Resolve daily/explicit-weekday cadence with one explicit HH:MM.

    Rules retain the reference's IANA time zone (or UTC). A fixed-offset zone
    without an IANA identity cannot establish a reusable calendar rule.
    """
    value = _evidence(text, reference)
    if value is None:
        return None
    zone_name = _zone_name(reference)
    if zone_name is None:
        return None
    parsed = _clock_times(value)
    if parsed is None:
        return None
    clocks, remaining = parsed
    if len(clocks) != 1:
        return None
    schedules = set()
    for match in _SCHEDULE.finditer(remaining):
        phrase = match.group()
        if phrase in {"каждый день", "ежедневно"}:
            schedules.add(("daily", tuple(range(7))))
        elif phrase == "по будням":
            schedules.add(("weekly", tuple(range(5))))
        elif phrase == "по выходным":
            schedules.add(("weekly", (5, 6)))
        else:
            weekdays = tuple(sorted({_DAY_WORDS[m.group()] for m in _WEEKDAY.finditer(phrase)}))
            schedules.add(("weekly", weekdays))
    remaining = _SCHEDULE.sub(" ", remaining)
    if (len(schedules) != 1 or _UNSUPPORTED_TIME.search(remaining) or _CADENCE_MODIFIER.search(remaining) or
            _WEEKDAY.search(remaining) or _RELATIVE_DAY.search(remaining) or
            _ISO.search(remaining) or _DMY.search(remaining)):
        return None
    frequency, weekdays = next(iter(schedules))
    return {"frequency": frequency, "weekdays": list(weekdays),
            "time": next(iter(clocks)).strftime("%H:%M"), "timezone": zone_name}


def _iana_name(name: str) -> bool:
    return (isinstance(name, str) and bool(name) and name not in {"localtime", "posixrules"} and
            not name.startswith(("posix/", "right/")))


def _validate_rule(rule: dict) -> tuple[list[int], time, ZoneInfo]:
    if not isinstance(rule, dict) or set(rule) != {"frequency", "weekdays", "time", "timezone"}:
        raise ValueError("Malformed recurrence rule")
    frequency, weekdays = rule["frequency"], rule["weekdays"]
    if not isinstance(frequency, str) or frequency not in {"daily", "weekly"}:
        raise ValueError("Unsupported recurrence frequency")
    if (not isinstance(weekdays, list) or not weekdays or
            any(type(day) is not int or not 0 <= day <= 6 for day in weekdays) or
            len(set(weekdays)) != len(weekdays) or
            (frequency == "daily" and weekdays != list(range(7)))):
        raise ValueError("Malformed recurrence weekdays")
    clock_text, zone_name = rule["time"], rule["timezone"]
    if not isinstance(clock_text, str) or not re.fullmatch(r"[0-2]\d:[0-5]\d", clock_text):
        raise ValueError("Malformed recurrence time")
    try:
        clock = time.fromisoformat(clock_text)
    except ValueError as exc:
        raise ValueError("Malformed recurrence time") from exc
    if not _iana_name(zone_name):
        raise ValueError("Malformed recurrence timezone")
    try:
        zone = ZoneInfo(zone_name)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ValueError("Malformed recurrence timezone") from exc
    return weekdays, clock, zone


def next_occurrence(rule: dict, after: datetime, inclusive: bool = False) -> datetime:
    """Return the next valid local calendar slot, in the rule's IANA zone.

    Nonexistent DST slots are skipped; ambiguous slots use fold=0 only. All
    comparisons use UTC, including when ``after`` is in the second DST fold.
    Malformed rules/references or an unrepresentable future raise ValueError.
    """
    _aware(after)
    if type(inclusive) is not bool:
        raise ValueError("inclusive must be a bool")
    weekdays, clock, zone = _validate_rule(rule)
    try:
        boundary = after.astimezone(_UTC)
        day = after.astimezone(zone).date()
        # A weekly slot in a DST gap may require the following week's slot.
        for _ in range(15):
            if day.weekday() in weekdays:
                candidate = _calendar_slot(day, clock, zone)
                if candidate is not None:
                    instant = candidate.astimezone(_UTC)
                    if instant > boundary or (inclusive and instant == boundary):
                        return candidate
            day += timedelta(days=1)
    except (OverflowError, ValueError) as exc:
        raise ValueError("No representable future occurrence") from exc
    raise ValueError("No representable future occurrence")
