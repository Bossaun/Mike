"""Reproduce production range truncation and per-recipient failure."""
import asyncio
import re
from datetime import date
from dataclasses import replace
import pytest
from test_sheets import Worksheet, book
from goldway.core import TASK_HEADERS, REPORT_HEADERS, TOUCH_HEADERS, parse_report_payload, ValidationError
from goldway.sheets import SheetsGateway
from goldway.config import load_settings, Member
from goldway.notifications import NotificationLog
from goldway.bot import digest

class RealRangeWorksheet(Worksheet):
    def get(self,bound):
        match=re.fullmatch(r'[A-Z]+(\d+):[A-Z]+(\d+)?',bound)
        if not match: raise AssertionError('Unexpected range: '+bound)
        start=int(match[1])-1
        end=int(match[2]) if match[2] else len(self.rows)
        return self.rows[start:end]
    def get_all_values(self): return self.rows

def test_all_tasks_including_row_151(book):
    rows=[list(TASK_HEADERS)]+[[str(i),'Task '+str(i),'Ален','','step','result','В работе','','',''] for i in range(1,151)]
    book.items['Задачи_GOLDWAY']=RealRangeWorksheet(rows)
    found=SheetsGateway(book).tasks()
    assert len(found)==150 and found[-1].number==150

def test_check_counts_tasks_after_row_150(book):
    book.items['Задачи_GOLDWAY']=RealRangeWorksheet([list(TASK_HEADERS)]+[[str(i),'Task','Ален','','step','result','В работе','','',''] for i in range(1,151)])
    assert SheetsGateway(book).check(date(2026,10,14))['tasks']==150

def test_confirmed_report_in_row_201_prevents_duplicate(book):
    rows=[list(REPORT_HEADERS)]+[['2025-01-01']+['']*10 for _ in range(199)]+[['2026-10-14']+['1']*9+['TRUE']]
    ws=RealRangeWorksheet(rows);book.items['Ален_день']=ws
    with pytest.raises(ValidationError,match='уже подтверждён'):
        SheetsGateway(book,allow_write=True).save_report(parse_report_payload('2026-10-14 | 1 | 2 | 3 | 4 | 5 | нет | нет | нет | нет'))
    assert not ws.appended and not ws.updates

def test_touch_in_row_201_is_read_and_can_close(book):
    rows=[list(TOUCH_HEADERS)]+[['']*9 for _ in range(199)]+[['2026-10-13','Клиент','Причина','Ожидает','2026-10-14','','','Ален','']]
    ws=RealRangeWorksheet(rows);book.items['Касания_GOLDWAY']=ws
    gateway=SheetsGateway(book,allow_write=True)
    assert len(gateway.touches())==201
    gateway.close_touch(201,'Клиент подтвердил результат','alen')
    assert ws.updates[-1][0][0]['range']=='D201'

class RecipientBot:
    def __init__(self): self.calls=[]
    async def send_message(self,chat_id,text):
        self.calls.append(chat_id)
        if chat_id==1: raise RuntimeError('Recipient failure')

def test_digest_continues_after_first_recipient_failure(book,tmp_path):
    conf=replace(load_settings(),auto_send_enabled=True,
        members={1:Member(1,'admin','Первый'),2:Member(2,'manager','Второй')})
    bot=RecipientBot();journal=NotificationLog(str(tmp_path/'journal.sqlite'))
    try:
        asyncio.run(digest(bot,SheetsGateway(book),conf,journal,'test'))
        assert bot.calls==[1,2]
        assert any(str(row[0]).endswith(':2') for row in journal.connection.execute('SELECT key FROM messages'))
    finally: journal.close()

def test_ambiguous_send_is_not_retried_on_restart(tmp_path):
    path=str(tmp_path/'journal.sqlite');journal=NotificationLog(path);bot=RecipientBot()
    with pytest.raises(RuntimeError): asyncio.run(journal.send_once(bot,'uncertain',1,'text'))
    journal.close();journal=NotificationLog(path)
    assert asyncio.run(journal.send_once(bot,'uncertain',1,'text')) is False
    assert bot.calls==[1]
    assert journal.connection.execute('SELECT state FROM attempts WHERE key=?',('uncertain',)).fetchone()[0]=='unknown'
    journal.close()
