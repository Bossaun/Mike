"""I27 actual Engine boundary: static help and unchanged fallback, no live actions."""
from dataclasses import FrozenInstanceError
import pytest
from goldway.engine import Engine
from goldway.store import Store
from goldway.knowledge import KnowledgeIndex
import goldway.preview_help_command as command
ORIGINAL_INDEX_INIT = KnowledgeIndex.__init__
MEMBERS = ['test_operator', 'frost', 'alen', 'nastya', 'dasha_it']


class NoExternal:
    def propose(self, *a, **k):
        pytest.fail('preview help called model')
    def call(self, *a, **k):
        pytest.fail('preview help called Telegram/provider')


@pytest.fixture
def case(monkeypatch):
    config = {'business': 'GOLDWAY', 'timezone': 'Europe/Moscow',
        'employees': {x: {'name': 'Synthetic '+x, 'telegram_user_id': i+1} for i, x in enumerate(MEMBERS)},
        'admins': ['test_operator'], 'business_account_user_id': 99,
        'team_intake': {'recipients': MEMBERS[:], 'started_at': 100},
        'allowed_live_recipients': MEMBERS[:], 'team_roster_verified': True, 'recipients_verified': True,
        'live_send': False, 'review_mode': True, 'sheets_write': False, 'daily_summary': False,
        'allow_onboarding_replies': False, 'allow_registered_replies': False,
        'allowed_group_ids': [], 'group_destinations': {}, 'meeting_recipient': None,
        'bot_user_id': 100, 'bot_user_id_verified': True,
        'intelligence': {'enabled': True, 'sources': []}}
    clock = [1792174600.0]
    store = Store(':memory:')
    engine = Engine(store, config, NoExternal(), lambda: clock[0])
    def forbidden(*a, **k):
        pytest.fail('preview help reached context/refresh/external I/O/constructor')
    engine._context = forbidden
    engine.intelligence.sources.refresh = forbidden
    import builtins, socket, httpx
    monkeypatch.setattr(builtins, 'open', forbidden)
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(httpx, 'Client', forbidden)
    monkeypatch.setattr(KnowledgeIndex, '__init__', forbidden)
    yield engine, store, clock
    store.db.close()


def reply(case,text='Что умеешь?',event='help',sender='test_operator',channel='bot',source=None):
    added=case[0].reply(event,sender,text,channel=channel,
        source_id=source or ('business:conn:1:20' if channel=='business' else 'telegram:1:20'))
    rows=case[1].rows('SELECT * FROM outbox WHERE dedupe=?',('preview-help:'+event,))
    return added,rows[0] if rows else None

@pytest.mark.parametrize('channel',['bot','business'])
@pytest.mark.parametrize('text',['Что умеешь?','Что ты умеешь?','  ЧТО   УМЕЕШЬ! '])
def test_admin_static_help_no_context_model_io_and_keeps_pending_barrier(case,channel,text):
    added,out=reply(case,text,channel=channel)
    assert added and out and out['text']==command.build_preview_help()
    for item in command.DESCRIPTORS:assert out['text'].count(item.request)==1
    assert 'только введённые вами значения' in out['text']
    assert 'ничего не загружают' in out['text']
    assert len(out['text'].encode('utf-16-le'))//2<=3500
    assert out['recipient']=='test_operator' and out['task_id'] is None
    assert out['preferred_transport']==channel and out['parse_mode'] is None
    assert case[1].rows('SELECT state FROM events')[0]['state']=='done'
    turns=case[1].rows('SELECT * FROM answer_citation_turns')
    assert len(turns)==1 and turns[0]['phase']=='pending'
    assert not case[1].rows('SELECT * FROM tasks')
    assert not case[1].rows('SELECT * FROM sources')
    assert not case[1].rows('SELECT * FROM runtime')
    case[0].process('help')

@pytest.mark.parametrize('text',['Кто ты?','Что умеешь завтра?','Расскажи что умеешь','Не что умеешь',
    'Что умеешь?\nРассчитай акцию','Мои задачи','Что умеешь с продажами?','x'*129])
def test_exact_requests_only(text):assert not command.is_preview_help_command(text)

@pytest.mark.parametrize('phrase',[d.request for d in command.DESCRIPTORS])
def test_advertised_phrase_reaches_real_existing_template_route(case,phrase):
    reply(case)
    added=case[0].reply('template','test_operator',phrase,channel='bot',source_id='telegram:1:21')
    rows=case[1].rows("SELECT * FROM outbox WHERE dedupe<>'preview-help:help'")
    assert added and len(rows)==1
    assert '[уточнить]' in rows[0]['text']
    assert rows[0]['task_id'] is None
    assert case[1].rows("SELECT state FROM events WHERE id='template'")[0]['state']=='done'


def test_replay_one_queue_no_offer(case):
    assert reply(case)[0]
    assert reply(case)[0] is False
    assert len(case[1].rows('SELECT * FROM outbox'))==1
    assert len(case[1].rows('SELECT * FROM answer_citation_turns'))==1
    assert not case[1].rows('SELECT * FROM runtime')

@pytest.mark.parametrize('gate',['staff','group','nonclosed','missing_intelligence','business_drift','config_drift','source'])
def test_initial_unavailable_gate_falls_through_pending_no_admin_help(case,gate):
    e,s,_=case;sender='test_operator';channel='bot';source='telegram:1:20'
    if gate=='staff':sender='frost';source='telegram:2:20'
    elif gate=='group':channel='group';source='telegram:-20:20'
    elif gate=='nonclosed':e.c.pop('team_intake')
    elif gate=='missing_intelligence':e.intelligence=None
    elif gate=='business_drift':e.intelligence.b='OTHER'
    elif gate=='config_drift':e.intelligence.c=dict(e.c)
    elif gate=='source':source='untrusted'
    added,out=reply(case,sender=sender,channel=channel,source=source)
    assert added and out is None and not s.rows('SELECT * FROM outbox')
    assert s.rows('SELECT state FROM events')[0]['state']=='pending'


def test_staff_actual_normal_model_path_not_silenced_or_admin_preview(case,monkeypatch):
    e,s,_=case
    reply(case,sender='frost',source='telegram:2:20')
    monkeypatch.setattr(KnowledgeIndex,'__init__',ORIGINAL_INDEX_INIT)
    e._context=Engine._context.__get__(e)
    calls=[]
    e.intelligence.sources.refresh=lambda now:calls.append('refresh')
    class Model:
        def propose(self,context):
            calls.append('model')
            return dict(intent='conversation',actions=[],summary='Обычный ответ сотруднику',
                reply_text='Обычный ответ сотруднику',knowledge_mode='not_needed',evidence_ids=[],
                dialogue_state=dict(topic='',entities=[],pending_offer=None,task_id=None))
    e.model=Model();e.process('help')
    assert calls.count('model')==1 and calls.count('refresh')>=1
    assert s.rows('SELECT state FROM events')[0]['state']=='done'
    rows=s.rows('SELECT * FROM outbox');assert len(rows)==1
    assert rows[0]['text']=='Обычный ответ сотруднику'
    assert not any(item.request in rows[0]['text'] for item in command.DESCRIPTORS)


def test_missing_intelligence_actual_basic_reply_preserved(case):
    e,s,_=case;e.intelligence=None;e._context=Engine._context.__get__(e)
    reply(case);e.process('help')
    rows=s.rows('SELECT * FROM outbox')
    assert len(rows)==1 and 'Я помощник GOLDWAY' in rows[0]['text']
    assert not any(item.request in rows[0]['text'] for item in command.DESCRIPTORS)

@pytest.mark.parametrize('drift',['admin','intelligence','scope','business'])
def test_after_queue_auth_scope_drift_rolls_back_event_barrier_queue(case,monkeypatch,drift):
    e,s,_=case;original=s.queue;payloads=[]
    real_handle=command.handle_preview_help
    def handle(engine,event_id,payload):
        payloads.append(payload)
        return real_handle(engine,event_id,payload)
    monkeypatch.setattr(command,'handle_preview_help',handle)
    def queue(*args,**kwargs):
        original(*args,**kwargs)
        if drift=='admin':e.c['admins']=[]
        elif drift=='intelligence':e.intelligence=None
        elif drift=='business':e.c['business']='OTHER'
        elif drift=='scope':payloads[0]['source_id']='telegram:9:20'
    monkeypatch.setattr(s,'queue',queue)
    with pytest.raises(ValueError):reply(case)
    assert not s.rows('SELECT * FROM outbox') and not s.rows('SELECT * FROM events')
    assert not s.rows('SELECT * FROM answer_citation_turns')


def test_queue_failure_rolls_back(case,monkeypatch):
    def fail(*a,**k):raise RuntimeError('synthetic failure')
    monkeypatch.setattr(case[1],'queue',fail)
    with pytest.raises(RuntimeError):reply(case)
    assert not case[1].rows('SELECT * FROM events')
    assert not case[1].rows('SELECT * FROM answer_citation_turns')

@pytest.mark.parametrize('changes',[{'request':''},{'request':'x'*65},{'description':'x'*121},
    {'description':'a\nb'},{'data_scope':'warehouse_live'}])
def test_descriptor_bounds_and_scope(changes):
    kwargs=dict(request='Команда',description='Описание');kwargs.update(changes)
    with pytest.raises(command.PreviewHelpUnavailable):command.PreviewDescriptor(**kwargs)


def test_descriptors_immutable():
    assert type(command.DESCRIPTORS) is tuple and len(command.DESCRIPTORS)==6
    with pytest.raises(FrozenInstanceError):command.DESCRIPTORS[0].request='Другой запрос'

@pytest.mark.parametrize('replacement',[[],(),command.DESCRIPTORS*2,(command.DESCRIPTORS[0],)*2])
def test_malformed_descriptors_rollback(case,monkeypatch,replacement):
    monkeypatch.setattr(command,'DESCRIPTORS',replacement)
    with pytest.raises(command.PreviewHelpUnavailable):reply(case)
    assert not case[1].rows('SELECT * FROM events') and not case[1].rows('SELECT * FROM outbox')


def test_output_budget_rollback(case,monkeypatch):
    monkeypatch.setattr(command,'MAX_OUTPUT_UNITS',10)
    with pytest.raises(command.PreviewHelpUnavailable):reply(case)
    assert not case[1].rows('SELECT * FROM events') and not case[1].rows('SELECT * FROM outbox')


def test_sales_descriptor_describes_operation_revenue_not_unit_price():
    item=next(d for d in command.DESCRIPTORS if d.request=='Проверь продажи')
    assert item.description=='выручка ниже себестоимости и неизвестная себестоимость в переданных строках'
    assert 'выручка ниже себестоимости' in command.build_preview_help()
