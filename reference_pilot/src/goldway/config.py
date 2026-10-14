"""Environment-only configuration: never request or print secrets in Telegram/chat."""
from __future__ import annotations
from dataclasses import dataclass
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

class ConfigurationError(ValueError):
    pass


def flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name, "true" if default else "false").strip().lower()
    if value not in {"true", "false", "1", "0", "yes", "no"}:
        raise ConfigurationError(f"{name} must be true/false")
    return value in {"true", "1", "yes"}


@dataclass(frozen=True)
class Member:
    telegram_id: int
    role: str
    label: str


@dataclass(frozen=True)
class Settings:
    bot_token: str
    spreadsheet_id: str
    timezone: str
    service_account_file: str
    service_account_json: str
    members: dict[int, Member]
    live_enabled: bool
    sheets_write_enabled: bool
    auto_send_enabled: bool
    sqlite_path: str
    digest_times: tuple[str, ...]
    report_reminder_time: str

    def member(self, user_id: int | None) -> Member | None:
        return self.members.get(user_id) if user_id else None

    def first_with_role(self, role: str) -> Member | None:
        return next((m for m in self.members.values() if m.role == role), None)


def _clock(text: str) -> str:
    import datetime
    try:
        datetime.datetime.strptime(text, "%H:%M")
        if len(text) != 5 or text[2] != ":":
            raise ValueError
    except ValueError as exc:
        raise ConfigurationError("Reminder time must be HH:MM") from exc
    return text


def load_settings() -> Settings:
    from .core import SPREADSHEET_ID
    raw = os.getenv("GOLDWAY_MEMBERS_JSON", "{}")
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError
        members: dict[int, Member] = {}
        for identifier, data in parsed.items():
            user_id = int(identifier)
            if user_id <= 0 or not isinstance(data, dict):
                raise ValueError
            role = str(data.get("role", "")).strip().lower()
            if role not in {"admin", "manager", "alen", "nastya"}:
                raise ValueError
            members[user_id] = Member(user_id, role, str(data.get("label", role))[:60])
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ConfigurationError("Invalid GOLDWAY_MEMBERS_JSON role mapping") from exc
    timezone = os.getenv("GOLDWAY_TIMEZONE", "Europe/Moscow")
    try:
        ZoneInfo(timezone)
    except Exception as exc:
        raise ConfigurationError("Invalid GOLDWAY_TIMEZONE") from exc
    times = tuple(_clock(t.strip()) for t in os.getenv("GOLDWAY_DIGEST_TIMES", "09:00,14:00,18:00").split(","))
    live = flag("GOLDWAY_LIVE_ENABLED")
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if live and not token:
        raise ConfigurationError("Configure TELEGRAM_BOT_TOKEN in hosting secrets")
    if live and not members:
        raise ConfigurationError("Configure GOLDWAY_MEMBERS_JSON in hosting secrets")
    if live and not any(m.role == "admin" for m in members.values()):
        raise ConfigurationError("At least one verified admin role is required")
    sid = os.getenv("GOLDWAY_SPREADSHEET_ID", SPREADSHEET_ID)
    if sid != SPREADSHEET_ID:
        raise ConfigurationError("Unverified target spreadsheet ID; stop before accessing another workbook")
    account_file = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
    account_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if live and not (account_file or account_json):
        raise ConfigurationError("Configure GOOGLE_SERVICE_ACCOUNT_JSON or GOOGLE_APPLICATION_CREDENTIALS securely")
    if account_file and not Path(account_file).is_file() and live:
        raise ConfigurationError("Service-account file not found")
    write = flag("GOLDWAY_SHEETS_WRITE_ENABLED")
    outbound = flag("GOLDWAY_AUTO_SEND_ENABLED")
    if write and not live:
        raise ConfigurationError("Sheet writes cannot be enabled before live gate")
    if outbound and not live:
        raise ConfigurationError("Automatic sends cannot be enabled before live gate")
    return Settings(token, sid, timezone, account_file, account_json,
                    members, live, write, outbound,
                    os.getenv("GOLDWAY_SQLITE_PATH", "/data/notifications.sqlite3"),
                    times, _clock(os.getenv("GOLDWAY_REPORT_REMINDER_TIME", "18:30")))
