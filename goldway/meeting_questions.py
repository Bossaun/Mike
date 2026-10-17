"""Current-input question candidates; no semantic confirmation or action authority.

One existing model response selects literal source lines. Host derives all text;
this is not the caller-reviewed MeetingReview contract or a knowledge import.
"""
from dataclasses import dataclass
from html import escape
from .answer_citations import _hash, _permissions
from .task_card_reference import _gate, _barrier
from .mutation_target import _policy
from .monthly_task_history import _Reader
from .intelligence import Intelligence

MAX_QUESTIONS=8
MAX_ITEM_UNITS=800
MAX_QUOTED_UNITS=2400
MAX_PAGE_UNITS=3500
MAX_PAGES=12
MAX_TOTAL_UNITS=28000
_HEADER='<b>Вопросы из обсуждения — черновик для проверки</b>\nСтатус решения не подтверждён. Это выборка кандидатов, а не полный список вопросов.'
NOTICE='Не удалось проверить вопросы из обсуждения. Разбор не применён. Пришлите более короткий фрагмент; вопросы можно отметить отдельными строками.'


class MeetingQuestionsInvalid(ValueError):
    def __init__(self):super().__init__('meeting_question_draft_invalid')


def _fail():raise MeetingQuestionsInvalid()


def _units(value):
    try:return len(value.encode('utf-16-le'))//2
    except (UnicodeError,AttributeError):_fail()


def _numbered(text):return '\n'.join(f'{i} | {line}' for i,line in enumerate(text.splitlines(),1))


def _refs(context):
    values=set(Intelligence.evidence(context.get('knowledge',{}),context.get('work_context')))
    for key in ('dialogue_evidence_ids','task_evidence_ids'):
        value=context.get(key,[])
        if type(value) is not list or len(value)>16:_fail()
        values.update(value)
    if len(values)>16 or any(type(v) is not str or not 0<len(v)<=160 for v in values):_fail()
    return tuple(sorted(values))


def _authorized(engine,payload,intelligence):
    scope,_=_gate(engine,payload,intelligence)
    if (engine.c.get('admins')!=[payload['sender']] or not engine._operator_turn(payload,'reply')
        or engine.c.get('review_mode') is not True):_fail()
    return scope,_hash([_policy(engine),engine.c.get('review_mode'),engine.c.get('meeting_recipient')])


@dataclass(frozen=True)
class QuestionHost:
    intelligence:object
    store:object
    config:object
    scope:str
    policy:str
    text:str
    event_id:str
    refs:tuple
    reader:object


@dataclass(frozen=True)
class QuestionDraft:
    host:QuestionHost
    pages:tuple[str,...]


def capture(engine,event_id,payload,context):
    """Advertise only this captured private host capability, before model call."""
    context.setdefault('capabilities',{})['meeting_question_draft']=False
    if (context.get('kind')!='operator_chat' or context.get('sender_role')!='admin'
        or context.get('can_create_tasks') is not True or context.get('work_context')
        or any(context.get(k) for k in ('rewrite_only','ordinal_only','comparison_only'))):return None
    try:
        text=payload['text'];lines=text.splitlines()
        if type(text) is not str or not 0<len(text)<=32000 or not 1<=len(lines)<=1000:_fail()
        if context.get('text')!=_numbered(text):_fail()
        intelligence=engine.intelligence;scope,policy=_authorized(engine,payload,intelligence)
        reader=_Reader(engine.s);_barrier(reader,engine.b,scope,event_id)
        refs=_refs(context)
        _permissions(reader,engine.s,engine.c,payload['sender'],list(refs),engine.now())
        token=QuestionHost(intelligence,engine.s,engine.c,scope,policy,text,event_id,refs,reader)
        context['capabilities']['meeting_question_draft']=True
        context['meeting_question_line_count']=len(lines)
        return token
    except Exception:return None


def recheck(engine,payload,context,host):
    try:
        if type(host) is not QuestionHost or host.store is not engine.s or host.config is not engine.c:_fail()
        if _authorized(engine,payload,host.intelligence)!=(host.scope,host.policy):_fail()
        if (payload.get('text')!=host.text or context.get('text')!=_numbered(host.text)
            or context.get('kind')!='operator_chat' or context.get('sender_role')!='admin'
            or context.get('can_create_tasks') is not True or _refs(context)!=host.refs):_fail()
        _barrier(host.reader,engine.b,host.scope,host.event_id)
        _permissions(host.reader,engine.s,engine.c,payload['sender'],list(host.refs),engine.now())
    except MeetingQuestionsInvalid:raise
    except Exception:raise MeetingQuestionsInvalid() from None


def validate(engine,payload,context,result,host):
    """Missing/empty field is legacy-compatible; no projection or new authority."""
    if 'meeting_question_lines' not in result:return None
    spans=result['meeting_question_lines']
    if type(spans) is not list or len(spans)>MAX_QUESTIONS:_fail()
    if not spans:return None
    if result.get('intent')!='meeting':_fail()
    recheck(engine,payload,context,host)
    lines=host.text.splitlines();used=set();quoted=0;blocks=[]
    for ordinal,span in enumerate(spans,1):
        if (type(span) is not list or not 1<=len(span)<=3
            or any(type(i) is not int or not 1<=i<=len(lines) for i in span)
            or span!=list(range(span[0],span[-1]+1)) or used.intersection(span)):_fail()
        used.update(span);quote='\n'.join(lines[i-1] for i in span)
        units=_units(quote);quoted+=units
        if not quote.strip() or units>MAX_ITEM_UNITS or quoted>MAX_QUOTED_UNITS:_fail()
        # Exact source text is escaped, never paraphrased, normalized or truncated.
        label=str(span[0]) if len(span)==1 else f'{span[0]}–{span[-1]}'
        blocks.append(f'<b>Кандидат {ordinal}</b> · строки {label} текущего сообщения\n'+escape(quote))
    pages=[];page=_HEADER
    for block in blocks:
        if _units(_HEADER+'\n\n'+block)>MAX_PAGE_UNITS:_fail()
        if _units(page+'\n\n'+block)>MAX_PAGE_UNITS:pages.append(page);page=_HEADER
        page+='\n\n'+block
    pages.append(page)
    draft=QuestionDraft(host,tuple(pages));check_bundle((),draft)
    return draft


def check_bundle(card_pages,draft):
    if type(draft) is not QuestionDraft:_fail()
    pages=tuple(card_pages)+draft.pages
    if (len(pages)>MAX_PAGES or any(type(p) is not str or _units(p)>MAX_PAGE_UNITS for p in pages)
        or sum(_units(p) for p in pages)>MAX_TOTAL_UNITS):_fail()


def queue(engine,payload,context,draft):
    if not engine.s.db.in_transaction:_fail()
    recheck(engine,payload,context,draft.host)
    for ordinal,page in enumerate(draft.pages):
        engine.s.queue(engine.b,payload['sender'],page,f'meeting-questions:{draft.host.event_id}:page:{ordinal}',
            engine.now(),parse_mode='HTML',preferred_transport=payload['channel'])
        recheck(engine,payload,context,draft.host)


def clarify(engine,event_id,payload,intelligence):
    try:_authorized(engine,payload,intelligence)
    except Exception:
        engine.s.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(engine.b,event_id));return
    engine.s.queue(engine.b,payload['sender'],NOTICE,'meeting-questions-invalid:'+event_id,engine.now(),preferred_transport=payload['channel'])
    _authorized(engine,payload,intelligence)
    engine.s.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(engine.b,event_id))
