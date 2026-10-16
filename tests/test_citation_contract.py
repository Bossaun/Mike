"""Current-source provider contract and fail-closed conversation regressions."""
import json

import httpx
import pytest

import goldway.model as module
from goldway.model import JsonModel, response_schema
from test_intelligence_guards import intel, doc, enrich, answer


def test_schema_excludes_historical_and_foreign_evidence(intel):
    e, _, _, clock = intel
    doc(e, clock[0])
    _, ctx, _ = enrich(e, clock[0])
    ctx['dialogue_evidence_ids'] = ['historic-private']
    ctx['task_evidence_ids'] = ['foreign-task']
    ctx['work_context'] = {'evidence': [{'id': 'current-work'}]}
    accepted = response_schema(ctx)['properties']['evidence_ids']['items']['enum']
    assert set(accepted) == {r['id'] for r in ctx['knowledge']['evidence']} | {'current-work'}
    assert 'historic-private' not in accepted and 'foreign-task' not in accepted


def test_empty_sources_cannot_be_cited(intel):
    e, _, _, clock = intel
    _, ctx, _ = enrich(e, clock[0])
    refs = response_schema(ctx)['properties']['evidence_ids']
    assert refs['maxItems'] == 0 and 'enum' not in refs['items']


@pytest.mark.parametrize('mode,refs', [('grounded', ['invented']), ('grounded', []),
                                      ('not_needed', ['invented'])])
def test_bad_citations_preserve_failure_and_never_apply_model_action(intel, mode, refs):
    e, s, _, clock = intel
    doc(e, clock[0])
    class InvalidSourceModel:
        def propose(self, ctx):
            return answer(ctx, knowledge_mode=mode, evidence_ids=refs,
                          reply_text='UNVERIFIED MODEL TEXT', actions=[{'op': 'create'}])
    e.model = InvalidSourceModel()
    assert e.reply('citation-contract', 'test_operator', 'Какие бренды?',
                   source_id='telegram:77:1', channel='bot')
    with pytest.raises(ValueError):
        e.process('citation-contract')
    assert not e.tasks()
    event = s.rows('SELECT state,error FROM events')[0]
    assert event == {'state': 'failed', 'error': 'ValueError: knowledge_citations'}
    messages = s.rows('SELECT text,dedupe FROM outbox')
    assert len(messages) == 1 and messages[0]['dedupe'] == 'error:citation-contract'
    assert 'источники' in messages[0]['text']
    assert 'UNVERIFIED' not in messages[0]['text'] and 'чуть позже' not in messages[0]['text']
    assert not s.rows("SELECT * FROM runtime WHERE key LIKE 'dialogue:v1:%'")


def test_actual_request_contains_enum_and_schema_cost_is_reserved(intel, monkeypatch):
    e, s, c, clock = intel
    doc(e, clock[0]); _, ctx, _ = enrich(e, clock[0])
    observed = []
    def respond(request):
        value = json.loads(request.content)
        observed.append(value)
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(answer(ctx))},
                                                     'finish_reason': 'stop'}],
                                        'usage': {'prompt_tokens': 10, 'completion_tokens': 5}})
    client = httpx.Client(transport=httpx.MockTransport(respond))
    monkeypatch.setattr(module.httpx, 'Client', lambda **kw: client)
    config = dict(c['model'], provider='openai', json_mode=False,
                  input_per_million=1, output_per_million=0, daily_budget=10)
    JsonModel(s, e.b, config, 'synthetic-key', lambda: clock[0]).propose(ctx)
    request = observed[0]
    schema = request['response_format']['json_schema']['schema']
    assert schema['properties']['evidence_ids']['items']['enum']
    size = sum(len(m['content'].encode()) for m in request['messages'])
    size += len(json.dumps(schema, ensure_ascii=False).encode())
    assert s.rows('SELECT reserved_cost FROM usage')[0]['reserved_cost'] >= size / 1e6
