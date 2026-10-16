"""New source-status -> context/not-found boundary, synthetic only."""
import copy
import json
from types import SimpleNamespace
import pytest
from goldway.store import Store
from goldway.intelligence import Intelligence

SECRET='SYNTHETIC_PRIVATE_SOURCE_TITLE_URL_CREDENTIAL_EXCEPTION'

@pytest.fixture
def case(monkeypatch):
    s=Store(':memory:')
    config={'business':'SYNTHETIC','employees':{'alice':{'name':'Alice'},'bob':{'name':'Bob'}},
            'admins':['alice'],'intelligence':{'enabled':True,'sources':[]}}
    intel=Intelligence(s,config)
    # No real source activation or source callback: status projection only.
    intel.sources.refresh=lambda **kw:dict(intel.sources.status)
    import builtins,socket,goldway.knowledge_sources as source_module
    def forbidden(*a,**k):pytest.fail('unexpected file/network/provider call')
    monkeypatch.setattr(builtins,'open',forbidden)
    monkeypatch.setattr(socket,'socket',forbidden)
    monkeypatch.setattr(source_module.httpx,'Client',forbidden)
    yield intel,s,config
    s.db.close()


def add(case,readers=('alice',),status='ok',name='synthetic'):
    i=case[0];source=i.prefix+name
    i.sources.specs.append({'id':source,'readers':[i.reader(r) for r in readers],
                           'title':SECRET,'url':'https://'+SECRET+'.invalid','reader_emails':SECRET})
    if status is not None:i.sources.status[source]=status
    return source


def enrich(case,sender='alice'):
    p={'sender':sender,'text':'Что известно об ассортименте?','channel':'bot','source_id':'synthetic:1'}
    ctx={'tasks':[],'recent_summaries':[]}
    case[0].enrich(p,ctx,1000)
    return p,ctx


def answer(case,ctx,p,**changes):
    r={'dialogue_state':{'topic':'','entities':[],'pending_offer':None,'task_id':None},
       'knowledge_mode':'not_found','evidence_ids':[],'actions':[],
       'reply_text':'MODEL INVENTED FACT','intent':'conversation','summary':'Synthetic summary'}
    r.update(changes)
    return case[0].validate(r,ctx,p,1000)


@pytest.mark.parametrize('status',['unavailable','blocked'])
def test_readable_failure_precise_honest_no_provider_leak(case,status):
    add(case,status=status);p,ctx=enrich(case);result=answer(case,ctx,p)
    assert ctx['source_health']=={'non_grounding':True,'registered_readable':1,
           'last_refresh_counts':dict(ok=0,blocked=int(status=='blocked'),unavailable=int(status=='unavailable'),unknown=0)}
    assert 'недоступны или заблокированы' in result['reply_text']
    assert 'это не означает' in result['reply_text']
    assert result['actions']==[] and result['evidence_ids']==[] and result['knowledge_mode']=='not_found'
    assert SECRET not in json.dumps(ctx,ensure_ascii=False)+str(result)
    assert 'MODEL INVENTED FACT' not in result['reply_text']


def test_successful_last_status_without_facts_not_business_verification(case):
    add(case);p,ctx=enrich(case);r=answer(case,ctx,p)
    assert 'не найдено подходящих подтверждённых сведений' in r['reply_text']
    assert 'не подтверждает полноту или актуальность' in r['reply_text']
    assert 'недоступны или заблокированы' not in r['reply_text']
    assert r['evidence_ids']==[]
    assert not any(k in json.dumps(ctx['source_health']) for k in ('checked_at','observed_at','source_id','url','title'))


def test_no_registered_readable_sources_not_no_matching_facts(case):
    add(case,readers=('bob',),status='blocked');p,ctx=enrich(case);r=answer(case,ctx,p)
    assert ctx['source_health']['registered_readable']==0
    assert sum(ctx['source_health']['last_refresh_counts'].values())==0
    assert 'Для вас не зарегистрированы доступные источники' in r['reply_text']


def test_private_registration_and_global_status_never_affect_other_reader(case):
    add(case,readers=('alice',),status='ok');p,before=enrich(case)
    add(case,readers=('bob',),status='blocked',name=SECRET)
    case[0].sources.status[SECRET]='unavailable'
    _,after=enrich(case)
    assert before['source_health']==after['source_health']
    assert SECRET not in json.dumps(after,ensure_ascii=False)
    _,bob=enrich(case,'bob')
    assert bob['source_health']['registered_readable']==1
    assert bob['source_health']['last_refresh_counts']['blocked']==1


def test_unregistered_sender_has_no_health_metadata(case):
    add(case);p,ctx=enrich(case,'outsider')
    assert 'source_health' not in ctx
    assert 'нет подтверждённых сведений' in answer(case,ctx,p)['reply_text']


@pytest.mark.parametrize('status',[None,SECRET,{'raw_exception':SECRET},False,23])
def test_unknown_status_fixed_bucket_no_invented_clock(case,status):
    add(case,status=status);p,ctx=enrich(case);r=answer(case,ctx,p)
    assert ctx['source_health']['last_refresh_counts']['unknown']==1
    assert 'Состояние зарегистрированных для вас источников пока не подтверждено' in r['reply_text']
    assert SECRET not in json.dumps(ctx,ensure_ascii=False)+str(r)
    assert '1000' not in json.dumps(ctx['source_health'])


def test_partial_failure_not_all_sources_or_full_success(case):
    add(case,status='ok');add(case,status='blocked',name='second')
    p,ctx=enrich(case)
    assert 'Часть зарегистрированных' in answer(case,ctx,p)['reply_text']


def test_membership_removed_source_not_counted(case):
    add(case);case[0].sources.members.remove(case[0].reader('alice'))
    assert case[0]._source_health('alice')['registered_readable']==0
    case[2]['employees'].pop('alice');case[2]['admins']=[]
    assert case[0]._source_health('alice') is None


def test_foreign_business_specs_not_counted(case):
    add(case);case[0].sources.specs.append({'id':'source:foreign:'+SECRET,'readers':[case[0].reader('alice')]})
    assert case[0]._source_health('alice')['registered_readable']==1


def test_optional_explicit_local_bindings_scoped_without_discovery(case):
    i=case[0]
    alice=SimpleNamespace(audience=frozenset({i.reader('alice')}))
    bob=SimpleNamespace(audience=frozenset({i.reader('bob')}))
    bridge=SimpleNamespace(business=i.b,bindings=(alice,bob),source_id=lambda b:'local-alice' if b is alice else SECRET)
    i.sources.trusted_bridge=bridge;i.sources.status={'local-alice':'blocked',SECRET:'unavailable'}
    assert i._source_health('alice')['last_refresh_counts']==dict(ok=0,blocked=1,unavailable=0,unknown=0)
    bridge.business='FOREIGN'
    assert i._source_health('alice')['registered_readable']==0


def test_source_health_not_citation_or_acl(case):
    add(case,status='blocked');p,ctx=enrich(case)
    assert Intelligence.evidence(ctx['knowledge'],ctx.get('work_context'))=={}
    assert case[0].readable_ids('alice',1000)==set()
    with pytest.raises(ValueError,match='Invalid knowledge citations'):
        answer(case,ctx,p,knowledge_mode='grounded',evidence_ids=['source_health'])
    with pytest.raises(ValueError,match='Grounded answer requires evidence'):
        answer(case,ctx,p,knowledge_mode='grounded')
    assert case[1].rows('SELECT * FROM knowledge_documents')==[]


def test_explicit_missing_result_fallback_remains_result_specific(case):
    add(case,status='blocked');p,ctx=enrich(case)
    ctx['work_context']={'scope':'current_authorized_registered_task_result','evidence':[]}
    r=answer(case,ctx,p)
    assert 'нет зарегистрированного подтверждённого результата этой задачи' in r['reply_text']
    assert 'это не означает' in r['reply_text'].lower()
    assert 'проблем бизнеса' not in r['reply_text'] and r['evidence_ids']==[]


def test_existing_generic_work_fallback_preserved(case):
    p,ctx=enrich(case)
    ctx['work_context']={'scope':'current_authorized_task_records','evidence':[]}
    assert 'Подтверждённого обзора проблем бизнеса' in answer(case,ctx,p)['reply_text']


@pytest.mark.parametrize('health',[
    {},{'non_grounding':True,'registered_readable':True,'last_refresh_counts':{}},
    {'non_grounding':True,'registered_readable':1,'last_refresh_counts':dict(ok=0,blocked=0,unavailable=0,unknown=0)},
    {'non_grounding':True,'registered_readable':1,'last_refresh_counts':{'raw_exception':SECRET}},
])
def test_malformed_health_does_not_echo_or_claim_status(case,health):
    p,ctx=enrich(case);ctx['source_health']=health
    result=answer(case,ctx,p)
    assert result['reply_text'].startswith('В доступных вам источниках нет подтверждённых сведений')
    assert SECRET not in result['reply_text']


def test_unknown_health_does_not_mutate_proposals_or_mode(case):
    add(case,status=SECRET);p,ctx=enrich(case)
    r=answer(case,ctx,p,knowledge_mode='not_needed',reply_text='Ordinary conversation')
    assert r['reply_text']=='Ordinary conversation' and r['actions']==[]
