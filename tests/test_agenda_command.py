"""Actual Engine.reply→agenda adapter→protected outbox, synthetic only."""
import json
import pytest
from goldway.engine import Engine
from goldway.store import Store
from goldway.agenda_command import is_agenda_command

MEMBERS=['test_operator','frost','alen','nastya','dasha_it']

class NoModel:
    def propose(self,*a,**k):pytest.fail('agenda called paid model')

@pytest.fixture
def case(monkeypatch):
    config={'business':'GOLDWAY','timezone':'Europe/Moscow',
      'employees':{x:{'name':'Synthetic '+x,'telegram_user_id':i+1} for i,x in enumerate(MEMBERS)},
      'admins':['test_operator'],'business_account_user_id':99,
      'team_intake':{'recipients':MEMBERS[:],'started_at':100},
      'allowed_live_recipients':MEMBERS[:],'team_roster_verified':True,'recipients_verified':True,
      'live_send':False,'review_mode':True,'sheets_write':False,'daily_summary':False,
      'allow_onboarding_replies':False,'allow_registered_replies':False,
      'allowed_group_ids':[],'group_destinations':{},'meeting_recipient':None,
      'intelligence':{'enabled':True,'sources':[]}}
    s=Store(':memory:');e=Engine(s,config,NoModel(),lambda:1792174600)
    def forbidden(*a,**k):pytest.fail('agenda reached context/refresh/I/O')
    e._context=forbidden;e.intelligence.sources.refresh=forbidden
    import builtins,socket
    monkeypatch.setattr(builtins,'open',forbidden);monkeypatch.setattr(socket,'socket',forbidden)
    yield e,s
    s.db.close()

def seed(s,business='GOLDWAY',id='a',owner='test_operator'):
    s.execute('''INSERT INTO tasks(business,id,title,owner,deadline,next_step,criterion,status,source_id,created)
      VALUES(?,?,?,?,?,?,?,?,?,?)''',(business,id,'SYNTHETIC BLOCKED',owner,'2026-10-19','synthetic step','synthetic criterion','blocked','s',1))

def replies(s):
    return s.rows("SELECT * FROM outbox WHERE dedupe='agenda-preview:cmd'")

@pytest.mark.parametrize(('text','channel'),[('/agenda','bot'),('Подготовь повестку следующей планёрки','business')])
def test_actual_command_no_model_or_refresh_protected_current_refs(case,text,channel):
    e,s=case;seed(s);seed(s,'OTHER',id='foreign')
    before=[tuple(r) for r in s.rows('SELECT * FROM tasks')]
    assert e.reply('cmd','test_operator',text,channel=channel)
    rows=replies(s);assert len(rows)==1
    assert rows[0]['preferred_transport']==channel and rows[0]['recipient']=='test_operator'
    assert 'SYNTHETIC BLOCKED' in rows[0]['text'] and 'OTHER' not in rows[0]['text']
    assert len(rows[0]['text'].splitlines())<=6
    assert 'Только выборка' in rows[0]['text'] and 'реестр задач' in rows[0]['text']
    assert 'work:' not in rows[0]['text']
    acl=s.execute('SELECT evidence_ids FROM intelligence_outbox_acl WHERE outbox_id=?',(rows[0]['id'],)).fetchone()
    assert acl and json.loads(acl[0])
    assert not s.rows('SELECT * FROM usage') and before==[tuple(r) for r in s.rows('SELECT * FROM tasks')]
    assert s.execute("SELECT state FROM events WHERE id='cmd'").fetchone()[0]=='done'
    assert not e.reply('cmd','test_operator',text,channel=channel)
    assert len(replies(s))==1

@pytest.mark.parametrize(('sender','channel'),[('alen','bot'),('test_operator','group'),('test_operator',None),('outsider','bot')])
def test_private_admin_gate_no_task_read(case,sender,channel):
    e,s=case
    sql=[];s.db.set_trace_callback(sql.append)
    e.reply('cmd',sender,'/agenda',channel=channel)
    s.db.set_trace_callback(None)
    assert not replies(s) and not any(x.lstrip().upper().startswith('SELECT') for x in sql)

def test_missing_index_honest_unavailable(case):
    e,s=case;e.intelligence=None
    e.reply('cmd','test_operator','/agenda',channel='bot')
    assert 'не подключён' in replies(s)[0]['text']
    assert not s.rows('SELECT * FROM intelligence_outbox_acl')

@pytest.mark.parametrize('text',['пошли повестку всем','/agenda завтра','Подготовь повестку следующей планёрки и создай задачи','обзор задач','текст /agenda'])
def test_only_explicit_finite_commands(text):
    assert not is_agenda_command(text)

def test_adapter_fixed_failure_no_raw_error(case,monkeypatch):
    e,s=case
    from goldway import operational_agenda_adapter as a
    def fail(*args,**kw):raise a.AgendaUnavailable('SYNTHETIC_SECRET_ERROR')
    monkeypatch.setattr(a,'build_agenda_preview',fail)
    e.reply('cmd','test_operator','/agenda',channel='bot')
    assert 'не удалось подтвердить' in replies(s)[0]['text']
    assert 'SYNTHETIC_SECRET' not in replies(s)[0]['text']


def test_existing_dispatch_acl_blocks_when_reader_removed(case):
    e,s=case;seed(s)
    e.reply('cmd','test_operator','/agenda',channel='bot')
    from goldway.intelligence import delivery_permitted
    row=replies(s)[0]
    assert delivery_permitted(s,'GOLDWAY','test_operator',row['id'],e.now(),e.c)
    e.c['admins']=[];del e.c['employees']['test_operator']
    assert not delivery_permitted(s,'GOLDWAY','test_operator',row['id'],e.now(),e.c)


def test_omitted_sample_count_keeps_all_five_refs_for_delivery(case):
    e,s=case
    for i in range(5):seed(s,id='task'+str(i))
    e.reply('cmd','test_operator','/agenda',channel='bot')
    row=replies(s)[0]
    assert 'Ещё пунктов' in row['text']
    refs=json.loads(s.execute('SELECT evidence_ids FROM intelligence_outbox_acl WHERE outbox_id=?',(row['id'],)).fetchone()[0])
    assert len(refs)==5
    from goldway.intelligence import delivery_permitted
    assert delivery_permitted(s,'GOLDWAY','test_operator',row['id'],e.now(),e.c)
    s.execute("DELETE FROM tasks WHERE business='GOLDWAY' AND id='task4'")
    assert not delivery_permitted(s,'GOLDWAY','test_operator',row['id'],e.now(),e.c)


@pytest.mark.parametrize(('sender','expected'),[('test_operator','explicit_request_current_task_draft'),('alen',False)])
def test_real_general_reply_model_receives_only_current_sender_agenda_capability(case,sender,expected):
    e,s=case
    # This check exercises the normal model path, not the explicit command.
    e._context=Engine._context.__get__(e,Engine)
    e.intelligence.sources.refresh=lambda *a,**kw:None
    observed=[]
    class SyntheticModel:
        def propose(self,context):
            observed.append(dict(context['capabilities']))
            return {'actions':[],'intent':'conversation','summary':'Synthetic capability answer',
                    'reply_text':'Это синтетическая проверка описания возможностей.',
                    'dialogue_state':{'topic':'','entities':[],'pending_offer':None,'task_id':None},
                    'evidence_ids':[],'knowledge_mode':'not_needed'}
    e.model=SyntheticModel()
    assert e.reply('capability','test_operator' if sender=='test_operator' else 'alen','Что ты умеешь?',channel='bot')
    e.process('capability')
    assert len(observed)==1 and observed[0]['agenda_preview']==expected
    assert observed[0]['automatic_agenda_dispatch'] is False
    assert ('agenda_preview_request' in observed[0])==(expected is not False)
    assert not s.rows('SELECT id FROM tasks')
    assert s.execute("SELECT state FROM events WHERE id='capability'").fetchone()[0]=='done'
