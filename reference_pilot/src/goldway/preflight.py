"""Read-only preflight. No BotFather token and no Telegram traffic required."""
from __future__ import annotations
from datetime import datetime
import json
from zoneinfo import ZoneInfo
from .config import load_settings
from .sheets import SheetsGateway


def main() -> None:
    settings = load_settings()
    if settings.sheets_write_enabled or settings.auto_send_enabled or settings.live_enabled:
        raise SystemExit("Preflight requires all LIVE/WRITE/AUTO_SEND switches = false")
    if not (settings.service_account_file or settings.service_account_json):
        raise SystemExit("Missing Google service-account credentials in secure environment")
    gateway = SheetsGateway.connect(settings)
    today = datetime.now(ZoneInfo(settings.timezone)).date()
    result = gateway.check(today)
    print(json.dumps({"date":today.isoformat(), "spreadsheet_id":settings.spreadsheet_id, **result},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
