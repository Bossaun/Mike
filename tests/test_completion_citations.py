"""Cross-referenced completion and current-only citations, entirely synthetic."""
import copy
import pytest
from goldway.engine import Engine
from goldway.store import Store
from goldway.model import response_schema

TEXT = ('По задаче №3 результат проверки: единственный экземпляр №4 появился по расписанию, '
        'получен и выполнен. Повторение остановлено. Закрой задачу №3 как выполненную.')


class CompletionModel:
    def __init__(self, mode='not_needed', refs=None):
        self.mode=mode
        self.refs=refs or []
        self.contexts=[]

    def propose(self, context):
        self.contexts.append(copy.deepcopy(context))
        return {'intent':'task_action','actions':[{'op':'update','task_id':'template',
                'status':'completed','set_fields':['status'],'evidence_lines':[1]}],
            'summary':'Принят результат проверки.','reply_text':'Результат проверки записан.',
            'knowledge_mode':self.mode,'evidence_ids':self.refs,
            'dialogue_state':{'topic':'Проверка','entities':[],
                              'pending_offer':None,'task_id':'template'}}


@pytest.fixture
def case(monkeypatch):
    members=['test_operator','frost','alen','nastya','dasha_it']
    now=1792177200
    config={'business':'GOLDWAY','timezone':'Europe/Moscow',
        'employees':{key:{'name':key,'telegram_user_id':n} for n,key in enumerate(members,1)},
        'admins':['test_operator'],'business_account_user_id':99,'team_roster_verified':True,
        'allowed_live_recipients':members,'live_send':False,'recipients_verified':True,
        'review_mode':True,'sheets_write':False,'daily_summary':False,
        'allow_onboarding_replies':False,'allow_registered_replies':False,
        'team_intake':{'recipients':members,'started_at':now-60},
        'intelligence':{'enabled':True,'sources':[]}}
    store=Store(':memory:');model=CompletionModel();engine=Engine(store,config,model,lambda:now)
    # No network client is needed, even for an empty registered-source refresh.
    monkeypatch.setattr(engine.intelligence.sources,'refresh',lambda *a,**k:{})
    store.execute("INSERT INTO sources(business,id,body,link) VALUES('GOLDWAY','seed','Synthetic instruction','local')")
    for id,status in [('old-1','completed'),('old-2','completed'),('template','confirmed'),('occurrence','completed')]:
        store.execute('''INSERT INTO tasks(business,id,title,owner,deadline,next_step,criterion,status,source_id,evidence,created)
            VALUES('GOLDWAY',?,?,'test_operator','2026-10-16','Проверить расписание','Один экземпляр',?,'seed','Synthetic seed',?)''',
            (id,'Синтетическая проверка '+id,status,now-10))
        store.execute("INSERT INTO task_sources VALUES('GOLDWAY',?,'seed')",(id,))
    yield engine,store,model
    store.db.close()


def test_empty_evidence_schema_excludes_impossible_grounded_modes(case):
    engine,store,model=case
    engine.reply('synthetic','test_operator',TEXT,source_id='telegram:1:50',channel='bot')
    engine.process('synthetic')
    context=model.contexts[0]
    assert {t['public_number'] for t in context['tasks']}=={3,4}
    assert not context['knowledge']['evidence'] and not context.get('work_context')
    schema=response_schema(context)['properties']
    assert schema['evidence_ids']['maxItems']==0
    assert set(schema['knowledge_mode']['enum'])=={'not_needed','not_found'}


def test_literal_completion_crossref_updates_only_explicit_target_without_kb_refs(case):
    engine,store,model=case
    before={t['id']:dict(t) for t in engine.tasks()}
    engine.reply('completion','test_operator',TEXT,source_id='telegram:1:50',channel='bot')
    engine.process('completion')
    after={t['id']:dict(t) for t in engine.tasks()}
    assert after['template']['status']=='completed' and after['template']['evidence']==TEXT
    assert all(after[id]==before[id] for id in ('old-1','old-2','occurrence'))
    assert store.rows('SELECT state,error FROM events')==[{'state':'done','error':None}]
    count=len(store.rows('SELECT * FROM outbox'));engine.process('completion')
    assert len(store.rows('SELECT * FROM outbox'))==count


@pytest.mark.parametrize('mode,refs',[('grounded',[]),('conflict',[]),
                                     ('not_needed',['template']),('grounded',['invented'])])
def test_invalid_citation_combinations_still_fail_closed_before_completion(case,mode,refs):
    engine,store,_=case;engine.model=CompletionModel(mode,refs)
    engine.reply('invalid','test_operator',TEXT,source_id='telegram:1:50',channel='bot')
    with pytest.raises(ValueError):engine.process('invalid')
    assert next(t for t in engine.tasks() if t['id']=='template')['status']=='confirmed'
    assert store.rows('SELECT state,error FROM events')==[{'state':'failed','error':'ValueError: knowledge_citations'}]
    assert all(row['dedupe']=='error:invalid' for row in store.rows('SELECT dedupe FROM outbox'))


def test_current_evidence_still_allows_grounded_modes_and_only_current_ids():
    context={'intelligence':True,'kind':'operator_chat',
             'knowledge':{'evidence':[{'id':'current'}]},'dialogue_evidence_ids':['old']}
    props=response_schema(context)['properties']
    assert set(props['knowledge_mode']['enum'])=={'grounded','conflict','not_found','not_needed'}
    assert props['evidence_ids']['items']['enum']==['current']
