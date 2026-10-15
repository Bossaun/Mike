"""Durable task provenance with fictional documents and synthetic Telegram only."""

import json

import pytest

from goldway.engine import Engine
from goldway.intelligence import delivery_permitted
from goldway.model import ScriptedModel
from goldway.telegram import Dispatcher
from test_team_tasks import team, inbound


SOURCE = 'fictional-task-plan'
PRIVATE_TITLE = 'PRIVATE-KNOWLEDGE-PLAN'


@pytest.fixture
def knowledge_team(team, monkeypatch):
    _, store, config, clock, api = team
    config['intelligence'] = {'enabled': True, 'sources': []}
    engine = Engine(store, config, None, lambda: clock[0])
    monkeypatch.setattr(engine.intelligence.sources, 'refresh', lambda *args, **kwargs: {})
    return engine, store, config, clock, api


def response(actions=(), refs=()):
    return dict(actions=list(actions), intent='task_action', summary='Вымышленный рабочий итог',
                reply_text='Поручение обработано.', evidence_ids=list(refs),
                knowledge_mode='grounded' if refs else 'not_needed',
                dialogue_state=dict(topic='Норива', entities=['Норива'], pending_offer=None, task_id=None))


def grounded_task(case, readers=('test_operator',)):
    engine, store, config, clock, api = case
    intel = engine.intelligence
    intel.index.upsert(dict(source_id=SOURCE, document_id='plan', title='Норива: вымышленный план',
        url='https://fictional.example.invalid/plan', text='Норива: внутренний план ' + PRIVATE_TITLE + '.',
        readers=[intel.reader(reader) for reader in readers], observed_at=clock[0] - 1,
        permission_expires_at=clock[0] + 7 * 86400, confirmed=True))
    refs = intel.index.allowed_ids(intel.reader('test_operator'), now=clock[0])
    action = dict(op='create', title=PRIVATE_TITLE, owner='alen', owner_lines=[1],
        deadline=None, deadline_lines=[], next_step='Подготовить вымышленный план',
        criterion='План сохранён', status='proposed', evidence_lines=[1], set_fields=[])
    engine.model = ScriptedModel(response([action], refs))
    engine.process(inbound(case, 'Создай задачу: подготовить план Норива. Ответственный Алэн.', 201))
    Dispatcher(store, config, api, lambda: clock[0]).flush()
    task = engine.tasks()[0]
    engine.model = ScriptedModel(response([dict(op='update', task_id=task['id'], status='confirmed',
        evidence_lines=[1], set_fields=['status'])]))
    engine.process(inbound(case, 'Задача №1: подтверждаю поручение.', 202))
    Dispatcher(store, config, api, lambda: clock[0]).flush()
    return engine.tasks()[0]


def add_ordinary_task(case):
    engine, store, config, clock, api = case
    store.execute('''INSERT INTO tasks(business,id,title,owner,next_step,criterion,status,source_id,evidence,created)
        VALUES(?,?,?,?,?,?,?,?,?,?)''', (engine.b, 'ordinary-task', 'Обычная публичная задача', 'alen',
        'Проверить три пункта', 'Три пункта проверены', 'confirmed', 'fictional-meeting',
        'Вымышленное поручение', clock[0]))


def revoke(case):
    engine, store, config, clock, api = case
    engine.intelligence.index.revoke_source(SOURCE, observed_at=clock[0])


def test_nonreader_never_receives_a_later_reminder_with_a_private_task_title(knowledge_team):
    engine, store, config, clock, api = knowledge_team
    task = grounded_task(knowledge_team)
    assignment = store.rows("SELECT * FROM outbox WHERE dedupe LIKE 'assignment:%'")[0]
    assert assignment['recipient'] == 'alen' and assignment['state'] == 'blocked'
    assert assignment['attempts'] == 0
    assert not delivery_permitted(store, engine.b, 'alen', assignment['id'], clock[0])
    add_ordinary_task(knowledge_team)
    clock[0] += 86401
    engine.tick()
    reminders = store.rows("SELECT * FROM outbox WHERE dedupe LIKE 'reminder:%'")
    assert [row['task_id'] for row in reminders] == ['ordinary-task']
    assert all(PRIVATE_TITLE not in row['text'] for row in reminders)
    assert next(t for t in engine.tasks() if t['id'] == task['id'])['last_reminder'] is None


@pytest.mark.parametrize('sender', ['test_operator', 'alen'])
def test_revoked_task_metadata_never_enters_a_future_model_context(knowledge_team, sender):
    engine, store, config, clock, api = knowledge_team
    task = grounded_task(knowledge_team, readers=('test_operator', 'alen'))
    assert engine.intelligence.task_permitted(task['id'], sender, clock[0])
    revoke(knowledge_team)
    seen = []
    class Observed:
        def propose(self, context):
            seen.append(context)
            assert context['tasks'] == []
            assert context['dialogue']['task_id'] is None
            assert PRIVATE_TITLE not in json.dumps(context, ensure_ascii=False)
            return dict(response(), intent='conversation', reply_text='Доступных поручений сейчас нет.')
    engine.model = Observed()
    engine.process(inbound(knowledge_team, 'Как идут мои задачи?', 203, sender=sender))
    assert len(seen) == 1
    assert engine.tasks()[0]['id'] == task['id']


def test_summaries_filter_current_task_access_and_keep_ordinary_work_visible(knowledge_team):
    engine, store, config, clock, api = knowledge_team
    grounded_task(knowledge_team)
    add_ordinary_task(knowledge_team)
    assert PRIVATE_TITLE in engine.summary('test_operator')
    assert PRIVATE_TITLE not in engine.summary('alen')
    revoke(knowledge_team)
    summary = engine.summary('test_operator')
    assert PRIVATE_TITLE not in summary and 'Обычная публичная задача' in summary


@pytest.mark.parametrize('output_kind', ['summary', 'meeting_review'])
@pytest.mark.parametrize('enabled_at_send', [True, False])
def test_queued_unlinked_task_reports_recheck_acl_before_transport(knowledge_team, output_kind, enabled_at_send):
    engine, store, config, clock, api = knowledge_team
    task = grounded_task(knowledge_team, readers=('test_operator', 'alen'))
    first_id = store.execute('SELECT MAX(id) FROM outbox').fetchone()[0]
    if output_kind == 'summary':
        engine.queue_summary('fictional-protected-summary')
    else:
        engine.queue_meeting_review([task], 'fictional-protected-review', 'test_operator')
    queued = store.rows('SELECT * FROM outbox WHERE id>?', (first_id,))
    assert len(queued) == 1 and queued[0]['task_id'] is None and PRIVATE_TITLE in queued[0]['text']
    revoke(knowledge_team)
    config['intelligence']['enabled'] = enabled_at_send
    api.calls.clear()
    Dispatcher(store, config, api, lambda: clock[0]).flush()
    assert not api.calls
    row = store.rows('SELECT state,attempts FROM outbox WHERE id=?', (queued[0]['id'],))[0]
    assert row == {'state': 'blocked', 'attempts': 0}


@pytest.mark.parametrize('enabled_at_send', [True, False])
def test_task_link_alone_protects_later_outputs_without_an_event_acl(knowledge_team, enabled_at_send):
    engine, store, config, clock, api = knowledge_team
    task = grounded_task(knowledge_team, readers=('test_operator', 'alen'))
    store.queue(engine.b, 'alen', PRIVATE_TITLE, 'fictional-later-task-output', clock[0], task['id'])
    queued = store.rows("SELECT * FROM outbox WHERE dedupe='fictional-later-task-output'")[0]
    assert not store.rows('SELECT * FROM intelligence_outbox_acl WHERE outbox_id=?', (queued['id'],))
    assert delivery_permitted(store, engine.b, 'alen', queued['id'], clock[0])
    revoke(knowledge_team)
    config['intelligence']['enabled'] = enabled_at_send
    api.calls.clear()
    Dispatcher(store, config, api, lambda: clock[0]).flush()
    assert not api.calls
    assert store.rows('SELECT state,attempts FROM outbox WHERE id=?', (queued['id'],)) == [
        {'state': 'blocked', 'attempts': 0}]
