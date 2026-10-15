"""Regression for stale structured state despite a delivered specific answer."""
import json

import httpx
import pytest

from goldway.model import JsonModel
from test_intelligence_guards import intel, doc, payload, enrich


@pytest.fixture
def focused(intel):
    e, s, c, clock = intel
    doc(e, clock[0])
    doc(e, clock[0], document_id='velon', title='Вымышленный Велон',
        text='Велон — вымышленный второй бренд; учебная сыворотка В-1.')
    refs = e.intelligence.index.allowed_ids(e.intelligence.reader('test_operator'), now=clock[0])
    _, _, scope = enrich(e, clock[0])
    stale = dict(topic='Общий список брендов', entities=['Норива', 'Велон'],
                 pending_offer=None, task_id=None)
    e.intelligence.memory.record(scope, 'broad', 'Какие бренды?',
        'В учебном примере два бренда: Норива и Велон. Рассказать подробнее?',
        state=stale, evidence_ids=refs, at=clock[0])
    clock[0] += 1
    specific = 'Велон: учебная сыворотка В-1. Это вымышленный пример.'
    e.intelligence.memory.record(scope, 'specific', 'О втором бренде',
        specific + '\n\nИсточники:\nhttps://fictional.example.invalid/catalog',
        state=stale, evidence_ids=refs, at=clock[0])
    return e, s, c, clock, scope, specific


@pytest.mark.parametrize('text,kind', [
    ('Сделай покороче', 'shorten_last_answer'),
    ('Откуда ты это знаешь?', 'cite_last_answer'),
    ('Давай', 'continue_last_turn'),
])
def test_delivered_specific_answer_wins_stale_broad_topic(focused, text, kind):
    e, _, _, clock, _, specific = focused
    _, ctx, _ = enrich(e, clock[0], payload(text))
    assert ctx['dialogue']['topic'] == 'Общий список брендов'  # reproduce real failure
    assert ctx['followup']['kind'] == kind
    assert ctx['followup']['target_answer'] == specific
    assert 'Источники:' not in ctx['followup']['target_answer']
    assert not e.tasks()


@pytest.mark.parametrize('text', ['Сделай покороче', 'Откуда ты это знаешь?'])
def test_retrieval_does_not_prioritize_old_list_entities(focused, text, monkeypatch):
    e, _, _, clock, _, specific = focused
    original = e.intelligence.index.search
    calls = []
    def search(query, *args, **kwargs):
        calls.append((query, kwargs['subjects']))
        return original(query, *args, **kwargs)
    monkeypatch.setattr(e.intelligence.index, 'search', search)
    _, ctx, _ = enrich(e, clock[0], payload(text))
    assert specific in calls[0][0]
    assert 'Общий список брендов' not in calls[0][0]
    assert calls[0][1] == []
    assert any(x['document_id'] == 'velon' for x in ctx['knowledge']['evidence'])


def test_selected_second_brand_has_explicit_focus(focused):
    e, _, _, clock, scope, _ = focused
    e.intelligence.memory.record(scope, 'new-list', 'Перечисли ещё раз',
        'Первый бренд Норива. Второй бренд Велон.', at=clock[0],
        state=dict(topic='Список', entities=['Норива', 'Велон'], pending_offer=None, task_id=None))
    _, ctx, _ = enrich(e, clock[0], payload('Расскажи подробнее о втором бренде'))
    assert ctx['followup']['selected_entity'] == 'Велон'
    assert any(x['document_id'] == 'velon' for x in ctx['knowledge']['evidence'])


def test_continue_can_retrieve_current_brand_outside_stale_entities(focused):
    e, _, _, clock, scope, _ = focused
    doc(e, clock[0], document_id='mayak', title='Вымышленный Маяк',
        text='Маяк — вымышленный бренд; учебная пудра М-9.')
    e.intelligence.memory.record(scope, 'new-brand', 'Расскажи о Маяке',
        'Маяк выпускает учебную пудру М-9. Рассказать подробнее?', at=clock[0],
        state=dict(topic='Старый список', entities=['Норива', 'Велон'], pending_offer=None, task_id=None))
    _, ctx, _ = enrich(e, clock[0], payload('Давай'))
    assert ctx['followup']['kind'] == 'continue_last_turn'
    assert any(x['document_id'] == 'mayak' for x in ctx['knowledge']['evidence'])


def test_stale_ordinal_cannot_force_wrong_brand_retrieval(focused):
    e, _, _, clock, scope, _ = focused
    e.intelligence.memory.record(scope, 'changed-list', 'Измени список',
        'Первый бренд Норива. Второй бренд Маяк.', at=clock[0],
        state=dict(topic='Старый список', entities=['Норива', 'Велон'], pending_offer=None, task_id=None))
    _, ctx, _ = enrich(e, clock[0], payload('Расскажи подробнее о втором бренде'))
    assert ctx['followup']['kind'] == 'clarify_entity'
    assert ctx['followup']['selected_entity'] is None


def test_revoked_memory_cannot_supply_followup_even_in_reused_context(focused):
    e, _, _, clock, *_ = focused
    p, ctx, _ = enrich(e, clock[0], payload('Сделай покороче'))
    assert 'followup' in ctx
    e.intelligence.index.revoke_source('synthetic')
    e.intelligence.enrich(p, ctx, clock[0])
    assert not ctx['dialogue']['turns']
    assert 'followup' not in ctx
    assert 'учебная сыворотка В-1' not in json.dumps(ctx, ensure_ascii=False)


@pytest.mark.parametrize('change', ['chat', 'sender'])
def test_focus_cannot_cross_user_or_chat(focused, change):
    e, _, _, clock, *_ = focused
    p = payload('Сделай покороче', chat=88 if change == 'chat' else 77)
    if change == 'sender':
        p['sender'] = 'another-synthetic-member'
    _, ctx, _ = enrich(e, clock[0], p)
    assert not ctx['dialogue']['turns']
    assert 'followup' not in ctx


@pytest.mark.parametrize('state', ['pending', 'unknown', 'sending', 'failed'])
def test_unconfirmed_outbox_answer_cannot_replace_actual_focus(focused, state):
    e, s, _, clock, scope, specific = focused
    s.queue(e.b, 'test_operator', 'UNDELIVERED-ANSWER', 'undelivered-focus', clock[0])
    identifier = s.execute('SELECT MAX(id) FROM outbox').fetchone()[0]
    s.execute('UPDATE outbox SET state=? WHERE id=?', (state, identifier))
    e.intelligence.memory.record(scope, 'undelivered', 'Вопрос', 'UNDELIVERED-ANSWER',
        at=clock[0], state=dict(topic='UNDELIVERED', entities=[], pending_offer=None, task_id=None),
        outbox_ids=[identifier])
    _, ctx, _ = enrich(e, clock[0], payload('Сделай покороче'))
    assert ctx['followup']['target_answer'] == specific
    assert 'UNDELIVERED' not in json.dumps(ctx, ensure_ascii=False)


def test_real_provider_payload_receives_current_focus(focused, monkeypatch):
    e, s, c, clock, _, specific = focused
    _, ctx, _ = enrich(e, clock[0], payload('Сделай покороче'))
    seen = []
    def transport(request):
        data = json.loads(request.content)
        sent_context = json.loads(data['messages'][-1]['content'])
        seen.append(sent_context['followup']['target_answer'])
        result = {'actions': [], 'summary': 'Сокращено', 'reply_text': 'Велон: учебная сыворотка В-1.',
                  'intent': 'conversation', 'knowledge_mode': 'not_needed', 'evidence_ids': [],
                  'dialogue_state': {'topic': 'Велон', 'entities': ['Велон'], 'pending_offer': None, 'task_id': None}}
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(result)}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 10}})
    client = httpx.Client(transport=httpx.MockTransport(transport))
    monkeypatch.setattr('goldway.model.httpx.Client', lambda **kw: client)
    result = JsonModel(s, e.b, c['model'], 'fictional-key', lambda: clock[0]).propose(ctx)
    assert seen == [specific]
    assert not result['actions']
