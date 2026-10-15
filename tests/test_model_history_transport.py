"""Provider history contracts using synthetic text and mocked HTTP only."""

import copy
import json

import httpx
import pytest

import goldway.model as module
from goldway.model import JsonModel
from goldway.responses import responses_payload


ROUTES = ('openai_chat', 'anthropic', 'openai_responses')
RESULT = {'actions': [], 'summary': 'Синтетический итог'}
SCHEMA = {'type': 'object', 'properties': {'summary': {'type': 'string'}},
          'required': ['summary'], 'additionalProperties': False}


def history():
    return [{'role': 'user', 'content': 'USER-HISTORY-1: расскажи о вымышленном товаре'},
            {'role': 'assistant', 'content': 'ASSISTANT-HISTORY-1: первый ответ 🙂'},
            {'role': 'user', 'content': 'USER-HISTORY-2: а второй вариант?'},
            {'role': 'assistant', 'content': 'ASSISTANT-HISTORY-2: второй ответ\n«цитата»'}]


def context(turns=None):
    return {'kind': 'operator_chat', 'intelligence': True,
            'text': '1 | Сделай последний ответ короче', 'tasks': [],
            'dialogue': {'turns': history() if turns is None else turns,
                         'topic': 'Вымышленный товар', 'entities': ['Первый', 'Второй'],
                         'pending_offer': None, 'task_id': None}}


def current_context(value):
    result = copy.deepcopy(value)
    result['dialogue']['turns'] = []
    return result


def config(base, route, **changes):
    result = dict(base, provider='anthropic' if route == 'anthropic' else 'openai',
                  api='responses' if route == 'openai_responses' else 'chat_completions',
                  max_context_chars=120000, daily_budget=10)
    result.update(changes)
    return result


def response(route):
    raw = json.dumps(RESULT, ensure_ascii=False)
    if route == 'anthropic':
        return {'content': [{'type': 'text', 'text': raw}],
                'usage': {'input_tokens': 17, 'output_tokens': 5}}
    if route == 'openai_responses':
        return {'status': 'completed', 'output': [
            {'type': 'message', 'role': 'assistant', 'status': 'completed',
             'content': [{'type': 'output_text', 'text': raw}]}],
            'usage': {'input_tokens': 17, 'output_tokens': 5}}
    return {'choices': [{'message': {'content': raw}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 17, 'completion_tokens': 5}}


def mock_http(monkeypatch, route):
    requests = []
    client_class = httpx.Client

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=response(route))

    def client(**kwargs):
        return client_class(transport=httpx.MockTransport(handle))

    monkeypatch.setattr(module.httpx, 'Client', client)
    return requests


def messages(route, payload):
    if route == 'openai_responses':
        return payload['input']
    if route == 'openai_chat':
        return payload['messages'][1:]
    return payload['messages']


@pytest.mark.parametrize('route', ROUTES)
def test_actual_history_roles_precede_one_current_context_without_mutating_source(env, monkeypatch, route):
    engine, store, base, clock = env
    requests = mock_http(monkeypatch, route)
    value, cfg = context(), config(base['model'], route)
    cfg.update(store=True, previous_response_id='PRIVATE-REMOTE-HISTORY',
               conversation='PRIVATE-CONVERSATION', tools=[{'type': 'web_search'}],
               messages=[{'role': 'system', 'content': 'PRIVATE-CONFIG-MESSAGES'}])
    before, config_before = copy.deepcopy(value), copy.deepcopy(cfg)
    assert JsonModel(store, engine.b, cfg, 'synthetic-key', lambda: clock[0]).propose(value) == RESULT
    assert value == before and cfg == config_before
    assert len(requests) == 1
    payload = json.loads(requests[0].content)
    actual = messages(route, payload)
    assert actual[:-1] == before['dialogue']['turns']
    assert [item['role'] for item in actual] == ['user', 'assistant', 'user', 'assistant', 'user']
    assert json.loads(actual[-1]['content']) == current_context(before)
    assert all(item['content'] not in actual[-1]['content'] for item in actual[:-1])
    assert not {'tools', 'previous_response_id', 'conversation'} & payload.keys()
    assert 'PRIVATE-' not in requests[0].content.decode()
    expected_system = module.SYSTEM + module.INTELLIGENCE_SYSTEM
    if route == 'openai_chat':
        assert requests[0].url.path == '/v1/chat/completions'
        assert payload['messages'][0] == {'role': 'system', 'content': expected_system}
    elif route == 'anthropic':
        assert requests[0].url.path == '/v1/messages'
        assert payload['system'] == expected_system
    else:
        assert requests[0].url.path == '/v1/responses'
        assert payload['instructions'] == expected_system and payload['store'] is False
        assert 'messages' not in payload
    usage = store.rows('SELECT * FROM usage')[0]
    assert usage['input_tokens'] == 17 and usage['output_tokens'] == 5


@pytest.mark.parametrize('route', ROUTES)
@pytest.mark.parametrize('mode', ['without_intelligence', 'without_dialogue', 'without_turns'])
def test_legacy_context_keeps_single_user_message_and_original_json(env, monkeypatch, route, mode):
    engine, store, base, clock = env
    requests = mock_http(monkeypatch, route)
    value = context()
    if mode == 'without_intelligence':
        value.pop('intelligence')
    elif mode == 'without_dialogue':
        value.pop('dialogue')
    else:
        value['dialogue'].pop('turns')
    before = copy.deepcopy(value)
    JsonModel(store, engine.b, config(base['model'], route), 'synthetic-key',
              lambda: clock[0]).propose(value)
    actual = messages(route, json.loads(requests[0].content))
    assert len(actual) == 1 and actual[0]['role'] == 'user'
    assert json.loads(actual[0]['content']) == before and value == before


VALID_PAIR = [{'role': 'user', 'content': 'u'}, {'role': 'assistant', 'content': 'a'}]
INVALID_HISTORY = [
    pytest.param(None, id='none'),
    pytest.param({}, id='mapping'),
    pytest.param('PRIVATE-HISTORY', id='string'),
    pytest.param(VALID_PAIR[:1], id='odd-turn-count'),
    pytest.param(VALID_PAIR * 26, id='over-fifty-turns'),
    pytest.param([None, VALID_PAIR[1]], id='nondict-turn'),
    pytest.param([dict(VALID_PAIR[0], extra='PRIVATE'), VALID_PAIR[1]], id='extra-key'),
    pytest.param([{'role': 'user'}, VALID_PAIR[1]], id='missing-content'),
    pytest.param([{'content': 'u'}, VALID_PAIR[1]], id='missing-role'),
    pytest.param([VALID_PAIR[1], VALID_PAIR[0]], id='assistant-start-user-end'),
    pytest.param([VALID_PAIR[0], VALID_PAIR[0]], id='wrong-alternation'),
    pytest.param([{'role': 'system', 'content': 'PRIVATE'}, VALID_PAIR[1]], id='system-role'),
    pytest.param([VALID_PAIR[0], {'role': 'tool', 'content': 'PRIVATE'}], id='tool-role'),
    pytest.param([{'role': ['user'], 'content': 'PRIVATE'}, VALID_PAIR[1]], id='nonstring-role'),
    pytest.param([{'role': 'user', 'content': ''}, VALID_PAIR[1]], id='empty-content'),
    pytest.param([VALID_PAIR[0], {'role': 'assistant', 'content': ' \t\n'}], id='blank-content'),
    pytest.param([{'role': 'user', 'content': 7}, VALID_PAIR[1]], id='nonstring-content'),
    pytest.param([{'role': 'user', 'content': 'я' * 30001},
                  {'role': 'assistant', 'content': 'a' * 30000}], id='over-sixty-thousand-chars'),
]


@pytest.mark.parametrize('route', ROUTES)
@pytest.mark.parametrize('turns', INVALID_HISTORY)
def test_invalid_history_rejects_before_transport_or_usage_reservation(env, monkeypatch, route, turns):
    engine, store, base, clock = env
    requests = mock_http(monkeypatch, route)
    value = context([])
    value['dialogue']['turns'] = copy.deepcopy(turns)
    before = copy.deepcopy(value)
    with pytest.raises(ValueError):
        JsonModel(store, engine.b, config(base['model'], route), 'synthetic-key',
                  lambda: clock[0]).propose(value)
    assert value == before
    assert requests == [] and not store.rows('SELECT * FROM usage')


@pytest.mark.parametrize('turns', INVALID_HISTORY[1:])
def test_responses_helper_applies_same_history_validation(turns):
    with pytest.raises(ValueError):
        responses_payload({'model': 'synthetic-model'}, 'SYSTEM', '{}', SCHEMA,
                          history=copy.deepcopy(turns))


def test_responses_helper_copies_history_and_keeps_remote_state_disabled():
    turns = history()
    before = copy.deepcopy(turns)
    cfg = {'model': 'synthetic-model', 'store': True,
           'previous_response_id': 'PRIVATE-PREVIOUS', 'conversation': 'PRIVATE-CONVERSATION'}
    schema_before, config_before = copy.deepcopy(SCHEMA), copy.deepcopy(cfg)
    payload = responses_payload(cfg, 'SYSTEM', '{"current":true}', SCHEMA, history=turns)
    assert payload['input'] == turns + [{'role': 'user', 'content': '{"current":true}'}]
    assert payload['store'] is False and payload['instructions'] == 'SYSTEM'
    assert not {'tools', 'messages', 'previous_response_id', 'conversation'} & payload.keys()
    payload['input'][0]['content'] = 'changed'
    payload['input'].append({'role': 'assistant', 'content': 'changed'})
    payload['text']['format']['schema']['required'].append('changed')
    assert turns == before and SCHEMA == schema_before and cfg == config_before


@pytest.mark.parametrize('route', ROUTES)
def test_history_counts_toward_context_cap_after_extraction(env, monkeypatch, route):
    engine, store, base, clock = env
    requests = mock_http(monkeypatch, route)
    value = context([{'role': 'user', 'content': 'u' * 2500},
                     {'role': 'assistant', 'content': 'a' * 2500}])
    current_chars = len(json.dumps(current_context(value), ensure_ascii=False))
    cfg = config(base['model'], route, max_context_chars=current_chars + 2500)
    with pytest.raises(ValueError, match='context'):
        JsonModel(store, engine.b, cfg, 'synthetic-key', lambda: clock[0]).propose(value)
    assert requests == [] and not store.rows('SELECT * FROM usage')


def input_minimum(value):
    current = len(json.dumps(current_context(value), ensure_ascii=False).encode())
    system = len((module.SYSTEM + module.INTELLIGENCE_SYSTEM).encode())
    history_bytes = sum(len(turn['content'].encode()) for turn in value['dialogue']['turns'])
    return current + system + 1024, history_bytes


@pytest.mark.parametrize('route', ROUTES)
def test_reservation_conservatively_includes_all_unicode_history_bytes(env, monkeypatch, route):
    engine, store, base, clock = env
    requests = mock_http(monkeypatch, route)
    value = context()
    cfg = config(base['model'], route, input_per_million=1, output_per_million=0)
    JsonModel(store, engine.b, cfg, 'synthetic-key', lambda: clock[0]).propose(value)
    baseline, history_bytes = input_minimum(value)
    assert len(requests) == 1 and history_bytes > sum(len(turn['content']) for turn in history())
    assert store.rows('SELECT reserved_cost FROM usage')[0]['reserved_cost'] >= (baseline + history_bytes) / 1e6


@pytest.mark.parametrize('route', ROUTES)
def test_expensive_history_blocks_provider_before_reservation(env, monkeypatch, route):
    engine, store, base, clock = env
    requests = mock_http(monkeypatch, route)
    value = context([{'role': 'user', 'content': 'я' * 10000},
                     {'role': 'assistant', 'content': '🙂' * 10000}])
    baseline, history_bytes = input_minimum(value)
    budget = (baseline + history_bytes / 2) / 1e6
    assert baseline / 1e6 < budget < (baseline + history_bytes) / 1e6
    cfg = config(base['model'], route, input_per_million=1,
                 output_per_million=0, daily_budget=budget)
    with pytest.raises(ValueError, match='budget'):
        JsonModel(store, engine.b, cfg, 'synthetic-key', lambda: clock[0]).propose(value)
    assert requests == [] and not store.rows('SELECT * FROM usage')


def test_history_participates_in_long_context_pricing_threshold(env, monkeypatch):
    engine, store, base, clock = env
    route = 'openai_responses'
    mock_http(monkeypatch, route)
    value = context([{'role': 'user', 'content': 'я' * 2000},
                     {'role': 'assistant', 'content': '🙂' * 2000}])
    baseline, history_bytes = input_minimum(value)
    cfg = config(base['model'], route, input_per_million=2, output_per_million=3,
                 max_output_tokens=32, long_context_threshold=baseline + history_bytes // 2,
                 long_input_multiplier=2, long_output_multiplier=1.5)
    JsonModel(store, engine.b, cfg, 'synthetic-key', lambda: clock[0]).propose(value)
    minimum = ((baseline + history_bytes) * 4 + 32 * 4.5) / 1e6
    assert store.rows('SELECT reserved_cost FROM usage')[0]['reserved_cost'] >= minimum


def test_exact_history_count_and_character_boundaries_remain_usable(env, monkeypatch):
    engine, store, base, clock = env
    requests = mock_http(monkeypatch, 'openai_responses')
    turns = [{'role': 'user' if index % 2 == 0 else 'assistant', 'content': 'я' * 1200}
             for index in range(50)]
    value, before = context(turns), copy.deepcopy(turns)
    JsonModel(store, engine.b, config(base['model'], 'openai_responses'),
              'synthetic-key', lambda: clock[0]).propose(value)
    payload = json.loads(requests[0].content)
    assert payload['input'][:-1] == before and len(payload['input']) == 51
    assert sum(len(turn['content']) for turn in before) == 60000
    assert value['dialogue']['turns'] == before


def test_explicit_empty_history_is_valid_and_preserves_current_context(env, monkeypatch):
    engine, store, base, clock = env
    requests = mock_http(monkeypatch, 'openai_chat')
    value, before = context([]), context([])
    JsonModel(store, engine.b, config(base['model'], 'openai_chat'),
              'synthetic-key', lambda: clock[0]).propose(value)
    payload = json.loads(requests[0].content)
    assert len(payload['messages']) == 2
    assert json.loads(payload['messages'][-1]['content']) == before and value == before
