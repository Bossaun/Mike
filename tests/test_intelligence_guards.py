"""Runtime safety wiring with invented documents and mocked transports only."""
import copy
import json

import httpx
import pytest

from goldway.dialogue import scope_from_payload
from goldway.engine import Engine
from goldway.intelligence import Intelligence, delivery_permitted
from goldway.model import JsonModel, response_schema
from goldway.telegram import Dispatcher, Updates
from test_personal_intake import profile
from test_telegram import API


@pytest.fixture
def intel(env):
    _, s, c, clock = env
    profile(c, clock)
    c['live_send'] = True
    c['recipients_verified'] = True
    c['intelligence'] = {'enabled': True, 'sources': []}
    e = Engine(s, c, None, lambda: clock[0])
    return e, s, c, clock


def doc(e, now, **changes):
    value = dict(source_id='synthetic', document_id='catalog', title='Каталог вымышленных брендов',
                 url='https://fictional.example.invalid/catalog', text='Вымышленный бренд Норива выпускает кремы.',
                 readers=[e.intelligence.reader('test_operator')], observed_at=now-1,
                 permission_expires_at=now+900, updated_at=None, confirmed=True)
    value.update(changes)
    e.intelligence.index.upsert(value)


def payload(text='Расскажи о Норива', chat=77):
    return dict(sender='test_operator', text=text, channel='bot', source_id=f'telegram:{chat}:1')


def enrich(e, now, p=None):
    p = p or payload()
    context = {'tasks': [], 'recent_summaries': ['NOT DELIVERED SECRET']}
    scope = e.intelligence.enrich(p, context, now)
    return p, context, scope


def answer(context, **changes):
    refs = [r['id'] for r in context['knowledge']['evidence']]
    value = dict(actions=[], summary='Внутренний итог', intent='conversation', reply_text='Норива выпускает кремы.',
                 dialogue_state=dict(topic='Норива', entities=['Норива'], pending_offer=None, task_id=None),
                 evidence_ids=refs, knowledge_mode='grounded' if refs else 'not_needed')
    value.update(changes)
    return value


def test_enrichment_uses_only_delivered_chat_history_and_no_internal_summaries(intel):
    e, s, c, clock = intel
    doc(e, clock[0])
    p, ctx, scope = enrich(e, clock[0])
    assert ctx['recent_summaries'] == [] and not ctx['dialogue']['turns']
    result = e.intelligence.validate(answer(ctx), ctx, p, clock[0])
    assert 'Источники:' in result['reply_text'] and 'дата обновления не указана' in result['reply_text']
    assert result['summary'] == 'Внутренний итог'
    assert ctx['knowledge']['evidence']


@pytest.mark.parametrize('change', [dict(evidence_ids=['invented']), dict(knowledge_mode='grounded', evidence_ids=[]),
    dict(knowledge_mode='not_found'), dict(dialogue_state={'task_id': 'foreign'}), dict(knowledge_mode='invented')])
def test_invalid_model_metadata_cannot_authorize_an_answer(intel, change):
    e, _, _, clock = intel; doc(e, clock[0]); p, ctx, _ = enrich(e, clock[0])
    with pytest.raises(ValueError): e.intelligence.validate(answer(ctx, **change), ctx, p, clock[0])


def test_revocation_during_model_call_rejects_even_uncited_source_context(intel):
    e, _, _, clock = intel; doc(e, clock[0]); p, ctx, _ = enrich(e, clock[0])
    e.intelligence.index.revoke_source('synthetic')
    with pytest.raises(ValueError, match='permission'):
        e.intelligence.validate(answer(ctx, knowledge_mode='not_needed', evidence_ids=[]), ctx, p, clock[0])


def test_pending_output_revocation_blocks_before_any_telegram_lookup_or_send(intel):
    e, s, c, clock = intel; doc(e, clock[0]); p, ctx, scope = enrich(e, clock[0])
    result = e.intelligence.validate(answer(ctx), ctx, p, clock[0])
    s.queue(e.b, 'test_operator', result['reply_text'], 'synthetic', clock[0], preferred_transport='bot')
    e.intelligence.remember('event', p, ctx, result, scope, 0, [], clock[0])
    e.intelligence.index.revoke_source('synthetic')
    api = API(); Dispatcher(s, c, api, lambda: clock[0]).flush()
    assert not api.calls
    assert s.rows('SELECT state,attempts FROM outbox') == [{'state':'blocked','attempts':0}]


def test_inherited_private_answer_provenance_cannot_be_laundered_by_general_followup(intel):
    e, s, _, clock = intel; doc(e, clock[0]); p, ctx, scope = enrich(e, clock[0])
    result = e.intelligence.validate(answer(ctx), ctx, p, clock[0])
    s.queue(e.b, 'test_operator', result['reply_text'], 'a', clock[0])
    e.intelligence.remember('a', p, ctx, result, scope, 0, [], clock[0])
    s.execute("UPDATE outbox SET state='sent',transport='bot',destination_chat='77'")
    p2 = payload('Сделай короче'); p2['source_id'] = 'telegram:77:2'
    _, ctx2, scope2 = enrich(e, clock[0], p2)
    assert ctx2['dialogue']['turns'] and ctx2['dialogue_evidence_ids']
    ctx2['knowledge'] = {'evidence': [], 'facts': [], 'conflicts': [], 'status':'no_evidence'}
    r2 = answer(ctx2, evidence_ids=[], knowledge_mode='not_needed')
    s.queue(e.b, 'test_operator', r2['reply_text'], 'b', clock[0])
    e.intelligence.remember('b', p2, ctx2, r2, scope2, 1, [], clock[0])
    assert delivery_permitted(s, 'another-business', 'test_operator', 2, clock[0])
    e.intelligence.index.revoke_source('synthetic')
    assert not delivery_permitted(s, e.b, 'test_operator', 2, clock[0])
    memory = e.intelligence.memory.get(scope, readable_ids=set(), now=clock[0])
    assert memory['turns'] == [] and memory['topic'] == ''


def test_reader_keys_isolate_businesses_in_shared_store(intel):
    e, s, c, clock = intel; doc(e, clock[0])
    other = Intelligence(s, dict(c, business='another-business'))
    assert other.reader('test_operator') != e.intelligence.reader('test_operator')
    assert not other.index.allowed_ids(other.reader('test_operator'), now=clock[0])


def test_short_acceptance_cannot_turn_explanation_into_task_consent(intel):
    e, _, _, clock = intel; p, ctx, _ = enrich(e, clock[0], payload('Давай'))
    ctx['dialogue']['pending_offer'] = {'kind':'explain','text':'Расскажу про кремы'}
    with pytest.raises(ValueError, match='consent'):
        e.intelligence.validate(answer(ctx, actions=[{'op':'create'}]), ctx, p, clock[0])


@pytest.mark.parametrize('text',['Нет, я просто спрашивал, задачу создавать не надо','Не создавай задачу','Напомни завтра'])
def test_negation_and_reminder_do_not_silently_mutate_tasks(intel, text):
    e, _, _, clock = intel; p, ctx, _ = enrich(e, clock[0], payload(text))
    with pytest.raises(ValueError): e.intelligence.validate(answer(ctx, actions=[{'op':'create'}]), ctx, p, clock[0])


def test_schema_addition_is_opt_in_and_strict(intel):
    assert 'dialogue_state' not in response_schema({'kind':'operator_chat'})['properties']
    schema = response_schema({'kind':'operator_chat','intelligence':True})
    for key in ('dialogue_state','knowledge_mode','evidence_ids'): assert key in schema['required']
    assert schema['properties']['dialogue_state']['additionalProperties'] is False


def test_intelligence_system_and_schema_reach_model_and_budget_reservation(intel, monkeypatch):
    e, s, c, clock = intel
    import goldway.model as module
    captured = []
    def respond(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200,json={'choices':[{'message':{'content':'{"actions":[],"summary":"synthetic"}'}}]})
    client = httpx.Client(transport=httpx.MockTransport(respond))
    monkeypatch.setattr(module.httpx, 'Client', lambda **kwargs:client)
    JsonModel(s,e.b,c['model'],'fictional-key',lambda:clock[0]).propose({'intelligence':True,'kind':'operator_chat'})
    assert 'ACTUAL delivered conversation' in captured[0]['messages'][0]['content']
    assert 'dialogue_state' in captured[0]['response_format']['json_schema']['schema']['required']
    assert s.rows('SELECT reserved_cost FROM usage')[0]['reserved_cost'] > 0


def test_feature_off_does_not_import_old_knowledge_or_create_intelligence_tables(env):
    e, s, _, _ = env
    assert e.intelligence is None
    assert not s.rows("SELECT name FROM sqlite_master WHERE name LIKE 'knowledge_%'")


def test_assumptions_cannot_be_cited_as_confirmed_company_facts(intel):
    e, _, _, clock = intel; doc(e, clock[0], confirmed=False)
    p, ctx, _ = enrich(e, clock[0])
    assert ctx['knowledge']['evidence'] and ctx['knowledge']['evidence'][0]['confirmed'] is False
    with pytest.raises(ValueError, match='Assumptions'):
        e.intelligence.validate(answer(ctx),ctx,p,clock[0])


def test_missing_source_does_not_allow_model_to_invent_a_company_answer(intel):
    e, _, _, clock = intel; p, ctx, _ = enrich(e, clock[0], payload('Какие бренды?'))
    result = e.intelligence.validate(answer(ctx,knowledge_mode='not_found',reply_text='У нас есть ВЫДУМАННЫЙ-БРЕНД.'),ctx,p,clock[0])
    assert 'ВЫДУМАННЫЙ' not in result['reply_text'] and 'нет подтверждённых' in result['reply_text']


def test_expired_knowledge_is_removed_from_actual_dialogue_state(intel):
    e, _, _, clock = intel; doc(e,clock[0],permission_expires_at=clock[0]+10)
    p, ctx, scope = enrich(e,clock[0]); refs = list(e.intelligence.evidence(ctx['knowledge']))
    e.intelligence.memory.record(scope,'synthetic-direct','бренды','Норива',state=answer(ctx)['dialogue_state'],
                                 evidence_ids=refs,at=clock[0])
    _, later, _ = enrich(e,clock[0]+11,payload('Давай'))
    assert not later['dialogue']['turns'] and not later['dialogue']['entities']


def test_intelligence_greeting_enters_normal_conversation_instead_of_repeating_template(intel):
    e, s, _, clock = intel
    m = {'message_id':1,'date':int(clock[0]),'from':{'id':77},'chat':{'id':77,'type':'private'},'text':'Привет'}
    Updates(e,API(),999).handle({'update_id':1,'message':m})
    assert len(s.rows('SELECT * FROM events')) == 1 and not s.rows('SELECT * FROM outbox')


def test_restricted_foreign_proposal_collision_cannot_enter_dialogue_focus(intel):
    e, s, c, clock = intel
    c['admins'] = ['other-admin']
    p, ctx, scope = enrich(e,clock[0]); ctx['restricted_proposal_ids'] = {'FOREIGN-TASK-ID'}
    s.queue(e.b,'test_operator','Идея передана руководителю.','neutral',clock[0])
    e.intelligence.remember('neutral',p,ctx,answer(ctx),scope,0,['FOREIGN-TASK-ID'],clock[0])
    s.execute("UPDATE outbox SET state='sent',transport='bot',destination_chat='77'")
    _, later, _ = enrich(e,clock[0])
    assert later['dialogue']['task_id'] is None
    assert 'FOREIGN-TASK-ID' not in json.dumps(later)


def test_stale_task_focus_is_scrubbed_when_task_permissions_no_longer_match(intel):
    e, _, c, clock = intel
    p, _, scope = enrich(e,clock[0])
    e.intelligence.memory.record(scope,'old-focus','срок','какой срок?',at=clock[0],
        state={'topic':'Рабочая тема','entities':[],'pending_offer':None,'task_id':'deleted-or-unpermitted'})
    _, later, _ = enrich(e,clock[0])
    assert later['dialogue']['task_id'] is None


def test_task_context_taint_protects_plain_conversation_output_and_later_memory(intel):
    e, s, _, clock = intel; doc(e,clock[0])
    refs = e.intelligence.index.allowed_ids(e.intelligence.reader('test_operator'),now=clock[0])
    s.execute('INSERT INTO intelligence_task_acl VALUES(?,?,?)',(e.b,'protected-task',json.dumps(sorted(refs))))
    p = payload('Объясни текущую работу')
    context = {'tasks':[{'id':'protected-task'}]}
    scope = e.intelligence.enrich(p,context,clock[0])
    # No matched KB and no previous dialogue; sensitive facts came from task fields.
    context['knowledge'] = {'evidence': [], 'facts': [], 'conflicts': [], 'status':'no_evidence'}
    assert set(context['task_evidence_ids']) == refs
    result = answer(context, reply_text='PRIVATE TASK RESULT', evidence_ids=[], knowledge_mode='not_needed')
    s.queue(e.b,'test_operator',result['reply_text'],'plain-task-conversation',clock[0])
    e.intelligence.remember('plain-task-conversation',p,context,result,scope,0,[],clock[0])
    assert delivery_permitted(s,e.b,'test_operator',1,clock[0])
    s.execute("UPDATE outbox SET state='sent',transport='bot',destination_chat='77'")
    e.intelligence.index.revoke_source('synthetic')
    assert not delivery_permitted(s,e.b,'test_operator',1,clock[0])
    memory=e.intelligence.memory.get(scope,readable_ids=set(),now=clock[0])
    assert memory['turns'] == []
