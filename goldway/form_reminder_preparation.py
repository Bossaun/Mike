"""Disabled current form questions at ONE existing registered C07 slot.

Readonly local preparation, never scheduling, consent, collection or delivery.
"""
from dataclasses import dataclass,field,fields
from datetime import date,datetime,timezone
from decimal import Decimal
from enum import Enum
from zoneinfo import ZoneInfo
import hashlib,json,math,re
from .engine import Engine
from .store import Store
from .intelligence import Intelligence,intelligence_enabled
from .knowledge import KnowledgeIndex
from .scheduling import Scheduler
from .intake import validate_team
from .answer_citations import _scope
from .monthly_task_history import _Reader,_bounded_columns,_text,_numeric
from . import task_card_command as cards
from . import table_form_reconciliation as forms
from . import operational_reports as reports
from . import reminder_times


class FormReminderUnavailable(ValueError):
    def __init__(self):super().__init__('form_reminder_preparation_unavailable')
def _deny():raise FormReminderUnavailable()
def _hash(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def _id(value):
    if type(value) is not str or not 0<len(value)<=160 or value!=value.strip() or len(value.encode())>640 or any(ord(c)<32 or ord(c)==127 for c in value):_deny()
def _digest(value):
    if type(value) is not str or re.fullmatch('[0-9a-f]{64}',value) is None:_deny()
def _pack(value):
    if value is None or type(value) in (bool,int):return [type(value).__name__,value]
    if type(value) is str:
        if len(value.encode())>32768:_deny()
        return ['str',value]
    if type(value) is Decimal:
        if not value.is_finite():_deny()
        return ['decimal',str(value)]
    if type(value) is datetime:
        reports._aware(value);return ['datetime',value.astimezone(timezone.utc).isoformat()]
    if type(value) is date:return ['date',value.isoformat()]
    if type(value) is forms.SourceRole:return ['SourceRole',value.value]
    if type(value) is tuple:
        if len(value)>256:_deny()
        return ['tuple',[_pack(v) for v in value]]
    if type(value) in (forms.FormSource,forms.SourcePair,forms.FormSourceProof,forms.WorkbookFormReport,
            reports.Period,reports.MetricDefinition,reports.FormLine,cards.TaskBriefSource,ReportReminderBinding):
        return [type(value).__name__,[[f.name,_pack(getattr(value,f.name))] for f in fields(value)]]
    _deny()
def _method(obj,name):
    value=getattr(obj,name,None);return (getattr(value,'__self__',None),getattr(value,'__func__',None))


@dataclass(frozen=True)
class ReportReminderBinding:
    task_number:int
    series_id:str
    metric_ids:tuple[str,...]
    def __post_init__(self):
        if type(self.task_number) is not int or not 1<=self.task_number<=999999999:_deny()
        _id(self.series_id)
        if type(self.metric_ids) is not tuple or not 1<=len(self.metric_ids)<=3:_deny()
        for value in self.metric_ids:_id(value)
        if len(set(self.metric_ids))!=len(self.metric_ids):_deny()


@dataclass(frozen=True)
class PreparedFormReminder:
    text:str
    questions:tuple[str,...]
    state:str
    stored_slot:datetime
    report:forms.WorkbookFormReport
    source_proof:forms.FormSourceProof
    task_source:cards.TaskBriefSource
    owner_task_source:cards.TaskBriefSource
    series_fingerprint:str
    input_fingerprint:str
    evidence_ids:tuple[str,...]
    as_of:datetime
    requires_review:bool=field(default=True,init=False)
    delivery_authorized:bool=field(default=False,init=False)
    live_authorized:bool=field(default=False,init=False)
    collection_authorized:bool=field(default=False,init=False)


class FormReminderPreparation:
    def __init__(self,*,engine,reconciler,expected_sources,binding,enabled=False):
        self._engine=engine;self._reader=reconciler;self._sources=expected_sources;self._binding=binding;self._enabled=enabled is True
        s=getattr(engine,'s',None);intel=getattr(engine,'intelligence',None);scheduler=getattr(engine,'scheduler',None)
        self._host=(engine,s,getattr(s,'db',None),getattr(engine,'c',None),getattr(engine,'b',None),intel,
            getattr(intel,'index',None),getattr(engine,'now',None),getattr(engine,'tz',None),scheduler,
            getattr(scheduler,'now',None),getattr(scheduler,'permitted',None),getattr(scheduler,'spawn',None),
            reconciler,expected_sources,binding,_method(reconciler,'source_proof'),_method(reconciler,'read_reconciliation'),
            cards.build_task_brief_source,reminder_times.next_occurrence)

    def _policy(self,payload):
        e,s,db,c,b,intel,index,clock,tz,scheduler,sclock,permitted,spawn,reader,sources,binding,proof,read,task,next_slot=self._host
        if (not self._enabled or self._engine is not e or type(e) is not Engine or e.s is not s or type(s) is not Store or s.db is not db
                or e.c is not c or type(c) is not dict or e.b!=b or e.intelligence is not intel or type(intel) is not Intelligence
                or intel.s is not s or intel.c is not c or intel.b!=b or intel.index is not index or type(index) is not KnowledgeIndex
                or index.store is not s or e.now is not clock or not callable(clock) or e.tz is not tz or type(tz) is not ZoneInfo
                or tz.key!=c.get('timezone') or e.scheduler is not scheduler or type(scheduler) is not Scheduler
                or scheduler.s is not s or scheduler.c is not c or scheduler.b!=b or scheduler.now is not sclock
                or scheduler.permitted is not permitted or scheduler.spawn is not spawn or self._reader is not reader
                or type(reader) is not forms.TableFormReconciler or self._sources is not sources or type(sources) is not forms.SourcePair
                or self._binding is not binding or type(binding) is not ReportReminderBinding
                or cards.build_task_brief_source is not task or reminder_times.next_occurrence is not next_slot):_deny()
        for name,pin in (('source_proof',proof),('read_reconciliation',read)):
            actual=_method(reader,name)
            if actual[0] is not pin[0] or actual[1] is not pin[1]:_deny()
        validate_team(c);sources.__post_init__();binding.__post_init__()
        if not intelligence_enabled(c) or c.get('business')!=b or type(c.get('admins')) is not list or len(c['admins'])!=1 or c.get('scheduling',{}).get('enabled') is not True:_deny()
        if type(payload) is not dict or set(payload)!={'sender','channel','source_id'} or any(type(v) is not str for v in payload.values()):_deny()
        if payload['sender']!=c['admins'][0]:_deny()
        scope,parts=_scope(c,payload)
        if parts[3]!=str(c['employees'][payload['sender']]['telegram_user_id']):_deny()
        if sources.form.business!=b or sources.form.owner_id not in c['employees'] or sources.form.owner_id not in c['allowed_live_recipients'] or sources.form.timezone_name!=tz.key:_deny()
        keys=('business','timezone','employees','admins','team_intake','allowed_live_recipients','business_account_user_id','team_roster_verified',
            'recipients_verified','live_send','review_mode','intelligence','scheduling','sheets_write','daily_summary','allowed_group_ids',
            'group_destinations','meeting_recipient','allow_onboarding_replies','allow_registered_replies','bot_user_id','bot_user_id_verified')
        policy={k:c.get(k) for k in keys}
        if len(json.dumps(policy,ensure_ascii=False,allow_nan=False).encode())>65536:_deny()
        return _hash([scope,policy,_pack(sources),_pack(binding)])

    def _inputs(self,period,definitions,monday,complete,observed):
        if type(period) is not reports.Period or type(definitions) is not tuple or not 1<=len(definitions)<=3 or type(monday) is not date or type(complete) is not bool or type(observed) is not date:_deny()
        period.__post_init__()
        for value in definitions:
            if type(value) is not reports.MetricDefinition:_deny()
            value.__post_init__()
            if value.target is not None or value.direction!='informational' or len(value.label)>120:_deny()
        if len({d.id for d in definitions})!=len(definitions) or {d.id for d in definitions}!=set(self._binding.metric_ids):_deny()
        return _hash([_pack(period),_pack(definitions),monday.isoformat(),complete,observed.isoformat()])

    def _proof(self,now):
        value=self._reader.source_proof(now=now)
        if type(value) is not forms.FormSourceProof or _pack(value.sources)!=_pack(self._sources):_deny()
        _digest(value.fingerprint);return value

    def _owner(self,reader,task):
        spec={'id':_text('id',160),'owner':_text('owner',128),'status':_text('status',32)}
        rows=reader.query('SELECT '+_bounded_columns(spec)+' FROM tasks WHERE business=? AND id=?',(self._engine.b,task.task_id))
        if len(rows)!=1 or rows[0]['owner']!=self._sources.form.owner_id or rows[0]['status'] not in ('confirmed','in_progress','blocked'):_deny()
        return rows[0]

    def _series(self,reader,task,owner,sender,now):
        # Compare the bounded frozen template inside SQLite, never return its
        # evidence/body. Current links must equal the explicit original set.
        matching=' AND '.join("json_extract(s.snapshot,'$."+field+"') IS t."+field for field in ('title','owner','deadline','next_step','criterion','source_id'))
        snapshot="""CASE WHEN typeof(s.snapshot)='text' AND length(CAST(s.snapshot AS BLOB))<=32768 AND json_valid(s.snapshot)
            THEN (SELECT count(*) FROM json_each(s.snapshot))=8 AND (SELECT count(DISTINCT key) FROM json_each(s.snapshot))=8
            AND NOT EXISTS(SELECT 1 FROM json_each(s.snapshot) WHERE key NOT IN ('title','owner','deadline','next_step','criterion','source_id','evidence','source_ids'))
            AND json_type(s.snapshot,'$.source_ids')='array' AND json_array_length(s.snapshot,'$.source_ids') BETWEEN 1 AND 64
            AND NOT EXISTS(SELECT 1 FROM json_each(s.snapshot,'$.source_ids') j WHERE j.type!='text' OR length(CAST(j.value AS BLOB))>256
                OR (j.value IS NOT t.source_id AND NOT EXISTS(SELECT 1 FROM task_sources l WHERE l.business=s.business AND l.task_id=s.task_id AND l.source_id=j.value)))
            AND (SELECT count(DISTINCT value) FROM json_each(s.snapshot,'$.source_ids'))=json_array_length(s.snapshot,'$.source_ids')
            AND NOT EXISTS(SELECT 1 FROM task_sources l WHERE l.business=s.business AND l.task_id=s.task_id
                AND NOT EXISTS(SELECT 1 FROM json_each(s.snapshot,'$.source_ids') j WHERE j.value=l.source_id))
            AND EXISTS(SELECT 1 FROM json_each(s.snapshot,'$.source_ids') j WHERE j.value=t.source_id)
            AND """+matching+" ELSE 0 END"
        spec={'id':_text('s.id',160),'task_id':_text('s.task_id',160),'actor':_text('s.actor',128),'owner':_text('s.owner',128),
            'rule':_text('s.rule',4096),'next_at':_numeric('s.next_at'),'source_at':_numeric('s.source_at'),'created':_numeric('s.created'),'state':_text('s.state',16)}
        # Explicit aliases because the reusable projection expects column names.
        columns=_bounded_columns({'s.'+key:guard for key,guard in spec.items()}).replace(' AS s.',' AS ')
        rows=reader.query('SELECT '+columns+', '+snapshot+" AS snapshot_matches, NOT EXISTS(SELECT 1 FROM c07_series x WHERE x.business=s.business AND x.task_id=s.task_id AND x.state='active' AND x.id!=s.id LIMIT 1) AS unambiguous FROM c07_series s JOIN tasks t ON t.business=s.business AND t.id=s.task_id WHERE s.business=? AND s.id=?",(self._engine.b,self._binding.series_id))
        if len(rows)!=1:_deny()
        row=rows[0]
        if row['state']!='active' or row['task_id']!=task.task_id or row['owner']!=owner or row['actor']!=sender or row['snapshot_matches']!=1 or row['unambiguous']!=1 or row['created']>now or row['source_at']>row['created'] or row['next_at']<now:_deny()
        return row

    def prepare(self,scope_payload,*,period,definitions,monday,coverage_complete,coverage_observed_on):
        if not self._enabled:_deny()
        try:
            policy=self._policy(scope_payload);payload=dict(scope_payload)
            inputs=self._inputs(period,definitions,monday,coverage_complete,coverage_observed_on)
            def check():
                if self._policy(scope_payload)!=policy or scope_payload!=payload or self._inputs(period,definitions,monday,coverage_complete,coverage_observed_on)!=inputs:_deny()
            stamps=[]
            def clock():
                value=self._host[7]();check()
                if type(value) not in (int,float) or not math.isfinite(value) or not 0<=value<2**53 or (stamps and value<stamps[-1]):_deny()
                stamps.append(value);return value
            as_of=datetime.fromtimestamp(clock(),timezone.utc);metadata=_Reader(self._engine.s)
            proof=self._proof(as_of);check()
            e=self._engine
            task_stamp=clock()
            fixed_clock=lambda:task_stamp
            task=cards.build_task_brief_source(e.s,e.c,payload['sender'],e.intelligence.index,clock=fixed_clock,number=self._binding.task_number);check()
            owner=self._owner(metadata,task)['owner']
            owner_task=cards.build_task_brief_source(e.s,e.c,owner,e.intelligence.index,clock=fixed_clock,number=self._binding.task_number,reader=metadata);check()
            series=self._series(metadata,task,owner,payload['sender'],stamps[-1]);check()
            def pairs(values):
                result={}
                for k,v in values:
                    if k in result:_deny()
                    result[k]=v
                return result
            rule=json.loads(series['rule'],object_pairs_hook=pairs)
            if type(rule) is not dict or type(rule.get('weekdays')) is not list or len(rule['weekdays'])>7:_deny()
            saved_slot=datetime.fromtimestamp(series['next_at'],e.tz)
            actual_slot=reminder_times.next_occurrence(rule,saved_slot,inclusive=True);check()
            if actual_slot.astimezone(timezone.utc)!=saved_slot.astimezone(timezone.utc) or rule['timezone']!=e.tz.key:_deny()
            args=dict(period=period,definitions=definitions,monday=monday,coverage_complete=coverage_complete,coverage_observed_on=coverage_observed_on,now=as_of)
            first=self._reader.read_reconciliation(**args);check()
            if type(first) is not forms.WorkbookFormReport or _pack(first.sources)!=_pack(proof.sources) or first.source_fingerprint!=proof.fingerprint or first.period!=period or first.as_of!=as_of or type(first.lines) is not tuple or len(first.lines)!=len(definitions):_deny()
            # Finish OUR external clock/source callbacks before the final data
            # read: metadata equality alone cannot seal a changed workbook.
            task_stamp=clock()
            current_proof=self._proof(datetime.fromtimestamp(task_stamp,timezone.utc));check()
            if _pack(current_proof)!=_pack(proof):_deny()
            second=self._reader.read_reconciliation(**args);check()
            if _pack(second)!=_pack(first):_deny()
            # Frozen sampled time: no external callback after the final data
            # pass, including between the final actor and owner task checks.
            final_task=cards.build_task_brief_source(e.s,e.c,payload['sender'],e.intelligence.index,clock=fixed_clock,number=self._binding.task_number);check()
            final_owner_task=cards.build_task_brief_source(e.s,e.c,owner,e.intelligence.index,clock=fixed_clock,number=self._binding.task_number,reader=metadata);check()
            if final_task!=task or final_owner_task!=owner_task or self._owner(metadata,final_task)['owner']!=owner or self._series(metadata,final_task,owner,payload['sender'],stamps[-1])!=series:_deny()
            questions=[];all_refs=set(task.evidence_ids)|set(owner_task.evidence_ids)
            for line in first.lines:
                if type(line) is not reports.FormLine or line.owner!=owner or line.metric_id not in self._binding.metric_ids:_deny()
                if line.question is not None:
                    if type(line.question) is not str or not 0<len(line.question)<=500 or any(ord(c)<32 or ord(c)==127 for c in line.question):_deny()
                    questions.append(line.question)
                all_refs.update(line.sources)
            text='Вопросы по переданному снимку бланка для ручной проверки.\nСохранённое время серии: '+saved_slot.strftime('%d.%m.%Y %H:%M')+' ('+e.tz.key+').\n'
            text+=('\n'.join('- '+q for q in questions) if questions else 'В текущем снимке сверка не сформировала вопроса. Это не подтверждает завершение отчёта.')
            text+='\nЧерновик не отправлен. Сохранённая серия не изменена; согласие сотрудника и доставка этим не подтверждаются.'
            if len(text.encode('utf-16-le'))//2>3500 or len(text.encode())>8000:_deny()
            check()
            return PreparedFormReminder(text,tuple(questions),'questions_prepared' if questions else 'no_current_question',saved_slot,first,proof,task,owner_task,_hash(series),inputs,tuple(sorted(all_refs)),as_of)
        except FormReminderUnavailable:raise
        except Exception:raise FormReminderUnavailable() from None
