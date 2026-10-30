from datetime import datetime
from zoneinfo import ZoneInfo
import pytest
from goldway.work_voice import daily_opening


def at(hour, day=30):
    return datetime(2026,10,day,hour,tzinfo=ZoneInfo('Europe/Moscow'))


@pytest.mark.parametrize('hour',[0,8,18,23])
def test_quiet_hours_do_not_gain_greeting(hour):
    assert daily_opening('Один рабочий вопрос?',at(hour),False)=='Один рабочий вопрос?'


def test_only_first_useful_message_gets_opening():
    text='Алэн, получилось сделать повторные касания?'
    result=daily_opening(text,at(10),False)
    assert result.endswith(text) and len(result)>len(text)
    assert daily_opening(text,at(10),True)==text
    assert result.count('?')==1


def test_no_standalone_or_oversized_greeting():
    assert daily_opening('',at(10),False)==''
    assert daily_opening('x'*450,at(10),False)=='x'*450


def test_choice_stable_within_day_but_varies():
    assert daily_opening('Вопрос?',at(10),False)==daily_opening('Вопрос?',at(14),False)
    assert daily_opening('Вопрос?',at(10,29),False)!=daily_opening('Вопрос?',at(10,30),False)
