"""Synthetic actual scheduler/read-only overview/Engine command boundary only."""
from datetime import datetime
import json
from zoneinfo import ZoneInfo
import pytest
from goldway.engine import Engine
from goldway.store import Store
from goldway.reminder_overview import build_reminder_overview,is_reminder_overview,OverviewUnavailable
from goldway.intelligence import reader_key,delivery_permitted
from goldway.work_context import work_id

MEMBERS=['test_operator','frost','alen','nastya','dasha_it']
NOW=datetime(2026,10,16,18,tzinfo=ZoneInfo('Europe/Moscow')).timestamp()
PRIVATE='SYNTHETIC_PRIVATE_TITLE_SOURCE_EVIDENCE_ID'

@pytest.fixture
def case(monkeypatch):
    s=Store(':memory:')
    c={'business':'GOLDWAY','timezone':'Europe/Moscow',
       'employees':{x:{'name':x,'telegram_user_id':i+1} for i,x in enumerate(MEMBERS)},
       'admins':['test_operator'],'business_account_user_id':99,'team_intake':{'recipients':MEMBERS[:],'started_at':100},
       'allowed_live_recipients':MEMBERS[:],'team_roster_verified':True,'recipients_verified':True,
       'live_send':False,'review_mode':True,'sheets_write':False,'daily_summary':False,
       'allow_onboarding_replies':False,'allow_registered_replies':False,
       'allowed_group_ids':[],'group_destinations':{},'meeting_recipient':None,
       'scheduling':{'enabled':True},'intelligence':{'enabled':True,'sources':[]}}
    class NoModel:
        def propose(self,*a,**kw):pytest.fail('overview called model')
    e=Engine(s,c,NoModel(),lambda:NOW)
    import builtins,socket,httpx
    def forbidden(*a,**kw):pytest.fail('unexpected I/O or scheduler tick')
    monkeypatch.setattr(builtins,'open',forbidden);monkeypatch.setattr(socket,'socket',forbidden)
    monkeypatch.setattr(httpx,'Client',forbidden);monkeypatch.setattr(e.scheduler,'tick',forbidden)
    e.intelligence.sources.refresh=forbidden;e._context=forbidden
    yield e,s,c
    s.db.close()


def task(case,id='a',owner='frost'):
    e,s,c=case
    source=PRIVATE+id
    s.execute('INSERT INTO sources VALUES(?,?,?,?,0)',('GOLDWAY',source,PRIVATE,PRIVATE))
    s.execute('''INSERT INTO tasks(business,id,title,owner,deadline,next_step,criterion,status,source_id,evidence,created)
      VALUES(?,?,?,?,?,?,?,?,?,?,?)''',('GOLDWAY',id,PRIVATE,owner,'2026-10-20',PRIVATE,PRIVATE,'confirmed',source,PRIVATE,NOW))
    return id


def check(case,id='a',owner='frost'):
    task(case,id,owner)
    return case[0].scheduler.schedule_check(id,'test_operator',NOW+3600,'event-'+id,NOW)


def series(case,id='a',owner='frost'):
    task(case,id,owner)
    return case[0].scheduler.register_series(id,'test_operator',dict(frequency='daily',weekdays=list(range(7)),time='19:00',timezone='Europe/Moscow'),'event-'+id,NOW)


def overview(case,sender='test_operator'):
    e,s,c=case
    return build_reminder_overview(s,c,sender,e.intelligence.index,e.scheduler,now=NOW)


def test_actual_scheduler_pending_and_series_render_metadata_only(case):
    check(case);series(case,'b')
    value=overview(case)
    assert 'Разовые проверки:' in value.text and 'Регулярные серии:' in value.text
    assert 'Задача №1' in value.text and 'Задача №2' in value.text
    assert '16.10.2026 19:00' in value.text and 'Europe/Moscow' in value.text
    assert 'ожидает проверки' in value.text and 'серия активна' in value.text
    assert PRIVATE not in value.text and 'event-' not in value.text
    assert value.evidence_ids==(work_id('GOLDWAY','a'),work_id('GOLDWAY','b')) or set(value.evidence_ids)=={work_id('GOLDWAY','a'),work_id('GOLDWAY','b')}
    assert 'не обещание доставки' in value.text


def test_staff_only_own_no_hidden_counts(case):
    check(case);before=overview(case,'frost')
    check(case,'b','alen')
    after=overview(case,'frost')
    assert before.text==after.text
    assert set(after.evidence_ids)=={work_id('GOLDWAY','a')}
    assert 'Задача №2' not in after.text


def test_paused_series_is_not_future_delivery(case):
    value=series(case)
    case[0].scheduler.pause_series(value['id'],'test_operator','pause')
    assert 'приостановлена; сохранённое время не означает запуск' in overview(case).text


@pytest.mark.parametrize('change',['owner','title','deadline','deleted_source','missing_source','deleted_task'])
def test_changed_series_or_missing_source_omitted_without_private_reason(case,change):
    series(case)
    if change in ('owner','title','deadline'):
        case[1].execute('UPDATE tasks SET '+change+'=?',('alen' if change=='owner' else 'changed',))
    elif change=='deleted_source':case[1].execute('UPDATE sources SET deleted=1')
    elif change=='missing_source':case[1].execute('DELETE FROM sources')
    else:case[1].execute('DELETE FROM tasks')
    result=overview(case)
    assert 'Задача №1' not in result.text and result.evidence_ids==()
    assert PRIVATE not in result.text


def test_registered_fired_is_not_sent(case):
    check(case);case[1].execute("UPDATE c07_checks SET state='fired'")
    assert 'сработавшая; доставка неизвестна' in overview(case).text


def add_acl(case,who):
    e,s,c=case
    e.intelligence.index.upsert(dict(source_id='doc',document_id='one',title=PRIVATE,url='https://example.invalid',text=PRIVATE,
        readers=[reader_key('GOLDWAY',x) for x in who],observed_at=NOW-1,permission_expires_at=NOW+1,confirmed=True))
    ref=next(iter(e.intelligence.index.allowed_ids(reader_key('GOLDWAY',who[0]),now=NOW)))
    s.execute('INSERT INTO intelligence_task_acl VALUES(?,?,?)',('GOLDWAY','a',json.dumps([ref])))
    return ref


@pytest.mark.parametrize('who',[['test_operator'],['frost'],['test_operator','frost']])
def test_actual_actor_and_owner_document_acl_both_required(case,who):
    check(case);ref=add_acl(case,who)
    value=overview(case)
    assert ('Задача №1' in value.text)==(len(who)==2)
    if len(who)==2:assert ref in value.evidence_ids


def test_readonly_no_schema_tick_queue_or_payload_query(case):
    check(case)
    s=case[1];before=s.db.total_changes
    schema=s.rows('SELECT name,sql FROM sqlite_master ORDER BY name')
    traces=[];s.db.set_trace_callback(traces.append);s.execute('PRAGMA query_only=ON')
    value=overview(case)
    s.db.set_trace_callback(None)
    assert s.db.total_changes==before
    assert s.rows('SELECT name,sql FROM sqlite_master ORDER BY name')==schema
    assert PRIVATE not in value.text
    assert not any('SELECT *' in sql or 'body FROM' in sql for sql in traces)


@pytest.mark.parametrize('mutation',['task','acl','source','schedule','membership'])
def test_final_snapshot_change_fails_closed(case,monkeypatch,mutation):
    import goldway.reminder_overview as m
    check(case);real=m._snapshot;calls=[0]
    def changing(*a,**kw):
        calls[0]+=1
        if calls[0]==2:
            if mutation=='task':case[1].execute('UPDATE tasks SET revision=revision+1')
            elif mutation=='acl':case[1].execute('INSERT INTO intelligence_task_acl VALUES(?,?,?)',('GOLDWAY','a','["missing"]'))
            elif mutation=='source':case[1].execute('UPDATE sources SET deleted=1')
            elif mutation=='schedule':case[1].execute('UPDATE c07_checks SET at=at+60')
            else:case[2]['admins']=[]
        return real(*a,**kw)
    monkeypatch.setattr(m,'_snapshot',changing)
    with pytest.raises(OverviewUnavailable):overview(case)


@pytest.mark.parametrize('text',['/reminders','Какие у меня напоминания?','Покажи мои напоминания'])
def test_actual_engine_exact_command_and_replay_protect_refs(case,text):
    check(case);e,s,c=case
    assert e.reply('cmd','frost',text,channel='bot')
    row=s.execute("SELECT id,text FROM outbox WHERE dedupe='reminder-overview:cmd'").fetchone()
    assert row and 'Задача №1' in row['text']
    assert delivery_permitted(s,'GOLDWAY','frost',row['id'],NOW,c)
    assert not e.reply('cmd','frost',text,channel='bot')
    assert len(s.rows('SELECT id FROM outbox'))==1
    assert not s.rows('SELECT id FROM usage')


@pytest.mark.parametrize('text',['/reminders завтра',' Какие у меня напоминания?','Покажи мои напоминания и отмени','напомни завтра'])
def test_exact_request_does_not_hijack_other_commands(text):assert not is_reminder_overview(text)


def test_missing_scheduler_no_instantiation_and_group_no_response(case):
    e,s,c=case;e.scheduler=None
    e.reply('no','frost','/reminders',channel='business')
    assert 'недоступен' in s.rows('SELECT text FROM outbox')[0]['text']
    e.reply('group','test_operator','/reminders',channel='group')
    assert len(s.rows('SELECT id FROM outbox'))==1


def test_register_budget_fixed_failure(case,monkeypatch):
    import goldway.reminder_overview as m
    check(case);task(case,'b');monkeypatch.setattr(m,'MAX_REGISTER',1)
    with pytest.raises(OverviewUnavailable,match='^reminder_overview_unavailable$'):overview(case)


def test_final_document_revocation_withdraws_overview(case,monkeypatch):
    import goldway.reminder_overview as m
    check(case);add_acl(case,['test_operator','frost'])
    real=m._snapshot;calls=[0]
    def changing(*args,**kwargs):
        calls[0]+=1
        if calls[0]==2:case[0].intelligence.index.revoke_source('doc')
        return real(*args,**kwargs)
    monkeypatch.setattr(m,'_snapshot',changing)
    with pytest.raises(OverviewUnavailable):overview(case)


def test_visible_omission_provenance_and_utf16_budget(case):
    for i in range(18):check(case,'row'+str(i))
    value=overview(case)
    assert 'Другие доступные записи не показаны: 2.' in value.text
    assert len(value.evidence_ids)==18
    assert len(value.text.encode('utf-16-le'))//2<=3500
    assert value.text.count('• Задача №')==16


def test_foreign_business_records_do_not_change_visible_text(case):
    check(case);before=overview(case)
    case[1].execute('INSERT INTO c07_checks VALUES(?,?,?,?,?,?,?,?,?,?,?)',
      ('OTHER',PRIVATE,PRIVATE,PRIVATE,PRIVATE,PRIVATE,NOW,1,1,'pending',PRIVATE))
    assert overview(case).text==before.text


def test_staff_dependency_of_other_owner_denied_and_admin_refs_preserved(case):
    check(case);task(case,'dep','alen')
    case[1].execute('INSERT INTO intelligence_task_acl VALUES(?,?,?)',('GOLDWAY','a',json.dumps([work_id('GOLDWAY','dep')])))
    assert 'Задача №1' not in overview(case,'frost').text
    # Scheduler requires the owner as well as actor to retain access: admin cannot bypass owner denial.
    assert 'Задача №1' not in overview(case).text


def test_malformed_source_state_is_sql_bounded_and_fixed_failure(case):
    check(case)
    case[1].execute('UPDATE sources SET deleted=?',(PRIVATE*10000,))
    with pytest.raises(OverviewUnavailable,match='^reminder_overview_unavailable$'):overview(case)


@pytest.mark.parametrize('table', ['c07_checks','c07_series'])
def test_archive_scan_bound_precedes_state_filter_or_orphan_join(case,table):
    if table=='c07_checks':check(case)
    else:series(case)
    store=case[1]
    original=dict(store.execute('SELECT * FROM '+table).fetchone())
    for i in range(129):
        value=dict(original,id='archived-'+str(i),task_id='deleted-task-'+str(i))
        if table=='c07_checks':value['state']='cancelled'
        columns=list(value)
        store.execute('INSERT INTO '+table+'('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+')',tuple(value[k] for k in columns))
    queries=[]
    store.db.set_trace_callback(queries.append)
    try:
        with pytest.raises(OverviewUnavailable):overview(case)
    finally:store.db.set_trace_callback(None)
    assert any('SELECT 1 FROM '+table in q and 'LIMIT 129' in q for q in queries)
    assert not any("state IN ('pending','fired')" in q or 'JOIN tasks t' in q for q in queries)
