"""Pure, offline-testable policies for GOLDWAY; no network or secrets."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import re
from typing import Any

SPREADSHEET_ID = "17vpYrUUjjTxAhQeLA_uHq9XceNe22X81GL9e7pBPgrw"
TASK_SHEET = "Задачи_GOLDWAY"
REPORT_SHEET = "Ален_день"
TOUCH_SHEET = "Касания_GOLDWAY"
RECEIPT_SHEET = "Приемка_14_10"
TASK_HEADERS = ("ID", "Задача", "Ответственный", "Подтвержденное состояние", "Следующее действие", "Критерий готовности", "Статус", "Срок / период", "Доказательство / ссылка", "Комментарий")
REPORT_HEADERS = ("Дата", "Новые заявки", "Повторные касания", "Ответы на касания", "Заказы", "Оплачено €", "Публикации / ссылки", "Блогеры / следующий шаг", "Проблемы склада и сайта", "План завтра", "Отчёт заполнен")
TOUCH_HEADERS = ("Дата касания", "Клиент / ссылка диалога", "Причина обращения", "Статус", "Следующее касание", "ID заказа", "Сумма €", "Ответственный", "Комментарий")
TASK_STATUSES = frozenset({"К проверке", "Готов материал", "В работе", "Выполнено", "Ждём данные"})
TOUCH_DONE = frozenset({"закрыто", "выполнено", "отменено", "неактуально"})
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

class ValidationError(ValueError):
    pass


def val(row: list[Any], index: int) -> str:
    if index >= len(row) or row[index] is None:
        return ""
    return str(row[index]).strip()


def require_headers(rows: list[list[Any]], expected: tuple[str, ...], name: str) -> None:
    if not rows:
        raise ValidationError(f"Вкладка {name} не содержит заголовков")
    actual = [val(rows[0], i) for i in range(len(expected))]
    if actual != list(expected):
        raise ValidationError(f"Структура вкладки {name} изменилась: запись остановлена")


def iso_day(value: str) -> date | None:
    """Only real ISO dates; no guesses about 'today', 'urgent', or calendar windows."""
    if not DATE_RE.fullmatch(value.strip()):
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def sheet_day(value: str) -> date | None:
    """Accept existing explicitly dated sheet cells, never relative deadlines."""
    v = value.strip()
    if (result := iso_day(v)) is not None:
        return result
    if re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", v):
        try:
            return datetime.strptime(v, "%d.%m.%Y").date()
        except ValueError:
            pass
    return None


@dataclass(frozen=True)
class Task:
    number: int
    row_number: int
    name: str
    owner: str
    fact: str
    next_action: str
    criterion: str
    status: str
    deadline_text: str
    evidence: str
    note: str


def tasks_from_rows(rows: list[list[Any]]) -> list[Task]:
    require_headers(rows, TASK_HEADERS, TASK_SHEET)
    result: list[Task] = []
    seen: set[int] = set()
    for row_number, row in enumerate(rows[1:], start=2):
        if not val(row, 0):
            continue
        try:
            number = int(val(row, 0))
        except ValueError as exc:
            raise ValidationError(f"Некорректный ID в строке {row_number}") from exc
        if number <= 0 or number in seen:
            raise ValidationError(f"Повторный или некорректный ID задачи: {number}")
        seen.add(number)
        status = val(row, 6)
        if status not in TASK_STATUSES:
            raise ValidationError(f"Неизвестный статус задачи {number}: {status}")
        if not val(row, 1):
            raise ValidationError(f"Пустое название задачи {number}")
        result.append(Task(number, row_number, val(row, 1), val(row, 2), val(row, 3), val(row, 4), val(row, 5), status, val(row, 7), val(row, 8), val(row, 9)))
    return result


def task_summary(tasks: list[Task]) -> dict[str, int]:
    return dict(Counter(t.status for t in tasks))


def owner_can_report(role: str, owner: str) -> bool:
    if role in ("admin", "manager"):
        return True
    if role == "alen":
        return "ален" in owner.casefold()
    if role == "nastya":
        return "наст" in owner.casefold()
    return False


def goods_contact(stage: str) -> str:
    """Mandatory factual routing; never guess where goods currently are."""
    norm = stage.casefold().strip()
    if norm in {"sent", "received", "отправлено", "получено"}:
        return "alen"
    if norm in {"with_nastya", "у_насти", "у_настии", "у_насти_ещё"}:
        return "nastya"
    raise ValidationError("Укажите confirmed stage: sent, received или with_nastya")


def touches_due(rows: list[list[Any]], today: date) -> list[dict[str, str | int]]:
    require_headers(rows, TOUCH_HEADERS, TOUCH_SHEET)
    due: list[dict[str, str | int]] = []
    for row_number, row in enumerate(rows[1:], start=2):
        if not val(row, 1):
            continue
        if val(row, 3).casefold() in TOUCH_DONE:
            continue
        day = sheet_day(val(row, 4))
        if day is not None and day <= today:
            due.append({"row": row_number, "client": val(row, 1), "due": day.isoformat(), "reason": val(row, 2)})
    return due


def report_filled(rows: list[list[Any]], today: date) -> bool:
    require_headers(rows, REPORT_HEADERS, REPORT_SHEET)
    for row in rows[1:]:
        if sheet_day(val(row, 0)) == today:
            return val(row, 10).casefold() in {"true", "истина", "да", "1"}
    return False


@dataclass(frozen=True)
class DailyReport:
    day: date
    new_leads: int
    followups: int
    replies: int
    orders: int
    paid_eur: Decimal
    publications: str
    bloggers: str
    issues: str
    tomorrow: str

    def sheet_values(self) -> list[Any]:
        return [self.new_leads, self.followups, self.replies, self.orders,
                float(self.paid_eur), self.publications, self.bloggers, self.issues, self.tomorrow, True]


def parse_report_payload(payload: str) -> DailyReport:
    """10 explicitly supplied pipe-separated fields; zero must be entered, never inferred."""
    parts = [part.strip() for part in payload.split("|")]
    if len(parts) != 10 or any(not v for v in parts):
        raise ValidationError("Нужно 10 непустых полей: дата | 4 счетчика | оплачено € | публикации | блогеры | проблемы | план")
    day = iso_day(parts[0])
    if day is None:
        raise ValidationError("Дата строго YYYY-MM-DD")
    counts: list[int] = []
    for s in parts[1:5]:
        if not re.fullmatch(r"\d+", s):
            raise ValidationError("Количество — неотрицательное целое из подтверждённого отчёта")
        counts.append(int(s))
    try:
        paid = Decimal(parts[5].replace(",", "."))
    except InvalidOperation as exc:
        raise ValidationError("Оплачено € — неотрицательная числовая сумма") from exc
    if not paid.is_finite() or paid < 0 or paid.as_tuple().exponent < -2:
        raise ValidationError("Укажите подтверждённую сумму в € с максимум 2 знаками после запятой")
    if any(len(s) > 800 for s in parts[6:]):
        raise ValidationError("Текст отчёта слишком длинный")
    return DailyReport(day, *counts, paid, *parts[6:])


def parse_touch_payload(payload: str) -> tuple[date, str, str, str]:
    """Next touch date | client/dialog reference | reason | optional order ID."""
    parts = [p.strip() for p in payload.split("|")]
    if len(parts) != 4:
        raise ValidationError("Формат: YYYY-MM-DD | клиент/ссылка | причина | ID заказа или -")
    day = iso_day(parts[0])
    if day is None or not parts[1] or not parts[2]:
        raise ValidationError("Укажите реальную дату и клиента/причину; неизвестные данные не заменяйте догадками")
    if any(len(p) > 500 for p in parts):
        raise ValidationError("Слишком длинное поле")
    return day, parts[1], parts[2], "" if parts[3] == "-" else parts[3]


def receipt_uncertainties(rows: list[list[Any]]) -> int:
    """Read-only count of explicitly unresolved receipt reconciliation notes."""
    markers = ("сверить", "требуется сопоставлен", "не распределен", "не изменена", "не внесена")
    return sum(1 for r in rows[1:] if any(m in val(r, 6).casefold() for m in markers))


def audit_tables(task_rows: list[list[Any]], report_rows: list[list[Any]], touch_rows: list[list[Any]], today: date) -> dict[str, Any]:
    tasks = tasks_from_rows(task_rows)
    require_headers(report_rows, REPORT_HEADERS, REPORT_SHEET)
    require_headers(touch_rows, TOUCH_HEADERS, TOUCH_SHEET)
    active_touches = [r for r in touch_rows[1:] if val(r, 1)]
    return {
        "tasks": len(tasks), "statuses": task_summary(tasks),
        "tasks_without_strict_date": sum(1 for t in tasks if iso_day(t.deadline_text) is None),
        "touches_registered": len(active_touches),
        "touches_due": len(touches_due(touch_rows, today)),
        "alen_report_filled_today": report_filled(report_rows, today),
        "read_only_audit": True,
    }
