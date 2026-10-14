"""Allowlisted Google Sheets adapter; never writes to orders, stocks, supplies or payments."""
from __future__ import annotations
from datetime import date
import json
from typing import Any
from .core import (SPREADSHEET_ID, TASK_HEADERS, REPORT_HEADERS, TOUCH_HEADERS,
                   TASK_SHEET, REPORT_SHEET, TOUCH_SHEET, RECEIPT_SHEET,
                   ValidationError, Task, DailyReport, audit_tables, iso_day,
                   require_headers, task_summary, tasks_from_rows, sheet_day, val,
                   TOUCH_DONE, TASK_STATUSES)

class WriteDisabled(PermissionError):
    pass

class SheetsGateway:
    """A verified-sheet allowlist. All writes are explicit user-triggered."""
    def __init__(self, spreadsheet: Any, allow_write: bool = False):
        if spreadsheet.id != SPREADSHEET_ID:
            raise ValidationError("Unexpected spreadsheet ID; access refused")
        self.spreadsheet = spreadsheet
        self.allow_write = allow_write

    @classmethod
    def connect(cls, settings: Any) -> "SheetsGateway":
        import gspread  # optional dependency, isolated for offline tests
        if settings.service_account_json:
            credentials = json.loads(settings.service_account_json)
            gc = gspread.service_account_from_dict(credentials)
        else:
            gc = gspread.service_account(filename=settings.service_account_file)
        book = gc.open_by_key(settings.spreadsheet_id)
        return cls(book, allow_write=settings.sheets_write_enabled)

    def _ws(self, sheet: str) -> Any:
        if sheet not in {TASK_SHEET, REPORT_SHEET, TOUCH_SHEET, RECEIPT_SHEET, "Заказы", "Поставки", "Склад"}:
            raise ValidationError("Sheet not on read allowlist")
        return self.spreadsheet.worksheet(sheet)

    def _read(self, sheet: str, bound: str, headers: tuple[str, ...] | None = None) -> list[list[Any]]:
        rows = self._ws(sheet).get(bound)
        if headers is not None:
            require_headers(rows, headers, sheet)
        return rows

    def tasks(self) -> list[Task]:
        return tasks_from_rows(self._read(TASK_SHEET, "A1:J", TASK_HEADERS))

    def reports(self) -> list[list[Any]]:
        return self._read(REPORT_SHEET, "A1:K", REPORT_HEADERS)

    def touches(self) -> list[list[Any]]:
        return self._read(TOUCH_SHEET, "A1:I", TOUCH_HEADERS)

    def _sentinel_headers(self) -> dict[str, bool]:
        """Read ONLY headers of operational sheets; do not interpret any amounts."""
        specs = {
            "Заказы": ("A2:AB2", {2: "Номер Заказа", 9: "Кол-во штук"}),
            "Поставки": ("A6:X6", {1: "ID", 7: "Кол-во", 9: "Наличие"}),
            "Склад": ("A6:J6", {1: "Дата изменения", 3: "Баркод", 5: "Куплено"}),
        }
        result = {}
        for tab, (bound, expected) in specs.items():
            try:
                rows = self._ws(tab).get(bound)
                result[tab] = bool(rows) and all(val(rows[0], n) == label for n, label in expected.items())
            except Exception:
                result[tab] = False
        return result

    def check(self, today: date) -> dict[str, Any]:
        facts = audit_tables(self._read(TASK_SHEET, "A1:J", TASK_HEADERS),
                             self.reports(), self.touches(), today)
        receipts = self._read(RECEIPT_SHEET, "A1:H")
        from .core import receipt_uncertainties
        facts["receipt_rows_needing_reconciliation"] = receipt_uncertainties(receipts)
        facts["operational_header_checks"] = self._sentinel_headers()
        return facts

    def _permit(self, sheet: str) -> None:
        if not self.allow_write:
            raise WriteDisabled("Запись выключена: GOLDWAY_SHEETS_WRITE_ENABLED=false")
        if sheet not in {TASK_SHEET, REPORT_SHEET, TOUCH_SHEET}:
            raise WriteDisabled("Запрещено писать в защищённые финансовые/складские листы")

    def save_report(self, report: DailyReport) -> int:
        self._permit(REPORT_SHEET)
        rows = self.reports()
        ws = self._ws(REPORT_SHEET)
        for row_num, row in enumerate(rows[1:], start=2):
            if sheet_day(val(row, 0)) == report.day:
                if val(row, 10).casefold() in {"true", "истина", "да", "1"}:
                    raise ValidationError("Отчёт за эту дату уже подтверждён; повторная запись запрещена")
                if any(val(row, index) for index in range(1, 10)):
                    raise ValidationError("В дате уже есть данные: требуется ручная сверка, чтобы не затереть их")
                ws.update(range_name=f"B{row_num}:K{row_num}",
                          values=[report.sheet_values()], value_input_option="RAW")
                return row_num
        ws.append_row([report.day.isoformat()] + report.sheet_values(), value_input_option="RAW", table_range="A:K")
        return len(rows) + 1

    def add_touch(self, created: date, next_touch: date, client: str, reason: str, order: str, author_role: str) -> None:
        self._permit(TOUCH_SHEET)
        if author_role != "alen":
            raise ValidationError("Касания в пилоте вводит только Ален")
        if next_touch < created:
            raise ValidationError("Дата следующего касания раньше текущей — проверьте дату")
        self.touches()  # pre-write schema validation
        self._ws(TOUCH_SHEET).append_row([created.isoformat(), client, reason,
                                         "Ожидает", next_touch.isoformat(), order,
                                         "", "Ален", ""], value_input_option="RAW", table_range="A:I")

    def close_touch(self, row_num: int, proof: str, author_role: str) -> None:
        self._permit(TOUCH_SHEET)
        if author_role != "alen":
            raise ValidationError("Только Ален может закрывать касания в пилоте")
        if len(proof.strip()) < 5:
            raise ValidationError("Укажите фактический результат контакта (не менее 5 символов)")
        rows = self.touches()
        if row_num < 2 or row_num > len(rows) or not val(rows[row_num - 1], 1):
            raise ValidationError("Строка касания не найдена")
        if val(rows[row_num - 1], 3).casefold() in TOUCH_DONE:
            raise ValidationError("Касание уже закрыто")
        self._ws(TOUCH_SHEET).batch_update([
            {"range": f"D{row_num}", "values": [["Закрыто"]]},
            {"range": f"I{row_num}", "values": [[proof.strip()]]}
        ], value_input_option="RAW")

    def update_task(self, task_id: int, new_status: str, evidence: str, author_role: str) -> None:
        self._permit(TASK_SHEET)
        if author_role not in {"manager", "admin"}:
            raise ValidationError("Изменение статуса подтверждает руководитель")
        if new_status not in TASK_STATUSES:
            raise ValidationError("Недопустимый статус")
        if new_status == "Выполнено" and not evidence.strip():
            raise ValidationError("Для закрытия нужно доказательство / ссылка")
        task = next((t for t in self.tasks() if t.number == task_id), None)
        if task is None:
            raise ValidationError("Задача не найдена")
        if task.evidence and evidence.strip() and task.evidence != evidence.strip():
            raise ValidationError("В таблице уже есть доказательство; нельзя затирать его автоматически")
        changes = [{"range": f"G{task.row_number}", "values": [[new_status]]}]
        if evidence.strip() and not task.evidence:
            changes.append({"range": f"I{task.row_number}", "values": [[evidence.strip()]]})
        self._ws(TASK_SHEET).batch_update(changes, value_input_option="RAW")
