"""Static released preview help, current private admin only; otherwise fall through.

Publication is a host-maintained code list, not runtime Git/deployment/import or
connection discovery. No model, source, warehouse or finance access is needed.
"""
from dataclasses import dataclass

from .promotion_preview_command import _authorized

MAX_DESCRIPTORS=6
MAX_OUTPUT_UNITS=3500
_REQUESTS={'что умеешь','что ты умеешь'}
_SCOPE='supplied_values_only'


class PreviewHelpUnavailable(ValueError):
    def __init__(self):super().__init__('preview_help_unavailable')


@dataclass(frozen=True)
class PreviewDescriptor:
    request:str
    description:str
    data_scope:str=_SCOPE

    def __post_init__(self):
        if (type(self.request) is not str or not 0<len(self.request)<=64
            or type(self.description) is not str or not 0<len(self.description)<=120
            or self.request!=self.request.strip() or self.description!=self.description.strip()
            or any(ord(c)<32 or ord(c)==127 for c in self.request+self.description)
            or self.data_scope!=_SCOPE):
            raise PreviewHelpUnavailable()


# Explicitly published I20/I22/I23/I24/I25/I26; no runtime activation inference.
DESCRIPTORS=(
    PreviewDescriptor('Рассчитай акцию','скидка и указанные расходы'),
    PreviewDescriptor('Рассчитай закупку','стоимость партии по вашим расходам и курсам'),
    PreviewDescriptor('Рассчитай запас товара','месяцы запаса по вашим остаткам и продажам'),
    PreviewDescriptor('Проверь продажи','выручка ниже себестоимости и неизвестная себестоимость в переданных строках'),
    PreviewDescriptor('Проверь карточки товаров','отсутствующие и неизвестные фото/цены в вашей выборке'),
    PreviewDescriptor('Проверь план закупки','условный запас после переданной плановой закупки'),
)


# Separate static descriptions of released I33–I38 workflows. These are not
# calculator descriptors and infer no source/connector availability or rights.
_WORKFLOW_HELP=(
    'Ещё доступны команды:',
    '• «Сравни недели продаж» — сравнение переданных строк двух недель с покрытием себестоимости.',
    '• «Проверь сценарий лимита бартера» — сравнение условных затрат одного участника с введённым лимитом.',
    '• «Рассчитай сценарий окупаемости бартера» — расчёт по условным суммам, возвратам и полноте сведений.',
    '• «Проверь срок отчёта по отгрузке» — условная проверка правила 168 часов по введённым наблюдениям.',
    '• «Проверь измеримость бартера» — условная проверка связи отправки, промокода и отметки результата.',
    '• «Сверь касание по сценарию» — условная сверка плана и отметок квитанций.',
    '• «Проверь сценарий касания» — проверка условного плана контакта; это не разрешение на отправку.',
    '• «Проверь обещание по сценарию» — ручная проверка условного срока и результата; поручения и уведомления не создаются.',
    '• «Проверь сценарий ожидания ответа» — ручная проверка условных событий диалога; переписка не загружается.',
    '• «Подведи итоги акции» — черновик за семь суток по введённым счётчикам; неизвестные значения сохраняются.',
    '• «Проверь перерыв в публикациях» — ручная проверка введённых наблюдений; мониторинг аккаунтов и напоминания не включаются.',
    '• «Подготовь ответ о товаре» — шаблон черновика по введённому сценарию; наличие, цена и доставка не проверяются.',
    '• «Что мешает моим задачам?» — собственные задачи с отметкой препятствия и записанный следующий шаг.',
    '• «Покажи историю регулярной задачи №N» — сохранённые экземпляры и их текущие статусы; это не подтверждение фактического выполнения.',
    '• «Покажи мои справки» — названия и коды доступных личных справок; по восемь на странице.',
    '• «Сохрани справку для меня» — шаблон личной непроверенной справки. Ссылки не открываю и достоверность текста не проверяю.',
    '• После разбора новой планёрки со связанными черновиками вы получите шаблон «Регистрация планёрки MB-КОД». '
    'Заполните именно выданный шаблон: дата, строки решений, номера черновиков, личный доступ и срок. Регистрация связей не согласовывает задачи.',
    '• «Отчёт по планёрке MB-КОД» — шаблон вашего датированного отчёта по одной связанной задаче. '
    'Это ваше утверждение, не независимая проверка; статус задачи не меняется.',
    '• «Сверь планёрку MB-КОД за ГГГГ-ММ-ДД — ГГГГ-ММ-ДД» — разбор за семь дней по зарегистрированным связям и вашим датированным отчётам. '
    'Без отчёта результат остаётся неизвестным; противоречия отмечаются. Только по вашему запросу.',
    'Замените MB-КОД на выданный код регистрации, N — на номер задачи, обе даты — на нужные даты. '
    'Для недельного разбора укажите семь календарных дней, включая первую и последнюю даты.',
)


def is_preview_help_command(text):
    if type(text) is not str or not 0<len(text)<=128:return False
    return ' '.join(text.casefold().split()).strip(' ?!.') in _REQUESTS


def build_preview_help():
    if (type(DESCRIPTORS) is not tuple or not 1<=len(DESCRIPTORS)<=MAX_DESCRIPTORS
        or any(type(item) is not PreviewDescriptor for item in DESCRIPTORS)
        or len({item.request for item in DESCRIPTORS})!=len(DESCRIPTORS)):
        raise PreviewHelpUnavailable()
    lines=['Со мной можно общаться обычным текстом, давать поручения, предлагать идеи и обсуждать результаты.',
        'Для проверок по вашим данным есть шаблоны. Напишите одну из фраз — пришлю шаблон или инструкции для ввода:']
    for item in DESCRIPTORS:
        item.__post_init__()
        lines.append('• «'+item.request+'» — '+item.description+'.')
    lines.extend(['Заполните полученный шаблон и отправьте его сюда.',
        'Эти шаблоны используют только введённые вами значения: ничего не загружают из складских или финансовых систем, '
        'не изменяют учёт и не оформляют закупки. Результаты относятся к переданным данным.'])
    lines.extend(['']+list(_WORKFLOW_HELP))
    output='\n'.join(lines)
    if len(output.encode('utf-16-le'))//2>MAX_OUTPUT_UNITS:raise PreviewHelpUnavailable()
    return output


def handle_preview_help(engine,event_id,payload):
    # False is essential: staff/groups/nonclosed/unavailable gates keep their
    # existing basic/model route and pending event instead of being silenced.
    if (type(payload) is not dict or not is_preview_help_command(payload.get('text'))
        or not engine.s.db.in_transaction):return False
    intelligence=engine.intelligence
    try:scope=_authorized(engine,payload,intelligence)
    except (ValueError,TypeError,KeyError,AttributeError):return False
    text=build_preview_help()
    if _authorized(engine,payload,intelligence)!=scope:raise PreviewHelpUnavailable()
    engine.s.queue(engine.b,payload['sender'],text,'preview-help:'+event_id,engine.now(),None,
                   preferred_transport=payload['channel'])
    if _authorized(engine,payload,intelligence)!=scope:raise PreviewHelpUnavailable()
    # No dialogue state/offer/receipt authority is created. The earlier accepted
    # I14 pending barrier remains, preventing fallback to an older task offer.
    engine.s.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(engine.b,event_id))
    return True
