"""Display an already permission-checked WeeklyDraft; no authority or new facts.

This validates the available typed structure, not its authenticity or current
Store grants. The host must regenerate/revalidate the adapter draft before use.
Merged task/result spans do not identify which quote proves a result.
"""

import json
from dataclasses import asdict, replace
from datetime import date, datetime, timedelta, timezone

from .meeting_review import AgreementProgress, ReviewError, Span, TaskProgress
from .meeting_week_adapter import (
    LIMITATION, MAX_BINDINGS, MAX_DRAFT_CHARS, MAX_SOURCES, WeeklyDraft, _id,
)
from .presentation import clean


PAGE_BUDGET = 3500
MAX_PAGES = 32
MAX_OUTPUT_UNITS = 48000
_RECORD = {'proposed': 'черновик', 'confirmed': 'согласовано',
           'in_progress': 'в работе', 'blocked': 'есть препятствие', 'completed': 'выполнено'}
_RESULT = {'missing': 'датированный результат за период не передан',
           'completed': 'зарегистрировано сообщение о выполнении',
           'blocked': 'зарегистрировано сообщение о препятствии',
           'in_progress': 'зарегистрировано сообщение о ходе работы',
           'conflict': 'зарегистрированные результаты противоречат друг другу'}
_AGREEMENT = {
    'unlinked': 'связь с задачами не передана',
    'missing_results': 'не по всем связанным задачам переданы датированные результаты',
    'completed': 'по всем связанным задачам зарегистрированы сообщения о выполнении',
    'blocked': 'в зарегистрированных сообщениях есть препятствие',
    'in_progress': 'зарегистрировано продолжение работы',
    'conflict': 'зарегистрированные результаты противоречат друг другу',
    'contradiction': 'в исходной договорённости есть противоречие; итог не выбран',
}


def _fail():
    raise ReviewError('invalid_weekly_render_draft')


def _units(text):
    try:
        return len(text.encode('utf-16-le')) // 2
    except UnicodeError:
        raise ReviewError('invalid_render_unicode') from None


def _checked(draft):
    if (type(draft) is not WeeklyDraft or draft.review_only is not True
            or type(draft.result_scope) is not str or draft.result_scope != 'caller_bound_registered_reports'
            or type(draft.limitation) is not str or draft.limitation != LIMITATION):
        _fail()
    _id(draft.business)
    _id(draft.meeting_id)
    if (type(draft.start) is not date or type(draft.end) is not date
            or draft.end - draft.start != timedelta(days=7)
            or type(draft.as_of) not in (int, float) or not -1e15 <= draft.as_of <= 1e15
            or type(draft.progress) is not tuple or len(draft.progress) > MAX_BINDINGS):
        _fail()
    try:
        observed = datetime.fromtimestamp(draft.as_of, timezone.utc).strftime('%d.%m.%Y %H:%M UTC')
    except (ValueError, OverflowError, OSError):
        _fail()
    agreements, tasks, results, source_ids, lines = set(), set(), set(), set(), {}
    spans_count = quote_chars = 0
    for agreement in draft.progress:
        if (type(agreement) is not AgreementProgress or type(agreement.tasks) is not tuple
                or len(agreement.tasks) > MAX_BINDINGS or type(agreement.state) is not str
                or agreement.state not in _AGREEMENT):
            _fail()
        _id(agreement.agreement_id)
        if agreement.agreement_id in agreements:
            _fail()
        agreements.add(agreement.agreement_id)
        ordered = []
        for task in agreement.tasks:
            if (type(task) is not TaskProgress or type(task.recorded_status) is not str
                    or task.recorded_status not in _RECORD or type(task.result_state) is not str
                    or task.result_state not in _RESULT or type(task.result_ids) is not tuple
                    or len(task.result_ids) > MAX_BINDINGS or type(task.spans) is not tuple
                    or not 1 <= len(task.spans) <= 3 * (MAX_BINDINGS + 1)):
                _fail()
            _id(task.task_id)
            if task.task_id in tasks:
                _fail()
            tasks.add(task.task_id)
            ordered.append(task.task_id)
            if len(tasks) > MAX_BINDINGS:
                _fail()
            for result_id in task.result_ids:
                _id(result_id)
                if result_id in results:
                    _fail()
                results.add(result_id)
            if (len(results) > MAX_BINDINGS or task.result_ids != tuple(sorted(task.result_ids))
                    or (task.result_state == 'missing') != (not task.result_ids)
                    or (task.result_state == 'conflict' and len(task.result_ids) < 2)):
                _fail()
            seen = set()
            for span in task.spans:
                if type(span) is not Span:
                    _fail()
                replace(span)  # existing canonical identifier/quote/range validation
                spans_count += 1
                quote_chars += len(span.quote)
                if spans_count > 6 * MAX_BINDINGS or quote_chars > MAX_DRAFT_CHARS:
                    _fail()
                if span in seen:
                    _fail()
                seen.add(span)
                source_ids.add(span.source_id)
                if len(source_ids) > MAX_SOURCES:
                    _fail()
                quoted_lines = span.quote.split('\n')
                if (len(quoted_lines) != span.last - span.first + 1
                        or any(len(line) > 2000 or '\r' in line for line in quoted_lines)):
                    _fail()
                for number, line in enumerate(quoted_lines, span.first):
                    key = (span.source_id, number)
                    if key in lines and lines[key] != line:
                        _fail()
                    lines[key] = line
        if ordered != sorted(ordered):
            _fail()
        # Validate supplied aggregate invariants; do not select dates, outcomes
        # or a resolution. A contradiction needs the upstream Meeting context.
        states = {task.result_state for task in agreement.tasks}
        coherent = {
            'unlinked': not states,
            'completed': states == {'completed'},
            'missing_results': 'missing' in states and 'conflict' not in states,
            'blocked': 'blocked' in states and not states & {'missing', 'conflict'},
            'in_progress': 'in_progress' in states and states <= {'completed', 'in_progress'},
            'conflict': 'conflict' in states,
            'contradiction': True,
        }
        if not coherent[agreement.state]:
            _fail()
    serialized = json.dumps(asdict(draft), ensure_ascii=False, default=str)
    if len(serialized) > MAX_DRAFT_CHARS:
        _fail()
    _units(serialized)
    return observed


def _pages(header, blocks):
    pages, page, total = [], header, 0
    for block in blocks:
        if _units(header + '\n\n' + block) > PAGE_BUDGET:
            raise ReviewError('weekly_render_block_too_large')
        if _units(page + '\n\n' + block) > PAGE_BUDGET:
            pages.append(page)
            total += _units(page)
            if len(pages) >= MAX_PAGES or total > MAX_OUTPUT_UNITS:
                raise ReviewError('weekly_render_output_limit')
            page = header
        page += '\n\n' + block
    pages.append(page)
    if len(pages) > MAX_PAGES or total + _units(page) > MAX_OUTPUT_UNITS:
        raise ReviewError('weekly_render_output_limit')
    return pages


def render_weekly_draft(draft):
    """Return HTML preview pages only; host owns freshness, access and review."""
    observed = _checked(draft)
    period = draft.start.strftime('%d.%m.%Y') + '–' + (draft.end - timedelta(days=1)).strftime('%d.%m.%Y')
    header = ('<b>📋 GOLDWAY · недельный разбор</b>\nПериод: ' + period + '\n'
              'Черновик для проверки по переданному срезу. '
              'Выполнение независимо не проверено. Назначений и изменений нет.\n'
              'Номера действуют только внутри черновика.')
    blocks = ['Срез: ' + observed]
    task_numbers, result_numbers, source_numbers = {}, {}, {}
    if not draft.progress:
        blocks.append('Договорённости для сравнения не переданы. Это не подтверждение отсутствия работы.')
    for number, agreement in enumerate(draft.progress, 1):
        label = f'Договорённость {number}'
        blocks.append('<b>' + label + '</b>\nЗа период: ' + _AGREEMENT[agreement.state] + '.')
        for task in agreement.tasks:
            task_numbers.setdefault(task.task_id, len(task_numbers) + 1)
            subject = f'Задача {task_numbers[task.task_id]}'
            blocks.append(label + '\n<b>' + subject + '</b>\nСтатус переданной записи: '
                          + _RECORD[task.recorded_status] + '.\nЗа период: ' + _RESULT[task.result_state] + '.')
            for result_id in task.result_ids:
                result_numbers.setdefault(result_id, len(result_numbers) + 1)
                blocks.append(subject + f'\nСообщение о результате {result_numbers[result_id]} (по переданной связи).')
            for span in task.spans:
                source_numbers.setdefault(span.source_id, len(source_numbers) + 1)
                blocks.append(subject + f'\nИсточник {source_numbers[span.source_id]} · строки {span.first}–{span.last}\n'
                              + 'Фрагмент по связи задачи/сообщения: «' + clean(span.quote, 160) + '»')
    return _pages(header, blocks)
