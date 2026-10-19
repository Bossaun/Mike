"""Disabled readonly current-task metadata + explicitly supplied design fields.

Host scope metadata is an assertion, not authentication or delivery permission.
No task/source/event/outbox writes, assets lookup, designer selection or model.
"""
from dataclasses import dataclass,field
from datetime import datetime
from zoneinfo import ZoneInfo
import hashlib,json,math
from .engine import Engine
from .store import Store
from .intelligence import Intelligence,intelligence_enabled
from .knowledge import KnowledgeIndex
from .intake import validate_team
from .answer_citations import _scope
from . import task_card_command as cards
from . import design_brief_command as legacy


class TaskDesignBriefUnavailable(ValueError):
    def __init__(self):super().__init__('task_design_brief_unavailable')


def _deny():raise TaskDesignBriefUnavailable()


def _hash(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class TaskDesignDraft:
    text:str
    brief:legacy.EditableDraft
    task_source:cards.TaskBriefSource
    evidence_ids:tuple[str,...]
    input_fingerprint:str
    as_of:datetime
    requires_review:bool=field(default=True,init=False)
    live_authorized:bool=field(default=False,init=False)
    delivery_authorized:bool=field(default=False,init=False)


class TaskDesignBrief:
    def __init__(self,*,engine,enabled=False):
        self._engine=engine;self._enabled=enabled is True
        store=getattr(engine,'s',None);intel=getattr(engine,'intelligence',None)
        self._captured=(engine,store,getattr(store,'db',None),getattr(engine,'c',None),getattr(engine,'b',None),
            intel,getattr(intel,'index',None),getattr(engine,'now',None),getattr(engine,'tz',None),
            cards.build_task_brief_source,legacy.build_brief)

    def _policy(self,payload):
        e,s,db,c,b,intel,index,clock,tz,source,build=self._captured
        if (not self._enabled or self._engine is not e or type(e) is not Engine or type(s) is not Store
                or e.s is not s or s.db is not db or e.c is not c or type(c) is not dict or e.b!=b
                or e.intelligence is not intel or type(intel) is not Intelligence or intel.s is not s
                or intel.c is not c or intel.b!=b or intel.index is not index or type(index) is not KnowledgeIndex
                or index.store is not s or e.now is not clock or not callable(clock) or e.tz is not tz
                or type(tz) is not ZoneInfo or tz.key!=c.get('timezone')
                or cards.build_task_brief_source is not source or legacy.build_brief is not build):_deny()
        validate_team(c)
        if not intelligence_enabled(c) or c.get('business')!=b or type(c.get('admins')) is not list or len(c['admins'])!=1:_deny()
        if type(payload) is not dict or set(payload)!={'sender','channel','source_id'} or any(type(v) is not str for v in payload.values()):_deny()
        if payload['sender']!=c['admins'][0] or len(payload['source_id'])>512:_deny()
        scope,parts=_scope(c,payload)
        if parts[3]!=str(c['employees'][payload['sender']]['telegram_user_id']):_deny()
        keys=('business','admins','employees','timezone','team_intake','team_roster_verified','business_account_user_id',
            'allowed_live_recipients','live_send','recipients_verified','review_mode','sheets_write','daily_summary',
            'allow_onboarding_replies','allow_registered_replies','allowed_group_ids','group_destinations','meeting_recipient',
            'bot_user_id_verified','bot_user_id','intelligence')
        policy={k:c.get(k) for k in keys}
        encoded=json.dumps(policy,ensure_ascii=False,sort_keys=True,allow_nan=False)
        if len(encoded.encode())>65536:_deny()
        return _hash([scope,policy])

    def _inputs(self,payload,number,goal,audience,format,materials):
        if type(number) is not int or not 1<=number<=999999999:_deny()
        values=(goal,audience,format,materials)
        for value,limit in zip(values,(1000,1000,100,1000)):
            if value is not None and (type(value) is not str or not value.strip() or len(value)>limit
                    or any(ord(c)<32 or ord(c)==127 or c in '\u0085\u2028\u2029' for c in value)):_deny()
        block='ТЗ дизайнеру\n'+'\n'.join(label+': '+(value if value is not None else '[уточнить]')
            for label,value in zip(('Цель','Аудитория','Формат','Материалы'),values))
        if len(block)>4000 or len(block.encode())>8000:_deny()
        return block,_hash([payload,number,list(values)])

    def preview(self,scope_payload,number,goal,audience,format,materials):
        if not self._enabled:_deny()
        try:
            policy=self._policy(scope_payload)
            payload=dict(scope_payload)
            block,inputs=self._inputs(scope_payload,number,goal,audience,format,materials)
            def check():
                if (self._policy(scope_payload)!=policy or scope_payload!=payload
                        or self._inputs(scope_payload,number,goal,audience,format,materials)!=(block,inputs)):_deny()
            e=self._engine;clock=self._captured[7];stamps=[]
            def read_clock():
                value=clock();check()
                if (type(value) not in (int,float) or not math.isfinite(value) or not 0<=value<=2**53-1
                        or (stamps and value<stamps[-1])):_deny()
                stamps.append(value)
                return value
            source=cards.build_task_brief_source(e.s,e.c,payload['sender'],e.intelligence.index,clock=read_clock,number=number)
            check()
            if type(source) is not cards.TaskBriefSource or source.status not in ('confirmed','in_progress','blocked'):_deny()
            # Legacy's correlation argument is an opaque snapshot key, NOT an
            # invented/accepted inbound event. Nothing is persisted or actioned.
            brief=legacy.build_brief(block,business=e.b,event_id=source.snapshot_digest)
            if (type(brief) is not legacy.EditableDraft or brief.source_ids!=() or brief.review_required is not True
                    or brief.live_authorized is not False or type(brief.text) is not str):_deny()
            check()
            second=cards.build_task_brief_source(e.s,e.c,payload['sender'],e.intelligence.index,clock=read_clock,number=number)
            check()
            if second!=source:_deny()
            final_now=stamps[-1]
            status={'confirmed':'подтверждена в записи','in_progress':'в работе по записи','blocked':'есть препятствие по записи'}[source.status]
            text=('Связь с текущей задачей №'+str(source.public_number)+'\nНазвание: '+source.title+
                '\nКритерий задачи: '+source.criterion+'\nЗаписанный срок задачи: '+(source.deadline if source.deadline is not None else 'не записан')+
                '\nСостояние задачи: '+status+'\n\n'+brief.text+
                '\n\nДанные задачи показаны отдельно от цели и срока ТЗ. Результат работы независимо не проверен. Дизайнер не выбран; черновик не отправлен.')
            if len(text.encode('utf-16-le'))//2>3500 or len(text.encode())>8000:_deny()
            return TaskDesignDraft(text,brief,source,source.evidence_ids,inputs,datetime.fromtimestamp(final_now,e.tz))
        except TaskDesignBriefUnavailable:raise
        except Exception:raise TaskDesignBriefUnavailable() from None
