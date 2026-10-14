"""Telegram long-polling worker. No webhook or public HTTP endpoint needed."""
from __future__ import annotations
import asyncio
from datetime import datetime
import logging
from zoneinfo import ZoneInfo

from .config import Settings, load_settings
from .core import (ValidationError, goods_contact, iso_day,
                   owner_can_report, parse_report_payload, parse_touch_payload,
                   task_summary, touches_due, report_filled)
from .notifications import NotificationLog
from .sheets import SheetsGateway, WriteDisabled

log = logging.getLogger("goldway")


def now_date(settings: Settings):
    return datetime.now(ZoneInfo(settings.timezone)).date()


def summarize(check: dict) -> str:
    states = ", ".join(f"{k}: {v}" for k, v in check["statuses"].items())
    schemas = check.get("operational_header_checks", {})
    schemas_text = ", ".join(f"{k}: {'OK' if v else 'ТРЕБУЕТ ПРОВЕРКИ'}" for k, v in schemas.items())
    return (f"GOLDWAY / проверка\n"
            f"Задач: {check['tasks']} ({states})\n"
            f"Без строгого срока: {check['tasks_without_strict_date']}\n"
            f"Касаний зарегистрировано: {check['touches_registered']}\n"
            f"Касаний с датой <= сегодня и не закрытых: {check['touches_due']}\n"
            f"Отчёт Алена за сегодня: {'заполнен' if check['alen_report_filled_today'] else 'нет подтверждения'}\n"
            f"Строк приёмки с неопределённым сопоставлением: {check['receipt_rows_needing_reconciliation']}\n"
            f"Заголовки Заказы/Поставки/Склад: {schemas_text}\n"
            f"Склад, оплата, веса и остатки не менялись.")


async def send_to_recipient(journal, bot, key, chat_id, text):
    try:
        return await journal.send_once(bot, key, chat_id, text)
    except Exception as exc:
        # Isolate recipient errors; do not log tokens, text or exception URLs.
        log.warning("Notification failed for recipient %s (%s)", chat_id, type(exc).__name__)
        return False


async def digest(bot, sheets: SheetsGateway, conf: Settings, journal: NotificationLog, slot: str):
    """No automatic messages until explicitly enabled. Never infer payments or inventories."""
    if not conf.auto_send_enabled:
        return
    today = now_date(conf)
    snapshot = await asyncio.to_thread(sheets.check, today)
    text = summarize(snapshot)
    for member in conf.members.values():
        if member.role in {"admin", "manager"}:
            await send_to_recipient(journal, bot, f"check:{today}:{slot}:{member.telegram_id}", member.telegram_id, text)
    # Only Alen handles customer follow-ups; no contact-list broadcast to Nastya.
    alen = conf.first_with_role("alen")
    if alen and slot in conf.digest_times[:2]:
        rows = await asyncio.to_thread(sheets.touches)
        due = touches_due(rows, today)
        if due:
            lines = [f"Повторные касания: {len(due)} требуют подтверждённого действия."]
            for row in due[:10]:
                lines.append(f"Строка {row['row']}: {row['client']} / срок {row['due']} / {row['reason']}")
            if len(due) > 10:
                lines.append(f"И ещё {len(due) - 10}; смотри Касания_GOLDWAY")
            await send_to_recipient(journal, bot, f"touch:{today}:{slot}:{alen.telegram_id}", alen.telegram_id, "\n".join(lines))


async def report_reminder(bot, sheets: SheetsGateway, conf: Settings, journal: NotificationLog):
    if not conf.auto_send_enabled:
        return
    alen = conf.first_with_role("alen")
    if not alen:
        return
    today = now_date(conf)
    report_rows = await asyncio.to_thread(sheets.reports)
    if not report_filled(report_rows, today):
        await send_to_recipient(journal, bot, f"alen_report:{today}:{alen.telegram_id}", alen.telegram_id,
                                "Ален, нужен фактический отчёт за сегодня: заявки, повторные касания, ответы, заказы, оплачено €, публикации, блогеры, проблемы и план. Используй /report. Ничего не оценивай на глаз.")


def setup_handlers(dp, sheets: SheetsGateway, conf: Settings):
    from aiogram import F
    from aiogram.enums import ChatType
    from aiogram.filters import Command, CommandObject
    from aiogram.types import Message

    async def authenticate(message: Message, allowed: set[str] | None = None):
        if message.chat.type != ChatType.PRIVATE:
            await message.answer("GOLDWAY работает только в личных чатах.")
            return None
        actor = conf.member(message.from_user.id if message.from_user else None)
        if actor is None:
            await message.answer("Нет доступа. Руководитель должен добавить доступ в настройках хостинга; не отправляйте сюда токены и ID других людей.")
            return None
        if allowed is not None and actor.role not in allowed:
            await message.answer("Недостаточно прав для этой команды.")
            return None
        return actor

    @dp.message(Command("start", "help"))
    async def start(message: Message):
        actor = await authenticate(message)
        if actor is None:
            return
        help_text = ("GOLDWAY / доступ: " + actor.role + "\n"
                     "/tasks — активные задачи; /task 82 — детали\n"
                     "/check — проверка таблицы (руководитель)\n"
                     "/report — формат отчёта Алена; /report_submit — запись отчёта\n"
                     "/touches — касания; /touch_add — новое; /touch_done — завершить\n"
                     "/ask — спросить Алена/Настю о конкретном товаре (руководитель)\n"
                     "/status — статус задачи (руководитель)")
        await message.answer(help_text)

    @dp.message(Command("tasks"))
    async def tasks(message: Message):
        actor = await authenticate(message)
        if actor is None:
            return
        try:
            found = await asyncio.to_thread(sheets.tasks)
            if actor.role not in {"admin", "manager"}:
                found = [t for t in found if owner_can_report(actor.role, t.owner)]
            open_tasks = [t for t in found if t.status != "Выполнено"]
            lines = [f"Задач доступно: {len(open_tasks)} (первые 15):"]
            for task in open_tasks[:15]:
                lines.append(f"#{task.number} [{task.status}] {task.name[:85]} — {task.next_action[:70]}")
            await message.answer("\n".join(lines)[:3900])
        except Exception:
            log.exception("Read tasks failed")
            await message.answer("Таблица недоступна. Запись не выполнялась.")

    @dp.message(Command("task"))
    async def task(message: Message, command: CommandObject):
        actor = await authenticate(message)
        if actor is None:
            return
        try:
            number = int(command.args or "")
            task = next((t for t in await asyncio.to_thread(sheets.tasks) if t.number == number), None)
            if task is None or (actor.role not in {"admin", "manager"} and not owner_can_report(actor.role, task.owner)):
                await message.answer("Нет такой доступной задачи.")
                return
            await message.answer((f"#{task.number} {task.name}\nОтветственный: {task.owner}\nСтатус: {task.status}\n"
                                  f"Подтверждено: {task.fact or 'нет данных'}\nСледующее действие: {task.next_action or 'не задано'}\n"
                                  f"Срок: {task.deadline_text or 'не указан'}\nДоказательство: {task.evidence or 'нет'}")[:3900])
        except (ValueError, TypeError):
            await message.answer("Использование: /task 82")
        except Exception:
            log.exception("Read single task failed")
            await message.answer("Не удалось прочитать задачу.")

    @dp.message(Command("check"))
    async def check(message: Message):
        actor = await authenticate(message, {"admin", "manager"})
        if actor is None:
            return
        try:
            result = await asyncio.to_thread(sheets.check, now_date(conf))
            await message.answer(summarize(result))
        except Exception:
            log.exception("Check failed")
            await message.answer("Проверка не выполнена, данные не изменены.")

    @dp.message(Command("report"))
    async def report(message: Message):
        actor = await authenticate(message, {"alen"})
        if actor:
            await message.answer("Ален, вводи только известный факт. Формат:\n"
                "/report_submit 2026-10-14 | 1 | 3 | 2 | 1 | 25,50 | ссылка или нет | следующий шаг или нет | проблемы или нет | план завтра\n"
                "Поля: дата | заявки | касания | ответы | заказы | оплачено € | публикации | блогеры | проблемы | план. Неизвестное число нельзя заменять нулём. Если нет подтверждения — не отправляй отчёт.")

    @dp.message(Command("report_submit"))
    async def report_submit(message: Message, command: CommandObject):
        actor = await authenticate(message, {"alen"})
        if actor is None:
            return
        try:
            draft = parse_report_payload(command.args or "")
            if draft.day > now_date(conf):
                raise ValidationError("Отчёт за будущий день не принимается")
            await asyncio.to_thread(sheets.save_report, draft)
            await message.answer("Фактический отчёт сохранён в Ален_день; Отчёт заполнен = TRUE.")
        except (ValidationError, WriteDisabled) as exc:
            await message.answer(f"Не записано: {exc}")
        except Exception:
            log.exception("Report submit failed")
            await message.answer("Отчёт не подтверждён. Проверьте таблицу перед повторной отправкой, чтобы не дублировать факт.")

    @dp.message(Command("touches"))
    async def touches(message: Message):
        actor = await authenticate(message, {"alen", "admin", "manager"})
        if actor is None:
            return
        try:
            due = touches_due(await asyncio.to_thread(sheets.touches), now_date(conf))
            if not due:
                await message.answer("Нет просроченных записанных касаний. Это не доказывает, что клиенты не ждут ответа: проверьте полноту списка.")
                return
            lines = [f"Касаний по записи со сроком до сегодня: {len(due)}"]
            for row in due[:20]:
                lines.append(f"#{row['row']} {row['client']} — {row['due']}")
            await message.answer("\n".join(lines)[:3900])
        except Exception:
            log.exception("Read touches failed")
            await message.answer("Касания недоступны.")

    @dp.message(Command("touch_add"))
    async def touch_add(message: Message, command: CommandObject):
        actor = await authenticate(message, {"alen"})
        if actor is None:
            return
        try:
            due, client, reason, order = parse_touch_payload(command.args or "")
            await asyncio.to_thread(sheets.add_touch, now_date(conf), due, client, reason, order, actor.role)
            await message.answer("Касание зарегистрировано без выдуманных суммы и оплаты.")
        except (ValidationError, WriteDisabled) as exc:
            await message.answer(f"Не записано: {exc}\nФормат: /touch_add 2026-10-15 | клиент/ссылка | причина | ID заказа или -")
        except Exception:
            log.exception("Add touch failed")
            await message.answer("Касание не подтверждено — проверьте таблицу перед повторной отправкой.")

    @dp.message(Command("touch_done"))
    async def touch_done(message: Message, command: CommandObject):
        actor = await authenticate(message, {"alen"})
        if actor is None:
            return
        try:
            row, reason = (command.args or "").split("|", 1)
            await asyncio.to_thread(sheets.close_touch, int(row.strip()), reason.strip(), actor.role)
            await message.answer("Касание закрыто по вашему подтверждению; сумма и заказ не менялись.")
        except (ValueError, ValidationError, WriteDisabled) as exc:
            await message.answer(f"Не записано: {exc}\nФормат: /touch_done 2 | клиент ответил, следующий шаг согласован")
        except Exception:
            log.exception("Touch close failed")
            await message.answer("Не удалось подтвердить закрытие; проверьте таблицу.")

    @dp.message(Command("status"))
    async def status(message: Message, command: CommandObject):
        actor = await authenticate(message, {"admin", "manager"})
        if actor is None:
            return
        try:
            parts = [p.strip() for p in (command.args or "").split("|", 2)]
            if len(parts) != 3:
                raise ValidationError("Формат: /status 82 | Выполнено | ссылка/доказательство")
            number, state, evidence = parts
            await asyncio.to_thread(sheets.update_task, int(number), state, evidence, actor.role)
            await message.answer("Статус задачи обновлён после проверки руководителем.")
        except (ValidationError, WriteDisabled, ValueError) as exc:
            await message.answer(f"Не записано: {exc}")
        except Exception:
            log.exception("Update task failed")
            await message.answer("Подтверждение обновления не получено; проверьте задачу в таблице.")

    @dp.message(Command("ask"))
    async def ask(message: Message, command: CommandObject):
        actor = await authenticate(message, {"admin", "manager"})
        if actor is None:
            return
        try:
            stage, item = [p.strip() for p in (command.args or "").split("|", 1)]
            role = goods_contact(stage)
            if not item or len(item) > 600:
                raise ValidationError("Укажите конкретный товар (не более 600 символов)")
            target = conf.first_with_role(role)
            if target is None:
                raise ValidationError(f"Получатель {role} не настроен в защищённых переменных хостинга")
            who = "Ален" if role == "alen" else "Настя"
            prompt = (f"{who}, уточни факт по товару: {item}\n"
                      "Подтверди текущий статус, количество и основание. Если неизвестно — так и напиши. "
                      "Оплаты, веса и остатки бот не вычисляет и не меняет.")
            await message.bot.send_message(chat_id=target.telegram_id, text=prompt)
            await message.answer(f"Вопрос направлен: {who}. Это запрос факта, не подтверждение приёмки.")
        except (ValueError, ValidationError) as exc:
            await message.answer(f"Не отправлено: {exc}\nФормат: /ask sent | товар\n/ask received | товар\n/ask with_nastya | товар")
        except Exception:
            log.exception("Send question failed")
            await message.answer("Не удалось доставить вопрос. Проверьте, начал ли сотрудник личный чат с ботом.")

    @dp.message(F.text)
    async def unknown(message: Message):
        if await authenticate(message):
            await message.answer("Используйте /help. Свободный текст не интерпретируется как подтверждение наличия или оплаты.")


async def main():
    from aiogram import Bot, Dispatcher
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger
    conf = load_settings()
    if not conf.live_enabled:
        print("GOLDWAY: prelaunch gate is closed; Telegram polling was not started")
        return
    sheets = SheetsGateway.connect(conf)
    # Read and validate before interacting with Telegram.
    await asyncio.to_thread(sheets.check, now_date(conf))
    journal = NotificationLog(conf.sqlite_path)
    bot = Bot(token=conf.bot_token)
    dp = Dispatcher()
    setup_handlers(dp, sheets, conf)
    scheduler = AsyncIOScheduler(timezone=ZoneInfo(conf.timezone))
    for slot in conf.digest_times:
        hour, minute = map(int, slot.split(":"))
        scheduler.add_job(digest, CronTrigger(hour=hour, minute=minute, timezone=conf.timezone),
                          args=[bot, sheets, conf, journal, slot], id=f"digest_{slot}", max_instances=1, coalesce=True, misfire_grace_time=300)
    rh, rm = map(int, conf.report_reminder_time.split(":"))
    scheduler.add_job(report_reminder, CronTrigger(hour=rh, minute=rm, timezone=conf.timezone),
                      args=[bot, sheets, conf, journal], id="report_reminder", max_instances=1, coalesce=True, misfire_grace_time=300)
    try:
        scheduler.start()
        await dp.start_polling(bot, allowed_updates=["message"], handle_as_tasks=False)
    finally:
        scheduler.shutdown(wait=False)
        journal.close()
        await bot.session.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(main())
