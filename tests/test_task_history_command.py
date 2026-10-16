"""Actual private Engine command→monthly metadata report→protected outbox."""
import json
import pytest
from test_agenda_command import case
from goldway.task_history_command import is_history_command
from goldway.task_transition_history import snapshot,append_transition


def seed(e,s,id='synthetic-task'):
    with s.transaction():
        s.execute('''INSERT INTO tasks(business,id,title,owner,deadline,next_step,criterion,status,source_id,created)
         VALUES(?,?,?,?,?,?,?,?,?,?)''',('GOLDWAY',id,'SYNTHETIC_PRIVATE_TITLE','test_operator','2026-10-19','synthetic','synthetic','confirmed','source',e.now()))
        append_transition(s,'GOLDWAY',id,None,at=e.now(),source='reply_bot',creation='new')
        before=snapshot(s,'GOLDWAY',id)
        s.execute("UPDATE tasks SET status='completed',revision=revision+1 WHERE business='GOLDWAY' AND id=?",(id,))
        append_transition(s,'GOLDWAY',id,before,at=e.now(),source='reply_bot')


def reply(s):
    return s.rows("SELECT * FROM outbox WHERE dedupe='task-history:cmd'")


@pytest.mark.parametrize(('text','channel'),[('Покажи статистику задач за этот месяц','bot'),('/task_stats 2026-10','business')])
def test_actual_report_pipeline_current_metadata_no_model_or_refresh(case,monkeypatch,text,channel):
    e,s=case;seed(e,s)
    from goldway import monthly_task_history as m
    actual=m.build_monthly_task_history;seen=[]
    def observed(*args,**kw):
        value=actual(*args,**kw);seen.append(value);return value
    monkeypatch.setattr(m,'build_monthly_task_history',observed)
    before=[tuple(x) for x in s.rows('SELECT * FROM tasks')]
    assert e.reply('cmd','test_operator',text,channel=channel)
    rows=reply(s);assert len(rows)==1 and len(seen)==1
    assert rows[0]['text']=='\n'.join(seen[0].lines)
    assert rows[0]['preferred_transport']==channel
    assert 'SYNTHETIC_PRIVATE_TITLE' not in rows[0]['text']
    assert len(rows[0]['text'].encode('utf-16-le'))//2<=3500
    acl=s.execute('SELECT evidence_ids FROM intelligence_outbox_acl WHERE outbox_id=?',(rows[0]['id'],)).fetchone()
    assert acl and set(json.loads(acl[0]))==set(seen[0].evidence_ids)
    assert before==[tuple(x) for x in s.rows('SELECT * FROM tasks')]
    assert not s.rows('SELECT * FROM usage')
    assert s.execute("SELECT state FROM events WHERE id='cmd'").fetchone()[0]=='done'
    assert not e.reply('cmd','test_operator',text,channel=channel) and len(reply(s))==1


@pytest.mark.parametrize(('sender','channel'),[('alen','bot'),('test_operator','group'),('test_operator',None),('outsider','bot')])
def test_admin_private_gate_does_not_read_metadata(case,sender,channel):
    e,s=case;sql=[];s.db.set_trace_callback(sql.append)
    e.reply('cmd',sender,'/task_stats',channel=channel)
    s.db.set_trace_callback(None)
    assert not reply(s) and not any(x.lstrip().upper().startswith('SELECT') for x in sql)


@pytest.mark.parametrize('text',['/task_stats tomorrow','/task_stats 2026-13','расскажи про /task_stats','Покажи статистику задач за этот месяц и создай поручения'])
def test_finite_request_matching(text):
    assert not is_history_command(text)


@pytest.mark.parametrize('text',['/task_stats 0000-01','/task_stats 9999-12'])
def test_invalid_or_future_month_safe_failure_before_sample_read(case,text):
    e,s=case;sql=[];s.db.set_trace_callback(sql.append)
    e.reply('cmd','test_operator',text,channel='bot');s.db.set_trace_callback(None)
    assert 'не удалось подтвердить' in reply(s)[0]['text']
    assert not any('FROM tasks' in q for q in sql)


def test_fixed_unavailable_does_not_echo_errors_or_partial_facts(case,monkeypatch):
    e,s=case
    from goldway import monthly_task_history as m
    def failed(*a,**k):
        exc=m.MonthlyHistoryUnavailable();exc.args=('SYNTHETIC_SECRET',);raise exc
    monkeypatch.setattr(m,'build_monthly_task_history',failed)
    e.reply('cmd','test_operator','/task_stats',channel='bot')
    assert 'SYNTHETIC_SECRET' not in reply(s)[0]['text']
    assert 'не удалось подтвердить' in reply(s)[0]['text']


def test_existing_delivery_acl_blocks_removed_reader(case):
    e,s=case;seed(e,s)
    e.reply('cmd','test_operator','/task_stats',channel='bot')
    from goldway.intelligence import delivery_permitted
    row=reply(s)[0]
    assert delivery_permitted(s,'GOLDWAY','test_operator',row['id'],e.now(),e.c)
    e.c['admins']=[];del e.c['employees']['test_operator']
    assert not delivery_permitted(s,'GOLDWAY','test_operator',row['id'],e.now(),e.c)


def test_default_request_uses_bounded_explicit_sample_not_archive_total(case,monkeypatch):
    e,s=case
    for i in range(70):
        s.execute('''INSERT INTO tasks(business,id,title,owner,deadline,next_step,criterion,status,source_id,created)
         VALUES(?,?,?,?,?,?,?,?,?,?)''',('GOLDWAY',f'task{i:03}','PRIVATE_ARCHIVE_TITLE','test_operator',None,'step','criterion','confirmed','source',e.now()))
    from goldway import monthly_task_history as m
    actual=m.build_monthly_task_history;seen=[]
    def observe(*args,**kw):
        seen.append(kw['task_ids']);return actual(*args,**kw)
    monkeypatch.setattr(m,'build_monthly_task_history',observe)
    e.reply('cmd','test_operator','/task_stats',channel='bot')
    assert len(seen)==1 and len(seen[0])==64
    assert 'PRIVATE_ARCHIVE_TITLE' not in reply(s)[0]['text']


def test_oversized_task_identifier_not_transferred_as_report_input(case,monkeypatch):
    e,s=case
    s.execute('''INSERT INTO tasks(business,id,title,owner,next_step,criterion,status,source_id,created)
      VALUES(?,?,?,?,?,?,?,?,?)''',('GOLDWAY','A'*1000,'PRIVATE','test_operator','step','criterion','confirmed','source',e.now()))
    from goldway import monthly_task_history as m
    actual=m.build_monthly_task_history;seen=[]
    def observe(*args,**kw):
        seen.append(kw['task_ids']);return actual(*args,**kw)
    monkeypatch.setattr(m,'build_monthly_task_history',observe)
    e.reply('cmd','test_operator','/task_stats',channel='bot')
    assert seen==[(None,)]
    assert 'не удалось подтвердить' in reply(s)[0]['text']
    assert 'A'*161 not in reply(s)[0]['text']
