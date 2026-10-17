"""Compare only literal numbered items 1 and 2 of the exact delivered answer.

No entity-memory fallback, research, task authority or new source authority.
"""
from .answer_rewrite import _deny, _handle_readonly_answer
from .ordinal_followup import select_numbered_item

_REQUESTS = {'сравни пункты 1 и 2', 'сравни первый и второй пункты'}
_NOTICE = ('Не могу однозначно выбрать пункты 1 и 2 последнего доставленного ответа. '
           'Пришлите нужный список с явной нумерацией 1–12.')


def is_comparison_request(text):
    if type(text) is not str or not 0 < len(text) <= 128:
        return False
    return ' '.join(text.casefold().split()).rstrip(' .!?') in _REQUESTS


def handle_comparison_followup(engine, event_id, payload):
    if not is_comparison_request(payload.get('text')):
        _deny()
    def prepare(target):
        first = select_numbered_item(target['answer'], 1)
        second = select_numbered_item(target['answer'], 2)
        if first is None or second is None or first.count != second.count:
            return None
        # Keep both complete literal blocks, including their stated conditions.
        # The parser may retain a shared one-line heading in each. Duplicated
        # heading text grants no facts or citations and is charged to the bound.
        projection = first.text + '\n\n' + second.text
        if len(projection) > 3000:
            return None
        return dict(kind='compare_numbered_items', target_answer=projection,
                    selected_numbers=[first.number, second.number],
                    selected_names=[first.name, second.name], numbered_count=first.count,
                    pending_offer=None)
    return _handle_readonly_answer(engine, event_id, payload, prepare, comparison=True,
                                   notice=_NOTICE, prefix='comparison-followup')
