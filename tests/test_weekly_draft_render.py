"""Renderer boundary only; canonical synthetic progress from the existing core."""

import socket
import sqlite3
import time
from dataclasses import dataclass, replace
from datetime import date
from html.parser import HTMLParser

import pytest

from goldway.meeting_review import (
    Agreement, AgreementProgress, Claim, Meeting, ResultRecord, ReviewError,
    Source, Span, TaskProgress, TaskRecord, compare_week,
)
from goldway.meeting_week_adapter import LIMITATION, WeeklyDraft
import goldway.weekly_draft_render as renderer
from goldway.weekly_draft_render import render_weekly_draft


DAY, END = date(2026, 10, 12), date(2026, 10, 19)
NOW = 1792152000.0


def canonical(state='completed', *, record='completed', task_id='T1', source_id='source', source_quote='Переданная договорённость.'):
    source = Source(source_id, 'SYNTHETIC', DAY, (source_quote, 'Иной вариант решения.'))
    bound = Span(source.id, 1, 1, source.lines[0])
    agreement = Agreement('A', 'decision', 'Подготовить список', (bound,), True)
    claims = (Claim('C', 'decision', 'Отменить список', (Span(source.id, 2, 2, source.lines[1]),), True),) if state == 'contradiction' else ()
    meeting = Meeting('M', 'SYNTHETIC', DAY, (source,), (agreement,), claims=claims)
    tasks = () if state == 'unlinked' else (TaskRecord(task_id, 'SYNTHETIC', 'A', record, (bound,)),)
    sources, results = [source], []
    if state not in ('missing_results', 'unlinked'):
        outcomes = ('completed', 'blocked') if state == 'conflict' else ('completed' if state == 'contradiction' else state,)
        for index, outcome in enumerate(outcomes):
            report = Source(f'report-{index}', 'SYNTHETIC', date(2026, 10, 15),
                            ('Переданное сообщение: ' + outcome,))
            sources.append(report)
            results.append(ResultRecord(f'R{index}', 'SYNTHETIC', task_id, report.on,
                                        outcome, (Span(report.id, 1, 1, report.lines[0]),)))
    progress = compare_week(meeting, start=DAY, end=END, tasks=tasks,
                            results=tuple(results), sources=tuple(sources))
    return WeeklyDraft('SYNTHETIC', 'M', DAY, END, progress, NOW)


def text(draft):
    return '\n'.join(render_weekly_draft(draft))


@pytest.mark.parametrize(('state', 'phrase'), [
    ('completed', 'зарегистрированы сообщения о выполнении'),
    ('blocked', 'в зарегистрированных сообщениях есть препятствие'),
    ('in_progress', 'зарегистрировано продолжение работы'),
    ('missing_results', 'не по всем связанным задачам переданы датированные результаты'),
    ('unlinked', 'связь с задачами не передана'),
    ('conflict', 'зарегистрированные результаты противоречат друг другу'),
    ('contradiction', 'в исходной договорённости есть противоречие; итог не выбран'),
])
def test_canonical_progress_is_rendered_without_inventing_a_resolution(state, phrase):
    out = text(canonical(state))
    assert phrase in out and 'Черновик для проверки' in out
    assert 'Выполнение независимо не проверено' in out
    assert 'Назначений и изменений нет' in out
    assert '12.10.2026–18.10.2026' in out
    assert 'Договорённость 1' in out


def test_completed_record_without_result_does_not_become_weekly_completion():
    out = text(canonical('missing_results', record='completed'))
    assert 'Статус переданной записи: выполнено.' in out
    assert 'За период: датированный результат за период не передан.' in out
    assert 'зарегистрировано сообщение о выполнении' not in out


def test_empty_progress_is_unknown_coverage_not_absence_of_work():
    out = text(replace(canonical(), progress=()))
    assert 'Договорённости для сравнения не переданы' in out
    assert 'не подтверждение отсутствия работы' in out


def test_html_escaping_and_exact_source_identifiers():
    draft = canonical(source_id='A  <B>&"', source_quote='<tag> & "цитата"')
    out = text(draft)
    assert 'Источник 1 · строки 1–1' in out
    assert 'A  &lt;B&gt;' not in out and 'A  <B>' not in out
    assert '&lt;tag&gt; &amp; &quot;цитата&quot;' in out and '<tag>' not in out
    assert 'Фрагмент по связи задачи/сообщения' in out
    assert 'Сообщение о результате 1 (по переданной связи)' in out


def test_distinct_internal_source_spaces_remain_distinct():
    draft = canonical()
    task = draft.progress[0].tasks[0]
    spans = (Span('A  B', 1, 1, '<tag>'), Span('A B', 1, 1, '&other'))
    changed = replace(task, spans=spans)
    out = text(replace(draft, progress=(replace(draft.progress[0], tasks=(changed,)),)))
    assert 'Источник 1 · строки 1–1' in out
    assert 'Источник 2 · строки 1–1' in out
    assert 'Источник 3' not in out
    assert 'A  B' not in out and 'A B' not in out
    assert '&lt;tag&gt;' in out and '&amp;other' in out


class Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []

    def handle_starttag(self, tag, attrs):
        assert tag == 'b' and not attrs
        self.stack.append(tag)

    def handle_endtag(self, tag):
        assert self.stack.pop() == tag


def expanded():
    draft = canonical('missing_results', task_id='\U0001f600' * 40 + "'" * 50)
    agreement = draft.progress[0]
    base = agreement.tasks[0]
    tasks = []
    for index in range(16):
        task_id = f'{index:02}-' + base.task_id
        selected = Span('source-' + "'" * 100, index + 1, index + 1, "'\U0001f600" * 150)
        tasks.append(replace(base, task_id=task_id, spans=(selected,)))
    return replace(draft, progress=(replace(agreement, tasks=tuple(tasks)),))


def test_expanded_html_and_nonbmp_pages_keep_complete_tags_and_subjects():
    draft = expanded()
    pages = render_weekly_draft(draft)
    assert len(pages) > 1
    for page in pages:
        assert len(page.encode('utf-16-le')) // 2 <= renderer.PAGE_BUDGET
        assert 'Черновик для проверки' in page
        parser = Tags()
        parser.feed(page)
        parser.close()
        assert not parser.stack
    assert sum(len(p.encode('utf-16-le')) // 2 for p in pages) <= renderer.MAX_OUTPUT_UNITS
    assert len(pages) <= renderer.MAX_PAGES
    out = '\n'.join(pages)
    assert out.count('Источник 1 · строки') == len(draft.progress[0].tasks)
    for number in range(1, len(draft.progress[0].tasks) + 1):
        # Each source block remains attributed on continuation pages.
        assert f'Задача {number}\nИсточник 1' in out
    assert "source-" not in out and "'" * 20 not in out


def test_only_internal_ordinals_are_visible_and_mapping_is_stable():
    draft = canonical(task_id='opaque-task-cc00', source_id='opaque-source-aa00')
    draft = replace(draft, business='private-business-key', meeting_id='opaque-meeting-bb00')
    out = text(draft)
    assert out == text(draft)
    for identifier in ('opaque-task-cc00', 'opaque-source-aa00', 'private-business-key', 'opaque-meeting-bb00', 'R0'):
        assert identifier not in out
    assert 'Номера действуют только внутри черновика' in out
    assert 'Договорённость 1' in out and 'Задача 1' in out and 'Сообщение о результате 1' in out


def test_excerpt_is_visibly_bounded_without_claiming_result_span_identity():
    draft = canonical('missing_results')
    task = draft.progress[0].tasks[0]
    selected = Span('source', 1, 1, 'длинный фрагмент ' * 100)
    draft = replace(draft, progress=(replace(draft.progress[0], tasks=(replace(task, spans=(selected,)),)),))
    out = text(draft)
    assert '…»' in out and 'Фрагмент по связи задачи/сообщения' in out
    assert selected.quote not in out and 'Подтверждение выполнения:' not in out


@pytest.mark.parametrize('mutation', [
    lambda d: {}, lambda d: replace(d, review_only=False), lambda d: replace(d, review_only=1),
    lambda d: replace(d, result_scope='verified_external_results'),
    lambda d: replace(d, limitation='Всё независимо проверено'),
    lambda d: replace(d, progress=list(d.progress)),
    lambda d: replace(d, progress=({},)),
    lambda d: replace(d, progress=(replace(d.progress[0], tasks=list(d.progress[0].tasks)),)),
    lambda d: replace(d, progress=(replace(d.progress[0], state='unknown'),)),
    lambda d: replace(d, end=DAY), lambda d: replace(d, as_of=True),
    lambda d: replace(d, as_of=float('nan')), lambda d: replace(d, as_of=10**1000),
])
def test_raw_or_changed_frozen_contract_is_rejected(mutation):
    with pytest.raises(ReviewError):
        render_weekly_draft(mutation(canonical()))


def changed_task(draft, **fields):
    agreement = draft.progress[0]
    return replace(draft, progress=(replace(agreement, tasks=(replace(agreement.tasks[0], **fields),)),))


@pytest.mark.parametrize('fields', [
    {'recorded_status': 'externally_verified'}, {'result_state': 'unknown'},
    {'result_state': 'missing'}, {'result_state': 'conflict'}, {'result_ids': ()},
    {'result_ids': ('R0', 'R0')}, {'result_ids': ('R2', 'R1')},
    {'result_ids': ['R0']}, {'spans': []}, {'spans': ()},
    {'spans': ({'source_id': 'invented'},)},
])
def test_malformed_task_result_provenance_is_rejected(fields):
    with pytest.raises(ReviewError):
        render_weekly_draft(changed_task(canonical(), **fields))


def test_aggregate_completion_cannot_hide_missing_child_result():
    draft = canonical('missing_results')
    with pytest.raises(ReviewError):
        render_weekly_draft(replace(draft, progress=(replace(draft.progress[0], state='completed'),)))


def test_duplicate_task_result_or_agreement_identity_is_rejected():
    draft = canonical()
    ag = draft.progress[0]
    with pytest.raises(ReviewError): render_weekly_draft(replace(draft, progress=(ag, ag)))
    with pytest.raises(ReviewError): render_weekly_draft(replace(draft, progress=(replace(ag, tasks=ag.tasks * 2),)))
    second = replace(ag.tasks[0], task_id='T2')
    with pytest.raises(ReviewError): render_weekly_draft(replace(draft, progress=(replace(ag, tasks=ag.tasks + (second,)),)))


@pytest.mark.parametrize('spans', [
    (Span('same', 1, 1, 'one'), Span('same', 1, 1, 'other')),
    (Span('same', 1, 2, 'one\ntwo'), Span('same', 2, 2, 'other')),
    (Span('same', 1, 2, 'only one line'),),
    (Span('same', 1, 1, 'one\ntwo'),),
    (Span('same', 1, 1, 'x' * 2001),),
])
def test_source_spans_must_have_canonical_consistent_line_shapes(spans):
    with pytest.raises(ReviewError):
        render_weekly_draft(changed_task(canonical(), spans=spans))


def test_unrelated_dataclass_and_subclass_cannot_impersonate_checked_draft():
    @dataclass(frozen=True)
    class Fake:
        progress: tuple = ()
        review_only: bool = True

    class Child(WeeklyDraft):
        pass

    with pytest.raises(ReviewError): render_weekly_draft(Fake())
    d = canonical()
    child = Child(d.business, d.meeting_id, d.start, d.end, d.progress, d.as_of)
    with pytest.raises(ReviewError): render_weekly_draft(child)


@pytest.mark.parametrize('budget', ['MAX_PAGES', 'MAX_OUTPUT_UNITS'])
def test_output_budget_fails_without_partial_or_dropped_provenance(monkeypatch, budget):
    monkeypatch.setattr(renderer, budget, 1)
    with pytest.raises(ReviewError, match='weekly_render_output_limit'):
        render_weekly_draft(expanded())


def test_malformed_unicode_fails_before_any_output():
    draft = canonical()
    with pytest.raises(ReviewError, match='invalid_render_unicode'):
        render_weekly_draft(replace(draft, business='bad\ud800'))


def test_no_store_engine_model_network_files_or_wallclock(monkeypatch):
    import builtins

    draft = canonical()

    def forbidden(*args, **kwargs):
        pytest.fail('renderer reached external effect')

    for obj, name in ((builtins, 'open'), (socket, 'socket'), (sqlite3, 'connect'), (time, 'time')):
        monkeypatch.setattr(obj, name, forbidden)
    assert 'зарегистрированы сообщения о выполнении' in text(draft)
