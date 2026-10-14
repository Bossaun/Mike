"""Local notification deduplication. Mount a persistent volume for the SQLite file."""
from __future__ import annotations
from pathlib import Path
import sqlite3
from typing import Protocol

class Sender(Protocol):
    async def send_message(self, chat_id: int, text: str) -> object: ...


class NotificationLog:
    def __init__(self, path: str):
        file = Path(path)
        file.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(file))
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("CREATE TABLE IF NOT EXISTS messages (key TEXT PRIMARY KEY, sent_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
        self.connection.execute("CREATE TABLE IF NOT EXISTS attempts (key TEXT PRIMARY KEY, state TEXT NOT NULL, error_type TEXT)")
        self.connection.execute("UPDATE attempts SET state='unknown' WHERE state='sending'")
        self.connection.commit()

    def was_sent(self, key: str) -> bool:
        return self.connection.execute("SELECT 1 FROM messages WHERE key = ?", (key,)).fetchone() is not None

    def record(self, key: str) -> None:
        self.connection.execute("INSERT OR IGNORE INTO messages(key) VALUES (?)", (key,))
        self.connection.commit()

    async def send_once(self, sender: Sender, key: str, chat_id: int, text: str) -> bool:
        # Durable claim before network I/O; interrupted/ambiguous sends are never blindly retried.
        if self.was_sent(key):
            return False
        claimed=self.connection.execute("INSERT OR IGNORE INTO attempts(key,state) VALUES (?, 'sending')",(key,)).rowcount
        self.connection.commit()
        if not claimed:
            return False
        try:
            await sender.send_message(chat_id=chat_id, text=text[:3900])
        except BaseException as exc:
            state='unknown'
            try:
                from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramUnauthorizedError, TelegramRetryAfter
                if isinstance(exc,(TelegramBadRequest,TelegramForbiddenError,TelegramUnauthorizedError,TelegramRetryAfter)):
                    state='rejected'
            except ImportError:
                pass
            self.connection.execute("UPDATE attempts SET state=?,error_type=? WHERE key=?",(state,type(exc).__name__,key))
            self.connection.commit()
            raise
        self.connection.execute("UPDATE attempts SET state='sent' WHERE key=?",(key,))
        self.record(key)
        return True

    def close(self) -> None:
        self.connection.close()
