"""Local meeting-summary preview. No transport, recipient IDs or send authorization.

The caller must supply human-reviewed field classifications. This projection is
not a classifier: even routine-labelled text still requires final human review.
"""
from dataclasses import dataclass
from enum import Enum


class Visibility(Enum):
    ROUTINE = 'routine'
    FINANCIAL = 'financial'
    PRIVATE = 'private'
    UNKNOWN = 'unknown'


def _text(value: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError('invalid_text')
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError('control_character')
    return value


@dataclass(frozen=True)
class ReviewedText:
    value: str
    visibility: Visibility

    def __post_init__(self):
        _text(self.value, 300)
        if not isinstance(self.visibility, Visibility):
            raise ValueError('invalid_visibility')


@dataclass(frozen=True)
class SummaryItem:
    title: ReviewedText
    status: str
    owner: ReviewedText | None = None
    deadline: ReviewedText | None = None
    next_step: ReviewedText | None = None

    def __post_init__(self):
        if not isinstance(self.title, ReviewedText):
            raise ValueError('invalid_title')
        if self.status not in {'proposed', 'confirmed', 'in_progress', 'blocked', 'completed'}:
            raise ValueError('invalid_status')
        for value in (self.owner, self.deadline, self.next_step):
            if value is not None and not isinstance(value, ReviewedText):
                raise ValueError('invalid_field')


@dataclass(frozen=True)
class GroupPreview:
    text: str
    included_items: int
    omitted_items: int
    omitted_fields: int
    requires_review: bool = True
    live_send_authorized: bool = False


_STATUS = {'proposed': 'Черновик', 'confirmed': 'Подтверждено',
           'in_progress': 'В работе', 'blocked': 'Есть препятствие',
           'completed': 'Выполнено'}


def preview(items, *, max_items: int = 20) -> GroupPreview:
    """Project allowlisted fields only; discard non-routine titles entirely.

    No omitted value, raw source link, task ID, comment or financial column is
    copied into the output. Counts describe omissions without exposing content.
    """
    if type(max_items) is not int or not 1 <= max_items <= 20:
        raise ValueError('invalid_limit')
    selected = []
    # Bound consumption before materializing an arbitrary iterable.
    for item in items:
        if len(selected) >= 100:
            raise ValueError('too_many_items')
        if not isinstance(item, SummaryItem):
            raise ValueError('invalid_item')
        selected.append(item)
    lines = ['Общие итоги планёрки · черновик для проверки']
    included = omitted = hidden_fields = 0
    for item in selected:
        if item.title.visibility is not Visibility.ROUTINE or included >= max_items:
            omitted += 1
            continue
        included += 1
        lines.append(f'{included}. {item.title.value} · {_STATUS[item.status]}')
        for label, field in [('Ответственный', item.owner), ('Срок', item.deadline),
                             ('Следующий шаг', item.next_step)]:
            if field is None:
                continue
            if field.visibility is Visibility.ROUTINE:
                lines.append(f'   {label}: {field.value}')
            else:
                hidden_fields += 1
    if included == 0:
        lines.append('Нет пунктов для общего предварительного итога.')
    lines.append('Требуется проверка содержания и отдельное разрешение отправки.')
    return GroupPreview('\n'.join(lines), included, omitted, hidden_fields)
