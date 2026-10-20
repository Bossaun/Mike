"""Literal catalog details for one already validated accepted explanation.

The caller must first validate the model result and current evidence permissions.
This pure projection grants no access, retrieves nothing, and infers no brand facts.
Incomplete chunks, ambiguous lists and other conversation routes return None.
"""
import re
from .ordinal_followup import select_numbered_item

_URL = 'https://gw-api.abdulwaheedaziz8.workers.dev?api=catalog'
_SUFFIX = 'Это публичные названия товаров, не данные об остатках, оплатах или ответственном.'
_SCOPE = ('Это названия из публичного каталога; происхождение брендов, свойства товаров, '
          'остатки и ответственные здесь не подтверждены.')
_LIST_START = re.compile(r'^\s*(?:[0-9]+\s*[.)\-:]|[-*•])')


def _text(value, limit):
    return (type(value) is str and 0 < len(value) <= limit
            and not any(ord(c) < 32 and c != '\n' for c in value)
            and not any(0xD800 <= ord(c) <= 0xDFFF for c in value))


def _empty(value):
    return type(value) is list and not value


def _names(answer):
    if not _text(answer, 2000):
        return None
    lines = answer.splitlines()
    positions = [i for i, line in enumerate(lines) if _LIST_START.match(line)]
    if len(positions) != 3 or positions != list(range(positions[0], positions[0] + 3)):
        return None
    # Surrounding prose is not authority. The existing parser alone determines
    # the complete flat numbering and literal names of this one explicit block.
    block = '\n'.join(lines[i] for i in positions)
    selected = [select_numbered_item(block, i) for i in (1, 2, 3)]
    if any(item is None or item.count != 3 for item in selected):
        return None
    return tuple(item.name for item in selected)


def _products(item, name):
    prefix = 'Публичный каталог GOLDWAY. В поле brand указано: ' + name + '.\nПозиции каталога:\n'
    body = item.get('text')
    if (item.get('confirmed') is not True or item.get('truncated', False) is not False
            or item.get('url') != _URL or not _text(body, 8000)
            or not body.startswith(prefix) or not body.endswith('\n' + _SUFFIX)):
        return None
    lines = body[len(prefix):-len('\n' + _SUFFIX)].split('\n')
    if (not 1 <= len(lines) <= 64 or len(set(lines)) != len(lines)
            or any(not _text(line, 300) or line != line.strip() for line in lines)):
        return None
    # The reader clips original model names at 300 chars. Such a boundary cannot
    # prove a complete name, so it is never quoted; no consumer-side truncation.
    complete = tuple(line for line in lines if len(line) < 300)
    return complete[:2] or None


def build_catalog_explanation(context, result):
    """Return (plain text, immutable current refs, ordered names), or None.

    Only three exact current confirmed catalog projections can support this
    response. Entities, facts, model prose and surrounding list prose are unused.
    The body reserves at least 1600 characters for the caller's citation footer.
    """
    if type(context) is not dict or type(result) is not dict:
        return None
    reply = result.get('reply_text')
    if type(reply) is not str or not reply.strip() or len(reply) > 3000:
        return None
    if (result.get('intent') != 'conversation' or result.get('knowledge_mode') != 'grounded'
            or not _empty(result.get('actions')) or not _empty(context.get('tasks', []))
            or not _empty(context.get('task_evidence_ids', []))
            or context.get('work_context') is not None
            or any(key in context for key in ('rewrite_only', 'ordinal_only', 'comparison_only'))):
        return None
    dialogue, state, followup = (context.get('dialogue'), result.get('dialogue_state'),
                                context.get('followup'))
    if (type(dialogue) is not dict or type(state) is not dict
            or dialogue.get('task_id') is not None or state.get('task_id') is not None
            or type(followup) is not dict or followup.get('kind') != 'continue_last_turn'
            or followup.get('selected_entity') is not None):
        return None
    for offer in (followup.get('pending_offer'), dialogue.get('pending_offer')):
        if type(offer) is not dict or offer.get('kind') != 'explain':
            return None
    names = _names(followup.get('target_answer'))
    knowledge, refs = context.get('knowledge'), result.get('evidence_ids')
    if (names is None or type(knowledge) is not dict
            or not _empty(knowledge.get('conflicts', []))
            or type(refs) is not list or not 1 <= len(refs) <= 12
            or any(not _text(ref, 256) for ref in refs)):
        return None
    evidence = knowledge.get('evidence')
    if type(evidence) is not list or not 1 <= len(evidence) <= 16:
        return None
    ids = []
    for item in evidence:
        if (type(item) is not dict or not _text(item.get('id'), 256)
                or not _text(item.get('title'), 512)):
            return None
        ids.append(item['id'])
    if len(set(ids)) != len(ids) or not set(refs) <= set(ids):
        return None
    chosen, products = [], []
    for name in names:
        matches = [item for item in evidence
                   if item['title'] == name + ': публичные позиции каталога']
        if len(matches) != 1:
            return None
        values = _products(matches[0], name)
        if values is None:
            return None
        chosen.append(matches[0]['id'])
        products.append(values)
    for count in (2, 1):
        lines = [str(i) + '. ' + name + ' — в публичном каталоге указаны: '
                 + '; '.join('«' + value + '»' for value in values[:count]) + '.'
                 for i, (name, values) in enumerate(zip(names, products), 1)]
        text = '\n'.join([*lines, '  ' + _SCOPE])
        if len(text) <= 1400 and len(text.encode('utf-16-le')) // 2 <= 1400:
            return text, tuple(chosen), names
    return None
