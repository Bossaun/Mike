from datetime import date
import pytest
from goldway.core import TASK_HEADERS, TOUCH_HEADERS, REPORT_HEADERS, parse_report_payload, ValidationError
from goldway.sheets import SheetsGateway, WriteDisabled

class Worksheet:
    def __init__(self, rows):
        self.rows=[list(row) for row in rows]
        self.updates=[]
        self.appended=[]
    def get(self, _): return self.rows
    def update(self, **kwargs): self.updates.append(kwargs)
    def append_row(self, row, **kwargs): self.appended.append((row, kwargs))
    def batch_update(self, changes, **kwargs): self.updates.append((changes,kwargs))

class Book:
    id='17vpYrUUjjTxAhQeLA_uHq9XceNe22X81GL9e7pBPgrw'
    def __init__(self):
        self.items={
            'Задачи_GOLDWAY':Worksheet([list(TASK_HEADERS),['82','Облачный менеджер','Я + разработчик','Пилот','Запуск','Сервис','В работе','','','']]),
            'Ален_день':Worksheet([list(REPORT_HEADERS),['2026-10-14','','','','','','','','','','FALSE']]),
            'Касания_GOLDWAY':Worksheet([list(TOUCH_HEADERS),['2026-10-13','Клиент','Причина','Ожидает','2026-10-14','','','Ален','']]),
            'Приемка_14_10':Worksheet([['№','Товар','Количество','Единица','Получение','Строки','Сопоставление','Основание'],['1','Товар','','','','','Требуется сопоставление','']]),
            'Заказы':Worksheet([['','', 'Номер Заказа','','','','','','','Кол-во штук']]),
            'Поставки':Worksheet([['','ID','','','','','','Кол-во','','Наличие']]),
            'Склад':Worksheet([['','Дата изменения','','Баркод','','Куплено']]),
        }
    def worksheet(self, name): return self.items[name]

@pytest.fixture
def book(): return Book()

def test_wrong_workbook_refused(book):
    book.id='another-sheet'
    with pytest.raises(ValidationError):
        SheetsGateway(book)

def test_readonly_default(book):
    gateway=SheetsGateway(book)
    assert len(gateway.tasks())==1
    with pytest.raises(WriteDisabled):
        gateway.save_report(parse_report_payload('2026-10-14 | 1 | 2 | 3 | 4 | 5 | нет | нет | нет | нет'))

def test_write_allowlist(book):
    gateway=SheetsGateway(book, allow_write=True)
    with pytest.raises(WriteDisabled):
        gateway._permit('Поставки')
    with pytest.raises(WriteDisabled):
        gateway._permit('Заказы')

def test_save_report_changes_only_b_to_k(book):
    gateway=SheetsGateway(book,allow_write=True)
    draft=parse_report_payload('2026-10-14 | 1 | 2 | 3 | 4 | 5 | нет | нет | нет | нет')
    gateway.save_report(draft)
    updates=book.items['Ален_день'].updates
    assert updates[0]['range_name']=='B2:K2'
    assert updates[0]['value_input_option']=='RAW'
    assert updates[0]['values'][0][-1] is True

def test_no_report_overwrite(book):
    book.items['Ален_день'].rows[1][-1]='TRUE'
    gateway=SheetsGateway(book,allow_write=True)
    with pytest.raises(ValidationError,match='уже подтверждён'):
        gateway.save_report(parse_report_payload('2026-10-14 | 1 | 2 | 3 | 4 | 5 | нет | нет | нет | нет'))

def test_no_partial_report_overwrite(book):
    book.items['Ален_день'].rows[1][1]='1'
    gateway=SheetsGateway(book,allow_write=True)
    with pytest.raises(ValidationError,match='ручная сверка'):
        gateway.save_report(parse_report_payload('2026-10-14 | 1 | 2 | 3 | 4 | 5 | нет | нет | нет | нет'))

def test_add_touch_for_alen_only(book):
    gateway=SheetsGateway(book, allow_write=True)
    with pytest.raises(ValidationError):
        gateway.add_touch(date(2026,10,14),date(2026,10,15),'Клиент','Причина','','nastya')
    gateway.add_touch(date(2026,10,14),date(2026,10,15),'Клиент','Причина','','alen')
    new=book.items['Касания_GOLDWAY'].appended[0][0]
    assert new[6]=='' and new[7]=='Ален'

def test_touch_cannot_be_before_today(book):
    with pytest.raises(ValidationError):
        SheetsGateway(book,allow_write=True).add_touch(date(2026,10,14),date(2026,10,13),'Клиент','Причина','','alen')

def test_touch_close_requires_proof(book):
    gateway=SheetsGateway(book,allow_write=True)
    with pytest.raises(ValidationError):
        gateway.close_touch(2,'нет','alen')
    gateway.close_touch(2,'клиент ответил','alen')
    updates=book.items['Касания_GOLDWAY'].updates[-1][0]
    assert [u['range'] for u in updates] == ['D2','I2']

def test_status_manager_only(book):
    gateway=SheetsGateway(book,allow_write=True)
    with pytest.raises(ValidationError):
        gateway.update_task(82,'Выполнено','ссылка','alen')
    gateway.update_task(82,'Выполнено','ссылка','manager')
    updates=book.items['Задачи_GOLDWAY'].updates[-1][0]
    assert [u['range'] for u in updates]==['G2','I2']

def test_no_evidence_overwrite(book):
    book.items['Задачи_GOLDWAY'].rows[1][8]='old link'
    with pytest.raises(ValidationError):
        SheetsGateway(book,allow_write=True).update_task(82,'Выполнено','new link','admin')

def test_audit_receipt_counts(book):
    assert SheetsGateway(book).check(date(2026,10,14))['receipt_rows_needing_reconciliation']==1


def test_operational_headers_read_only(book):
    checked=SheetsGateway(book).check(date(2026,10,14))
    assert checked['operational_header_checks']=={'Заказы':True,'Поставки':True,'Склад':True}
    assert not book.items['Заказы'].updates and not book.items['Заказы'].appended


def test_operational_header_warning_when_changed(book):
    book.items['Склад'].rows[0][3]='Штрихкод'
    checked=SheetsGateway(book).check(date(2026,10,14))
    assert checked['operational_header_checks']['Склад'] is False
