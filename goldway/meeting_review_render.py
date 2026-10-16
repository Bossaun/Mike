"""Display only caller-reviewed typed meeting facts; no extraction or actions.

Source references and excerpts are untrusted display data. This adapter does not
verify staff identity, grant access, persist tasks, or authorize delivery.
"""
from dataclasses import fields, is_dataclass, replace
from html import escape

from .meeting_review import Meeting, MeetingReview, ReviewError, review_meeting
from .presentation import clean, task_card

PAGE_BUDGET = 3500  # existing Telegram display budget, measured in UTF-16 units
MAX_PAGES = 1024
_HEADER = '<b>📋 GOLDWAY · разбор планёрки</b>\nЧерновик для проверки руководителем. Поручения не созданы.'
_FIELDS = {'owner': 'Ответственный', 'deadline': 'Срок'}


def _units(text):
    try:
        return len(text.encode('utf-16-le')) // 2
    except UnicodeError:
        raise ReviewError('invalid_render_unicode') from None


def _same_typed(value, expected):
    if type(value) is not type(expected):return False
    if type(expected) is tuple:
        return len(value)==len(expected) and all(_same_typed(a,b) for a,b in zip(value,expected))
    if is_dataclass(expected):
        return all(_same_typed(getattr(value,f.name),getattr(expected,f.name)) for f in fields(expected))
    return value==expected


def _checked(review):
    # MeetingReview is a public dataclass without its own validation. Require a
    # canonical projection, including unresolved questions and every conflict.
    if type(review) is not MeetingReview or type(review.meeting) is not Meeting:
        raise ReviewError('typed_meeting_review_required')
    meeting = replace(review.meeting)  # recheck business/provenance/cardinality
    expected = review_meeting(meeting)
    if not _same_typed(review, expected):
        raise ReviewError('noncanonical_meeting_review')
    return expected


def _references(spans, subject):
    for span in spans:
        yield (f'{subject}\nИсточник: {escape(span.source_id)} · строки {span.first}–{span.last}\n'
               f'Цитата (фрагмент): «{clean(span.quote,160)}»')


def _pages(blocks):
    pages = []
    page = _HEADER
    for block in blocks:
        # Each block contains complete HTML tags; never split escaped entities
        # or UTF-16 surrogate pairs. Refuse an oversized block, never drop it.
        if _units(_HEADER+'\n\n'+block) > PAGE_BUDGET:
            raise ReviewError('render_block_too_large')
        if _units(page+'\n\n'+block) > PAGE_BUDGET:
            pages.append(page)
            page = _HEADER
            if len(pages) >= MAX_PAGES:
                raise ReviewError('render_page_limit')
        page += '\n\n'+block
    pages.append(page)
    return pages


def render_meeting_review(review, config):
    """Return bounded HTML previews; caller owns authorization and human review.

    ``config`` matches the presentation bridge signature, but its employee names
    are deliberately not used to reinterpret source-bound owner display text.
    No existing task numbers or assignments are manufactured.
    """
    review = _checked(review)
    blocks = [f'Дата источников: {review.meeting.on.isoformat()}\n'
              f'Предложенных задач: {len(review.proposed_tasks)} · '
              f'Открытых вопросов: {len(review.unresolved_questions)} · '
              f'Противоречий: {len(review.contradictions)}']
    for number, task in enumerate(review.proposed_tasks, 1):
        # Source owner is display text, not an employee ID or authorization.
        card = {'id': f'черновик {number}', 'status': 'proposed',
                'title': task.title, 'owner': 'source_owner' if task.owner else None,
                'deadline': task.deadline.isoformat() if task.deadline else None}
        local_config = {'employees': {'source_owner': {'name': task.owner}}}
        blocks.append(task_card(card, local_config))
        if task.owner:
            blocks.append(f'Черновик {number} · Ответственный по источнику (не назначение): '+clean(task.owner,300))
        for field in ('owner','deadline'):
            if field in task.conflicted_fields:
                blocks.append(f'Черновик {number} · '+_FIELDS[field]+': неизвестен — противоречие в источниках.')
            elif field in task.missing_fields:
                blocks.append(f'Черновик {number} · '+_FIELDS[field]+': неизвестен — нужно уточнить.')
        blocks.extend(_references(task.spans,f'Черновик {number}'))
    for question in review.unresolved_questions:
        subject='Вопрос '+clean(question.id,128)
        blocks.append('<b>Открытый вопрос</b> · '+subject+'\n'+clean(question.text,300))
        blocks.extend(_references(question.spans,subject))
        if question.resolution:
            # Never present an unconfirmed or contradicted resolution as settled.
            blocks.append(subject+' · Вариант ответа требует уточнения: '+clean(question.resolution.value,300))
            blocks.extend(_references((question.resolution.span,),subject))
    for conflict in review.contradictions:
        blocks.append('<b>Противоречие — решение неизвестно</b>\nПоле: '+clean(conflict.fact_key,128))
        for value in conflict.values:
            blocks.append('Поле: '+clean(conflict.fact_key,128)+'\nПротиворечащий вариант: '+clean(value,300))
        blocks.extend(_references(conflict.spans,'Противоречие · поле: '+clean(conflict.fact_key,128)))
    tentative = {a.id:a for a in review.meeting.agreements}
    for identifier in review.tentative_agreement_ids:
        agreement = tentative[identifier]
        blocks.append('<b>Обсуждалось — решение не подтверждено</b>\n'+clean(agreement.value,300))
        blocks.extend(_references(agreement.spans,'Неподтверждённое решение '+clean(identifier,128)))
    return _pages(blocks)
