from datetime import date
from decimal import Decimal
import pytest
from goldway.core import (TASK_HEADERS, TOUCH_HEADERS, REPORT_HEADERS, ValidationError,
    audit_tables, goods_contact, iso_day, owner_can_report, parse_report_payload,
    parse_touch_payload, receipt_uncertainties, report_filled, sheet_day,
    task_summary, tasks_from_rows, touches_due)

def task_row(id=1, status='В работе', owner='Ален'):
    return [str(id),'Позвонить клиенту',owner,'Факт не уточнён','Уточнить статус','Ответ',status,'','','']

def report_row(date_text='2026-10-14', filled=False):
    return [date_text, '', '', '', '', '', '', '', '', '', filled]

@pytest.fixture
def task_rows():
    return [list(TASK_HEADERS),task_row(1), task_row(2, 'К проверке', 'Настя')]

@pytest.fixture
def report_rows():
    return [list(REPORT_HEADERS),report_row()]

@pytest.fixture
def touch_rows():
    return [list(TOUCH_HEADERS),['2026-10-13','Клиент A','Уточнить','Ожидает','2026-10-14','','','Ален','']]

def test_task_valid(task_rows):
    assert [t.number for t in tasks_from_rows(task_rows)] == [1,2]

def test_summary(task_rows):
    assert task_summary(tasks_from_rows(task_rows)) == {'В работе':1,'К проверке':1}

def test_duplicate_task(task_rows):
    task_rows.append(task_row(1))
    with pytest.raises(ValidationError,match='Повторный'):
        tasks_from_rows(task_rows)

def test_invalid_task_id(task_rows):
    task_rows.append(task_row(-1))
    with pytest.raises(ValidationError):
        tasks_from_rows(task_rows)

def test_unknown_task_status(task_rows):
    task_rows.append(task_row(3,'Закуплено'))
    with pytest.raises(ValidationError,match='статус'):
        tasks_from_rows(task_rows)

def test_changed_task_header(task_rows):
    task_rows[0][2]='Владелец'
    with pytest.raises(ValidationError,match='Структура'):
        tasks_from_rows(task_rows)

def test_empty_task_title(task_rows):
    task_rows[1][1]=''
    with pytest.raises(ValidationError):
        tasks_from_rows(task_rows)

@pytest.mark.parametrize('text,valid',[('2026-10-14',True),('14.10.2026',False),('скоро',False),('срочно',False),('2026-02-30',False),('',False),('20261014',False)])
def test_iso_day(text,valid):
    assert (iso_day(text) is not None)==valid

def test_sheet_date_dot():
    assert sheet_day('14.10.2026') == date(2026,10,14)

def test_owner_privacy():
    assert owner_can_report('alen','Я + Ален')
    assert owner_can_report('nastya','Настя')
    assert not owner_can_report('nastya','Я + Ален')
    assert not owner_can_report('alen','Я')
    assert owner_can_report('admin','Я')

@pytest.mark.parametrize('stage,role',[('sent','alen'),('received','alen'),('with_nastya','nastya'),('отправлено','alen'),('получено','alen')])
def test_goods_route(stage,role):
    assert goods_contact(stage)==role

def test_unknown_stage_not_guessed():
    with pytest.raises(ValidationError):
        goods_contact('in_transit')

def test_due_contacts(touch_rows):
    due=touches_due(touch_rows,date(2026,10,14))
    assert len(due)==1 and due[0]['row']==2

def test_future_contact_not_due(touch_rows):
    assert not touches_due(touch_rows,date(2026,10,13))

def test_closed_contact_not_due(touch_rows):
    touch_rows[1][3]='Закрыто'
    assert not touches_due(touch_rows,date(2026,10,14))

def test_undated_contact_not_due(touch_rows):
    touch_rows[1][4]='завтра'
    assert not touches_due(touch_rows,date(2026,10,14))

def test_report_not_filled(report_rows):
    assert not report_filled(report_rows,date(2026,10,14))

def test_report_filled(report_rows):
    report_rows[1][10]='TRUE'
    assert report_filled(report_rows,date(2026,10,14))

def test_report_valid():
    r=parse_report_payload('2026-10-14 | 1 | 2 | 3 | 4 | 15,25 | нет | договорились | нет | позвонить')
    assert (r.day, r.followups, r.paid_eur)==(date(2026,10,14),2,Decimal('15.25'))
    assert r.sheet_values()[4]==15.25

@pytest.mark.parametrize('data',[ 
    '2026-10-14 | 1 | 2 | 3 | 4 | | нет | нет | нет | нет',
    '2026-10-14 | 1 | 2 | 3 | -1 | 15 | нет | нет | нет | нет',
    '2026-10-14 | 1 | 2 | 3 | 4 | -15 | нет | нет | нет | нет',
    '2026-10-14 | 1 | 2 | 3 | 4 | 15.333 | нет | нет | нет | нет',
    '2026-10-14 | 1 | 2 | 3 | 4 | NaN | нет | нет | нет | нет',
    'завтра | 1 | 2 | 3 | 4 | 15 | нет | нет | нет | нет',
])
def test_bad_report_rejected(data):
    with pytest.raises(ValidationError):
        parse_report_payload(data)

def test_touch_payload():
    day, client, reason, order=parse_touch_payload('2026-10-15 | ссылка | уточнить | -')
    assert day==date(2026,10,15) and client=='ссылка' and order==''

def test_bad_touch_payload():
    with pytest.raises(ValidationError):
        parse_touch_payload('завтра | клиент | уточнить | -')

def test_receipt_uncertainty():
    rows=[['headers']*8, ['', '', '', '', '', '', 'Не распределено: требуется сопоставление'],['']*6+['Ранее на складе']]
    assert receipt_uncertainties(rows)==1

def test_audit_no_fabrication(task_rows,report_rows,touch_rows):
    result=audit_tables(task_rows,report_rows,touch_rows,date(2026,10,14))
    assert result['tasks']==2
    assert result['touches_registered']==1
    assert result['tasks_without_strict_date']==2
    assert result['alen_report_filled_today'] is False
