"""Recorded operational evidence; fictional tasks and captured Telegram only."""

import copy
import json

import pytest

from goldway.dialogue import scope_from_payload
from goldway.engine import Engine
from goldway.intelligence import delivery_permitted
from goldway.model import ScriptedModel
from goldway.telegram import Dispatcher
from goldway.work_context import work_id
from test_intelligence_cycle import answer, snapshot
from test_team_tasks import team, inbound
from test_staff_dates import strict_update


@pytest.fixture
def work_team(team, monkeypatch):
    _, store, config, clock, api = team
    config['intelligence'] = {'enabled': True, 'sources': []}
    engine = Engine(store, config, None, lambda: clock[0])
    monkeypatch.setattr(engine.intelligence.sources, 'refresh', lambda *a, **kw: {})
    return engine, store, config, clock, api


def seed(case, identifier='blocked-task', owner='alen', status='blocked', **fields):
    engine, store, _, clock, _ = case
    row = dict(business=engine.b, id=identifier, title='Учебный запуск каталога',
               owner=owner, deadline='2026-10-13', next_step='Получить учебные фотографии',
               criterion='Учебный каталог опубликован', status=status,
               source_id='synthetic-meeting', evidence='RAW-MEETING-MUST-NOT-BE-READ',
               created=clock[0])
    row.update(fields)
    columns = ','.join(row)
    store.execute(f"INSERT INTO tasks({columns}) VALUES({','.join('?' for _ in row)})", tuple(row.values()))
    return store.rows('SELECT * FROM tasks WHERE business=? AND id=?', (row['business'], identifier))[0]


def request(case, text='Какие есть проблемы в нашем бизнесе?', sender='test_operator', n=401, flush=True):
    engine, store, config, clock, api = case
    event = inbound(case, text, n, sender=sender)
    engine.process(event)
    if flush:
        Dispatcher(store, config, api, lambda: clock[0]).flush()
    return store.rows('SELECT * FROM outbox WHERE business=? AND recipient=? ORDER BY id DESC LIMIT 1',
                      (engine.b, sender))[0]


class WorkAnswer:
    def __init__(self, mode='grounded'):
        self.contexts = []
        self.mode = mode

    def propose(self, context):
        self.contexts.append(copy.deepcopy(context))
        evidence = context.get('work_context', {}).get('evidence', [])
        return answer('По записи задач: запуск каталога заблокирован; нужен учебный материал.',
                      refs=[item['id'] for item in evidence] if self.mode == 'grounded' else (),
                      mode=self.mode, state=snapshot('Рабочие проблемы'))


def test_actual_screenshot_question_receives_task_evidence_not_catalog_only(work_team):
    engine, store, _, _, _ = work_team
    task = seed(work_team)
    engine.model = model = WorkAnswer()
    result = request(work_team)
    evidence = model.contexts[0]['work_context']['evidence']
    assert model.contexts[0]['knowledge']['evidence'] == []
    assert [e['task_id'] for e in evidence] == [task['id']]
    assert evidence[0]['source_kind'] == 'task' and evidence[0]['signals'] == ['blocked', 'overdue']
    assert 'Реестр задач Mike: №1' in result['text'] and 'https://' not in result['text']
    assert result['state'] == 'sent' and result['recipient'] == 'test_operator'
    assert 'RAW-MEETING' not in json.dumps(model.contexts, ensure_ascii=False)
    assert len(engine.tasks()) == 1 and engine.tasks()[0]['revision'] == task['revision']
    assert not store.rows("SELECT * FROM audit WHERE kind='task_changed'")


def test_model_not_found_cannot_hide_accessible_operational_records(work_team):
    engine, _, _, _, _ = work_team
    seed(work_team)
    engine.model = WorkAnswer(mode='not_found')
    result = request(work_team)
    assert 'Учебный запуск каталога' in result['text']
    assert 'это выборка' in result['text'].casefold() and 'полный диагноз' in result['text']
    assert 'нет подтверждённых сведений' not in result['text']
    assert 'Реестр задач Mike: №1' in result['text']


def test_empty_operational_register_explains_actual_limit_not_no_problems(work_team):
    engine, _, _, _, _ = work_team
    engine.model = ScriptedModel(answer('У нас ВЫДУМАННЫЙ-КРИЗИС.', mode='not_found'))
    result = request(work_team)
    assert 'ВЫДУМАННЫЙ' not in result['text']
    assert 'не означает, что проблем нет' in result['text']
    assert 'итоги планёрки' in result['text']
    assert len(engine.tasks()) == 0


def test_employee_operational_question_cannot_retrieve_other_people_work(work_team):
    engine, _, _, _, _ = work_team
    own = seed(work_team)
    seed(work_team, 'private-task', owner='test_operator', title='PRIVATE-OTHER-EMPLOYEE')
    seed(work_team, 'proposal', owner='alen', title='PRIVATE-UNCONFIRMED', status='proposed')
    engine.model = model = WorkAnswer()
    request(work_team, sender='alen')
    context = model.contexts[0]
    assert [e['task_id'] for e in context['work_context']['evidence']] == [own['id']]
    assert context['work_context']['audience_scope'] == 'own_tasks_only'
    assert 'PRIVATE-' not in json.dumps(context, ensure_ascii=False)


@pytest.mark.parametrize('change', ['owner', 'source', 'membership'])
def test_queued_work_answer_rechecks_current_permissions_before_transport(work_team, change):
    engine, store, config, clock, api = work_team
    task = seed(work_team)
    engine.model = WorkAnswer()
    row = request(work_team, sender='alen', flush=False)
    assert delivery_permitted(store, engine.b, 'alen', row['id'], clock[0], config)
    assert not delivery_permitted(store, engine.b, 'alen', row['id'], clock[0])
    if change == 'owner':
        store.execute('UPDATE tasks SET owner=? WHERE business=? AND id=?',
                      ('test_operator', engine.b, task['id']))
    elif change == 'source':
        store.execute('INSERT INTO intelligence_task_acl VALUES(?,?,?)',
                      (engine.b, task['id'], '["missing-current-source-grant"]'))
    else:
        del config['employees']['alen']
    api.calls.clear()
    Dispatcher(store, config, api, lambda: clock[0]).flush()
    assert not api.calls
    assert store.rows('SELECT state,attempts FROM outbox WHERE id=?', (row['id'],)) == [
        {'state': 'blocked', 'attempts': 0}]


def test_pending_work_answer_cannot_be_rerouted_to_another_employee(work_team):
    engine, store, config, clock, api = work_team
    seed(work_team)
    engine.model = WorkAnswer()
    row = request(work_team, flush=False)
    assert not delivery_permitted(store, engine.b, 'frost', row['id'], clock[0], config)
    assert delivery_permitted(store, engine.b, 'test_operator', row['id'], clock[0], config)


def test_snapshot_change_during_model_call_rejects_even_uncited_context(work_team):
    engine, store, _, _, _ = work_team
    task = seed(work_team)
    class ConcurrentChange:
        def propose(self, context):
            store.execute('UPDATE tasks SET next_step=?,revision=revision+1 WHERE business=? AND id=?',
                          ('CHANGED-SYNTHETIC-STEP', engine.b, task['id']))
            return answer('Ничего не изменилось.', mode='not_needed')
    engine.model = ConcurrentChange()
    with pytest.raises(ValueError, match='Work evidence changed'):
        request(work_team, flush=False)
    assert store.rows("SELECT state FROM events WHERE id='update:401'") == [{'state': 'failed'}]
    assert not store.rows("SELECT id FROM outbox WHERE dedupe='clarify:update:401'")


def test_delivered_work_followup_gets_fresh_evidence_and_no_task_authority(work_team):
    engine, store, _, clock, _ = work_team
    task = seed(work_team)
    engine.model = model = WorkAnswer()
    request(work_team, sender='alen')
    clock[0] += 1
    request(work_team, text='Давай', sender='alen', n=402)
    second = model.contexts[-1]
    assert second['followup']['kind'] == 'continue_last_turn'
    assert [e['id'] for e in second['work_context']['evidence']] == [work_id(engine.b, task['id'])]
    assert second['work_context']['evidence'][0]['observed_at'] == clock[0]
    assert engine.tasks()[0]['revision'] == task['revision']


def test_reassignment_removes_work_history_and_followup_from_previous_owner(work_team):
    engine, store, _, clock, _ = work_team
    task = seed(work_team)
    engine.model = WorkAnswer()
    request(work_team, sender='alen')
    store.execute('UPDATE tasks SET owner=? WHERE business=? AND id=?',
                  ('test_operator', engine.b, task['id']))
    clock[0] += 1
    engine.model = model = WorkAnswer(mode='not_needed')
    request(work_team, text='Давай', sender='alen', n=402)
    context = model.contexts[0]
    assert not context['dialogue']['turns'] and 'work_context' not in context
    assert 'followup' not in context
    assert task['title'] not in json.dumps(context, ensure_ascii=False)


@pytest.mark.parametrize('text', ['Создай задачу: проверить рабочие проблемы.',
                                  'Какие бренды мы продаём?', 'Напиши описание для клиента.'])
def test_work_retrieval_does_not_interfere_with_creation_or_catalog_dialogue(work_team, text):
    engine, _, _, _, _ = work_team
    seed(work_team)
    engine.model = model = WorkAnswer(mode='not_needed')
    request(work_team, text=text)
    assert 'work_context' not in model.contexts[0]


def test_work_topic_is_not_carried_into_unrelated_brand_followup(work_team):
    engine, _, _, clock, _ = work_team
    seed(work_team)
    engine.model = WorkAnswer()
    request(work_team)
    clock[0] += 1
    engine.model = ScriptedModel(answer('Нужен подтверждённый справочник.', mode='not_found',
                                       state=snapshot('Бренды')))
    request(work_team, text='Какие бренды?', n=402)
    clock[0] += 1
    engine.model = model = WorkAnswer(mode='not_needed')
    request(work_team, text='Давай', n=403)
    assert 'work_context' not in model.contexts[0]


def test_forged_task_evidence_id_cannot_ground_answer(work_team):
    engine, _, _, _, _ = work_team
    seed(work_team)
    engine.model = ScriptedModel(answer('Чужое закрытое дело.', refs=[work_id(engine.b, 'missing')], mode='grounded'))
    with pytest.raises(ValueError, match='citations'):
        request(work_team, flush=False)


@pytest.mark.parametrize('op', ['update', 'create'])
def test_readonly_operational_question_cannot_be_reclassified_into_mutation(work_team, op):
    engine, _, _, _, _ = work_team
    task = seed(work_team)
    if op == 'update':
        action = strict_update(task, ['next_step'], next_step='MUST-NOT-CHANGE')
    else:
        action = dict(op='create', title='MUST-NOT-CREATE', owner=None, deadline=None,
                      next_step='Не разрешён', criterion='Не разрешён', status='proposed',
                      evidence_lines=[1], owner_lines=[], deadline_lines=[], set_fields=[])
    engine.model = ScriptedModel(answer('Я изменил задачу.', actions=[action], intent='task_action'))
    result = request(work_team)
    assert len(engine.tasks()) == 1 and engine.tasks()[0]['revision'] == task['revision']
    assert engine.tasks()[0]['next_step'] == task['next_step']
    assert 'MUST-NOT-' not in result['text'] and 'Я изменил' not in result['text']
    assert result['state'] == 'sent' and 'Учебный запуск каталога' in result['text']


def test_independent_task_update_drops_prior_overview_content_before_model(work_team):
    engine, store, config, clock, api = work_team
    task = seed(work_team)
    seed(work_team, 'other', owner='frost', title='PRIVATE-UNRELATED-OVERVIEW')
    engine.model = WorkAnswer()
    request(work_team)
    clock[0] += 1
    class IndependentUpdate:
        def propose(self, context):
            assert not context['dialogue']['turns'] and 'work_context' not in context
            assert not context['dialogue_evidence_ids']
            assert 'PRIVATE-UNRELATED' not in json.dumps(context, ensure_ascii=False)
            return answer('Следующий шаг обновлён.',
                          actions=[strict_update(task, ['next_step'], next_step='Учебная сверка трёх пунктов')],
                          intent='task_action')
    engine.model = IndependentUpdate()
    request(work_team, text='Задача №1: следующий шаг — учебная сверка трёх пунктов.', n=402)
    assert engine.intelligence.task_permitted(task['id'], 'alen', clock[0])
    acl = engine.intelligence.task_refs(task['id'])
    assert not any(ref.startswith('work-task:') for ref in acl)
    assert engine.tasks()[0]['next_step'] == 'Учебная сверка трёх пунктов'
    notices = store.rows("SELECT * FROM outbox WHERE recipient='alen' AND task_id=?", (task['id'],))
    assert notices and all(row['state'] == 'sent' for row in notices)


def test_independent_task_creation_cannot_inherit_private_work_overview(work_team):
    engine, _, _, clock, _ = work_team
    seed(work_team, owner='frost', title='PRIVATE-UNRELATED-OVERVIEW')
    engine.model = WorkAnswer()
    request(work_team)
    clock[0] += 1
    class IndependentCreation:
        def propose(self, context):
            # Ordinary task candidates pre-exist, but the private delivered
            # overview and its state/provenance cannot be reused as evidence.
            assert not context['dialogue']['turns']
            assert context['dialogue']['topic'] == ''
            assert not context['dialogue_evidence_ids'] and 'work_context' not in context
            action = dict(op='create', title='Независимый учебный список', owner=None, deadline=None,
                          next_step='Составить независимый учебный список', criterion='Список готов',
                          status='proposed', evidence_lines=[1], owner_lines=[], deadline_lines=[], set_fields=[])
            return answer('Подготовлю черновик.', actions=[action], intent='task_action')
    engine.model = IndependentCreation()
    request(work_team, text='Создай задачу: составить независимый учебный список.', n=402)
    created = next(t for t in engine.tasks() if t['title'] == 'Независимый учебный список')
    assert not any(ref.startswith('work-task:') for ref in engine.intelligence.task_refs(created['id']))


def test_old_history_provenance_budget_does_not_discard_current_work_question(work_team):
    engine, _, _, clock, _ = work_team
    for n in range(5):
        seed(work_team, 'current-' + str(n), title='Учебная рабочая запись ' + str(n))
    intel = engine.intelligence
    for n in range(12):
        intel.index.upsert(dict(source_id='fictional-old', document_id=str(n),
                               title='Учебный старый источник ' + str(n),
                               url='https://example.invalid/old/' + str(n), text='Вымышленный учебный текст.',
                               readers=[intel.reader('test_operator')], observed_at=clock[0]-1,
                               permission_expires_at=clock[0]+1000, confirmed=True))
    refs = intel.index.allowed_ids(intel.reader('test_operator'), now=clock[0])
    p = dict(sender='test_operator', channel='bot', source_id='telegram:77:1',
             text='Какие есть проблемы в нашем бизнесе?')
    scope = scope_from_payload(p, engine.b)
    intel.memory.record(scope, 'old-fictional', 'Старый вопрос', 'Старый ответ.',
                        state=snapshot('Старая тема'), evidence_ids=refs, at=clock[0]-1)
    context = {'tasks': []}
    intel.enrich(p, context, clock[0])
    assert not context['dialogue']['turns'] and not context['dialogue_evidence_ids']
    assert len(context['work_context']['evidence']) == 5
    result = intel.validate(answer('По записям задач нужен разбор.', mode='grounded',
                                   refs=[context['work_context']['evidence'][0]['id']]),
                            context, p, clock[0])
    assert 'Реестр задач Mike' in result['reply_text']


def test_work_followup_remains_readonly_at_provenance_budget_boundary(work_team):
    engine, store, _, clock, _ = work_team
    task = seed(work_team)
    intel = engine.intelligence
    for n in range(16):
        intel.index.upsert(dict(source_id='fictional-task-plan', document_id=str(n),
                               title='Учебный источник ' + str(n),
                               url='https://example.invalid/task/' + str(n), text='Вымышленный учебный текст.',
                               readers=[intel.reader('test_operator')], observed_at=clock[0]-1,
                               permission_expires_at=clock[0]+1000, confirmed=True))
    refs = intel.index.allowed_ids(intel.reader('test_operator'), now=clock[0])
    store.execute('INSERT INTO intelligence_task_acl VALUES(?,?,?)',
                  (engine.b, task['id'], json.dumps(sorted(refs))))
    engine.model = WorkAnswer()
    request(work_team)
    clock[0] += 1
    class MisclassifiedShortening:
        def propose(self, context):
            assert context['work_context']['evidence']
            assert context['tasks'] == []  # Source refs cannot fit beside work ref.
            return answer('Я изменил следующий шаг.', intent='task_action',
                          actions=[strict_update(task, ['next_step'], next_step='MUST-NOT-CHANGE')])
    engine.model = MisclassifiedShortening()
    result = request(work_team, text='Сделай покороче', n=402)
    assert engine.tasks()[0]['revision'] == task['revision']
    assert engine.tasks()[0]['next_step'] == task['next_step']
    assert result['state'] == 'sent' and 'MUST-NOT-CHANGE' not in result['text']
