"""Actual marketing report core over one hypothetical supplied seven-day scenario.

Three nonnegative reported counters; no arithmetic, ROI, accounts or verification.
Exactly11 input lines, <=4096bytes/2048UTF16; one3500UTF16 own editable output.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from html import escape
import hashlib
import re

from . import marketing_drafts as core
from .customer_product_draft_command import _gate
from .weekly_review_command import _host, _outbox
from .monthly_task_history import _Reader
from .answer_citations import _hash

HEADER='Подведи итоги акции'
PREFIX='campaign-report-preview:'
BASIS='только счетчики из этого сообщения для указанной акции и периода; независимо не проверены'
SOURCE='Значения из этого сообщения — непроверенный сценарий'
_LABELS=('Название сценария','Начало периода','Конец периода','Срез сценария','Наблюдалось',
         'Покрытие переданных значений','Показы','Обращения','Заказы','Основание')
_COUNTERS=(('impressions','Показы','Число показов, указанное пользователем для этой акции и периода; уникальность и причинность не проверены.'),
           ('enquiries','Обращения','Число обращений, указанное пользователем для этой акции и периода; уникальность и причинность не проверены.'),
           ('orders','Заказы','Число заказов, указанное пользователем для этой акции и периода; это не подтверждение оплаты или выполнения.'))
_COVERAGES={'полное':'complete','частичное':'partial','неизвестно':'unknown'}
_DATE=re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])')
TEMPLATE=(HEADER+'\nНазвание сценария: [название]\nНачало периода: [YYYY-MM-DDTHH:MM:SS+HH:MM]\n'
 'Конец периода: [YYYY-MM-DDTHH:MM:SS+HH:MM]\nСрез сценария: [YYYY-MM-DDTHH:MM:SS+HH:MM]\n'
 'Наблюдалось: [YYYY-MM-DDTHH:MM:SS+HH:MM / неизвестно]\nПокрытие переданных значений: [полное / частичное / неизвестно]\n'
 'Показы: [целое число / неизвестно]\nОбращения: [целое число / неизвестно]\nЗаказы: [целое число / неизвестно]\nОснование: '+BASIS)
NOTICE='Итоги не подготовлены. Нужны все десять полей без повторов, явные счетчики или «неизвестно», даты с часовым смещением и период ровно семь суток.\n\n'+TEMPLATE


class CampaignUnavailable(ValueError):
    def __init__(self):super().__init__('supplied_campaign_report_unavailable')


def _deny():raise CampaignUnavailable()


def _units(value):
    try:return len(value.encode('utf-16-le'))//2
    except (AttributeError,UnicodeError):_deny()


def is_command(text):return type(text) is str and text.startswith(HEADER)


def _fields(text):
    if type(text) is not str or len(text)>4096 or _units(text)>2048 or len(text.encode())>4096:_deny()
    if any(ord(c)<32 and c!='\n' or ord(c)==127 for c in text):_deny()
    lines=text.split('\n')
    if len(lines)!=11 or lines[0]!=HEADER:_deny()
    fields={}
    for line in lines[1:]:
        if ': ' not in line:_deny()
        key,value=line.split(': ',1)
        if key not in _LABELS or key in fields or not value or value!=value.strip():_deny()
        fields[key]=value
    if set(fields)!=set(_LABELS) or fields['Основание']!=BASIS:_deny()
    return fields


def _date(value):
    if type(value) is not str or len(value)>35 or not _DATE.fullmatch(value):_deny()
    result=datetime.fromisoformat(value.replace('Z','+00:00'))
    if result.utcoffset() is None:_deny()
    return result


def _localized(text):
    # Presentation only: actual core renderer text/numbers remain authoritative.
    # Replace ONLY exact structural state/coverage tokens, not arbitrary data.
    periods={'before_start':'ещё не начался','in_progress':'ещё идёт',
             'closed':'закончился по указанному срезу'}
    coverage={'complete':'полное по вашему сообщению','partial':'частичное',
              'unknown':'неизвестно','missing':'наблюдение не передано'}
    result=[]
    for line in text.split('\n'):
        if line.startswith('Состояние периода: ') and line[len('Состояние периода: '):] in periods:
            line='Состояние периода: '+periods[line[len('Состояние периода: '):]]
        else:
            match=re.fullmatch(r'(Показы|Обращения|Заказы): (.+) шт\.; покрытие: (complete|partial|unknown|missing)\.',line)
            if match:
                line=match[1]+': '+match[2]+' шт.; покрытие: '+coverage[match[3]]+'.'
        result.append(line)
    return '\n'.join(result)


@dataclass(frozen=True)
class CampaignPreview:
    text:str
    report:core.CampaignReport
    draft:core.EditableDraft


def build_preview(text):
    try:
        fields=_fields(text);title=fields['Название сценария']
        if _units(title)>100 or title.startswith('['):_deny()
        coverage=_COVERAGES.get(fields['Покрытие переданных значений'])
        if coverage is None:_deny()
        at=_date(fields['Срез сценария'])
        token=hashlib.sha256(text.encode()).hexdigest()[:32]
        definitions=tuple(core.MetricDefinition(key,label,definition,'шт.') for key,label,definition in _COUNTERS)
        campaign=core.Campaign('scenario:'+token,_date(fields['Начало периода']),_date(fields['Конец периода']),definitions)
        if campaign.end-campaign.start!=timedelta(days=7):_deny()
        observed=None if fields['Наблюдалось']=='неизвестно' else _date(fields['Наблюдалось'])
        if observed is not None and not campaign.start<=observed<=at:_deny()
        observations=[]
        for definition in definitions:
            value=fields[definition.label]
            if value=='неизвестно':continue
            if not re.fullmatch(r'0|[1-9][0-9]{0,8}',value) or observed is None:_deny()
            observations.append(core.MetricObservation('metric:'+token+':'+definition.key,campaign.id,definition.key,
                definition.unit,value,campaign.start,campaign.end,observed,coverage,SOURCE))
        report=core.campaign_report(campaign,tuple(observations),now=at)
        if (type(report) is not core.CampaignReport or report.campaign!=campaign or len(report.rows)!=3 or
            report.review_required is not True or report.live_authorized is not False or
            report.period_state not in ('before_start','in_progress','closed')):_deny()
        draft=core.render_campaign_report(report)
        if (type(draft) is not core.EditableDraft or type(draft.text) is not str or not draft.text or
            draft.review_required is not True or draft.live_authorized is not False or
            draft.source_ids!=((SOURCE,) if observations else ())):_deny()
        lines=['<b>Итоги по вашему сценарию · редактируемый черновик</b>',
            'Все счетчики, их период и полнота указаны вами, независимо не проверены. Это не проверка реальной акции или аккаунта.',
            'Название сценария: '+escape(title),'Срез сценария: '+escape(fields['Срез сценария']),
            'Период — семь суток: начало включено, конец исключён. Покрытие и время наблюдения относятся только к переданным известным счетчикам.',
            escape(_localized(draft.text)),
            'Неполное или неизвестное покрытие не доказывает полный итог. Неизвестные счетчики не заменены нулём.',
            'Черновик для вашей ручной проверки. Итоги не опубликованы; автоматическое наблюдение не включено.']
        body='\n\n'.join(lines)
        if _units(body)>3500:_deny()
        return CampaignPreview(body,report,draft)
    except CampaignUnavailable:raise
    except (ValueError,TypeError,OverflowError,UnicodeError,AttributeError,KeyError):raise CampaignUnavailable() from None


def handle(engine,event,payload):
    host=_host(engine);store=host[0]
    if not store.db.in_transaction:_deny()
    try:initial=_gate(engine,payload,host)
    except (ValueError,TypeError,KeyError,AttributeError):
        store.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(host[2],event));return
    if payload['text']==HEADER:body=TEMPLATE
    else:
        try:body=build_preview(payload['text']).text
        except CampaignUnavailable:body=NOTICE
    if _gate(engine,payload,host)!=initial:_deny()
    dedupe=PREFIX+_hash(event)
    if not store.queue(host[2],payload['sender'],body,dedupe,engine.now(),parse_mode='HTML',preferred_transport=payload['channel']):_deny()
    if _gate(engine,payload,host)!=initial:_deny()
    identifier=store.execute('SELECT id FROM outbox WHERE business=? AND dedupe=?',(host[2],dedupe)).fetchone()[0]
    row=_outbox(_Reader(store),host[2],identifier)
    if row['text']!=body or row['recipient']!=payload['sender'] or row['task_id'] is not None or row['state']!='pending' or row['parse_mode']!='HTML' or row['preferred_transport']!=payload['channel']:_deny()
    if _gate(engine,payload,host)!=initial:_deny()
    store.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(host[2],event))
