"""Disabled current task sample + declared nonfinancial KPI review agenda."""
from dataclasses import dataclass,field,fields,is_dataclass
from datetime import date,datetime,timezone
from decimal import Decimal
from zoneinfo import ZoneInfo
import hashlib,json,re
from .engine import Engine
from .store import Store
from .intelligence import Intelligence,intelligence_enabled
from .knowledge import KnowledgeIndex
from .intake import validate_team
from .table_kpi_reader import TableKpiReader,KpiSource,KpiSourceProof,WorkbookKpiReport
from . import operational_agenda_adapter as adapter
from . import operational_reports as reports
class MetricsAgendaUnavailable(ValueError):
    def __init__(self):super().__init__('operational_metrics_agenda_unavailable')
def _deny():raise MetricsAgendaUnavailable()
def _hash(v):return hashlib.sha256(json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def _aware(v):
    if type(v) is not datetime or v.utcoffset() is None or v.astimezone(timezone.utc).astimezone(v.tzinfo).replace(tzinfo=None)!=v.replace(tzinfo=None):_deny()
    return v.astimezone(timezone.utc)
def _id(v):
    if type(v) is not str or not 0<len(v)<=512 or not v.strip() or len(v.encode())>2048 or any(ord(c)<32 or ord(c)==127 for c in v):_deny()
    return v
def _digest(v):
    if type(v) is not str or re.fullmatch('[0-9a-f]{64}',v) is None:_deny()
    return v
def _pack(v):
    if v is None or type(v) in (bool,int):return [type(v).__name__,v]
    if type(v) is str:
        if len(v)>8192:_deny()
        return ['str',v]
    if type(v) is Decimal:
        if not v.is_finite() or len(v.as_tuple().digits)>200 or abs(v.as_tuple().exponent)>200:_deny()
        return ['decimal',str(v)]
    if type(v) is datetime:return ['datetime',_aware(v).isoformat()]
    if type(v) is date:return ['date',v.isoformat()]
    if type(v) is tuple:
        if len(v)>256:_deny()
        return ['tuple',[_pack(x) for x in v]]
    allowed=(KpiSource,KpiSourceProof,WorkbookKpiReport,reports.Period,reports.MetricDefinition,reports.MetricLine,reports.AgendaFact,reports.AgendaPreview,reports.DigestPreview,adapter.OperationalAgendaDraft)
    if type(v) in allowed:return [type(v).__name__,[[f.name,_pack(getattr(v,f.name))] for f in fields(v)]]
    _deny()
def _method(obj,name):
    m=getattr(obj,name,None);return (getattr(m,'__self__',None),getattr(m,'__func__',None))
@dataclass(frozen=True)
class MetricPriority:
    metric_id:str;priority:int
    def __post_init__(self):
        _id(self.metric_id)
        if len(self.metric_id)>128 or type(self.priority) is not int or not 0<=self.priority<=100:_deny()
@dataclass(frozen=True)
class CombinedMetricsAgenda:
    agenda:reports.AgendaPreview
    task_sample:adapter.OperationalAgendaDraft
    kpi_report:WorkbookKpiReport
    kpi_source_proof:KpiSourceProof
    task_snapshot_digest:str
    task_consulted_sources:tuple[str,...]
    kpi_consulted_sources:tuple[str,...]
    kpi_observation_fingerprints:tuple[tuple[str,str],...]
    kpi_unobserved_metric_ids:tuple[str,...]
    input_fingerprint:str
    scope:str=field(default='current_authorized_task_sample_and_declared_nonfinancial_kpi_subset',init=False)
    requires_review:bool=field(default=True,init=False)
    live_authorized:bool=field(default=False,init=False)
    staff_assessment_authorized:bool=field(default=False,init=False)
    dispatch_authorized:bool=field(default=False,init=False)
class OperationalMetricsAgenda:
    def __init__(self,*,host,kpi_reader,expected_kpi_source,priorities,enabled=False):
        self._host=host;self._reader=kpi_reader;self._source=expected_kpi_source;self._priorities=priorities;self._enabled=enabled is True
        s=getattr(host,'s',None);intel=getattr(host,'intelligence',None)
        self._captured=(host,s,getattr(s,'db',None),getattr(host,'c',None),getattr(host,'b',None),intel,getattr(intel,'index',None),getattr(host,'now',None),getattr(host,'tz',None),kpi_reader,expected_kpi_source,priorities,adapter.build_agenda_preview,reports.agenda_preview,_method(kpi_reader,'source_proof'),_method(kpi_reader,'read_daily'))
    def _policy(self,sender):
        e,s,db,c,b,intel,index,clock,tz,reader,source,priorities,build,agenda,proof,read=self._captured
        if self._host is not e or type(e) is not Engine or e.s is not s or type(s) is not Store or s.db is not db or e.c is not c or type(c) is not dict or e.b!=b or e.intelligence is not intel or type(intel) is not Intelligence or intel.s is not s or intel.c is not c or intel.b!=b or intel.index is not index or type(index) is not KnowledgeIndex or index.store is not s or e.now is not clock or e.tz is not tz or type(tz) is not ZoneInfo or tz.key!=c.get('timezone') or self._reader is not reader or type(reader) is not TableKpiReader or self._source is not source or type(source) is not KpiSource or self._priorities is not priorities or adapter.build_agenda_preview is not build or reports.agenda_preview is not agenda:_deny()
        for actual,pinned in ((_method(reader,'source_proof'),proof),(_method(reader,'read_daily'),read)):
            if actual[0] is not pinned[0] or actual[1] is not pinned[1]:_deny()
        validate_team(c)
        if not intelligence_enabled(c) or type(c.get('admins')) is not list or len(c['admins'])!=1 or sender!=c['admins'][0] or type(sender) is not str or c.get('business')!=b:_deny()
        source.__post_init__()
        if source.business!=b or source.owner_id not in set(c['admins'])|set(c.get('employees',{})):_deny()
        if type(priorities) is not tuple or not 1<=len(priorities)<=3 or any(type(p) is not MetricPriority for p in priorities):_deny()
        for p in priorities:p.__post_init__()
        if len({p.metric_id for p in priorities})!=len(priorities):_deny()
        keys=('business','admins','employees','timezone','team_intake','team_roster_verified','business_account_user_id','allowed_live_recipients','live_send','recipients_verified','review_mode','sheets_write','daily_summary','allow_onboarding_replies','allow_registered_replies','allowed_group_ids','group_destinations','meeting_recipient','bot_user_id_verified','bot_user_id','intelligence')
        policy=json.dumps({k:c.get(k) for k in keys},ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
        if len(policy.encode())>65536:_deny()
        return _hash(['operational-metrics-policy:v1',policy,_pack(source),[[p.metric_id,p.priority] for p in priorities]])
    def _input(self,sender,as_of,period,definitions,complete,observed,max_items):
        if type(period) is not reports.Period or type(definitions) is not tuple or not 1<=len(definitions)<=3 or type(complete) is not bool or type(observed) is not date or type(max_items) is not int or not 1<=max_items<=5:_deny()
        period.__post_init__()
        if (period.end-period.start).days!=1:_deny()
        for d in definitions:
            if type(d) is not reports.MetricDefinition:_deny()
            d.__post_init__()
            if d.currency is not None or d.unit=='money' or len(d.label)>80 or len(d.unit)>32:_deny()
            if d.target is not None and (type(d.target) is not Decimal or d.target<0):_deny()
        if len({d.id for d in definitions})!=len(definitions) or {d.id for d in definitions}!={p.metric_id for p in self._priorities}:_deny()
        return _hash(['operational-metrics-input:v1',sender,_aware(as_of).isoformat(),_pack(period),_pack(definitions),complete,observed.isoformat(),max_items])
    def _proof(self,as_of):
        value=self._reader.source_proof(now=as_of)
        if type(value) is not KpiSourceProof or type(value.source) is not KpiSource or _pack(value.source)!=_pack(self._source):_deny()
        _digest(value.fingerprint);return value
    def _task(self,sender,as_of):
        e=self._host;value=adapter.build_agenda_preview(e.s,e.c,sender,e.intelligence.index,as_of=as_of,max_items=5)
        if type(value) is not adapter.OperationalAgendaDraft or value.business!=e.b or type(value.agenda) is not reports.AgendaPreview or value.agenda.as_of!=as_of or len(value.agenda.facts)>5 or value.requires_review is not True or value.live_send_authorized is not False or type(value.digest) is not reports.DigestPreview:_deny()
        _digest(value.snapshot_digest)
        if type(value.digest.sources) is not tuple or len(value.digest.sources)>128:_deny()
        for ref in value.digest.sources:_id(ref)
        for fact in value.agenda.facts:
            if type(fact) is not reports.AgendaFact:_deny()
            fact.__post_init__()
        return value
    def _kpi(self,as_of,period,definitions,complete,observed,proof):
        value=self._reader.read_daily(period=period,definitions=definitions,coverage_complete=complete,coverage_observed_on=observed,now=as_of)
        if type(value) is not WorkbookKpiReport or _pack(value.source)!=_pack(proof.source) or value.source_fingerprint!=proof.fingerprint or value.period!=period or value.as_of!=as_of or value.requires_review is not True or value.live_authorized is not False or value.staff_assessment_authorized is not False or type(value.lines) is not tuple or len(value.lines)!=len(definitions):_deny()
        for v in (value.source_fingerprint,value.workbook_fingerprint,value.coverage_fingerprint):_digest(v)
        if type(value.observation_fingerprints) is not tuple or len(value.observation_fingerprints)!=len(definitions) or {x[0] for x in value.observation_fingerprints}!={d.id for d in definitions}:_deny()
        for pair in value.observation_fingerprints:
            if type(pair) is not tuple or len(pair)!=2:_deny()
            _id(pair[0]);_digest(pair[1])
        if type(value.unobserved_metric_ids) is not tuple or len(set(value.unobserved_metric_ids))!=len(value.unobserved_metric_ids) or not set(value.unobserved_metric_ids)<={d.id for d in definitions}:_deny()
        by_id={d.id:d for d in definitions};seen=set()
        for line in value.lines:
            if type(line) is not reports.MetricLine or line.metric_id not in by_id or line.metric_id in seen or line.owner!=proof.source.owner_id or line.currency is not None or line.unit!=by_id[line.metric_id].unit or _pack(line.target)!=_pack(by_id[line.metric_id].target) or line.status not in ('known','provisional','unknown') or line.target_state not in ('met','missed','informational','unknown') or type(line.sources) is not tuple or not 1<=len(line.sources)<=2:_deny()
            seen.add(line.metric_id)
            if line.value is not None and (type(line.value) is not Decimal or not line.value.is_finite() or line.value<0):_deny()
            if (line.status=='unknown')!=(line.value is None) or (line.status!='known' and line.target_state!='unknown'):_deny()
            for ref in line.sources:_id(ref)
        return value
    def preview(self,*,sender,as_of,period,definitions,coverage_complete,coverage_observed_on,max_items):
        if not self._enabled:_deny()
        try:
            as_of=_aware(as_of);policy=self._policy(sender);inputs=self._input(sender,as_of,period,definitions,coverage_complete,coverage_observed_on,max_items)
            def check():
                if self._policy(sender)!=policy or self._input(sender,as_of,period,definitions,coverage_complete,coverage_observed_on,max_items)!=inputs:_deny()
            proof=self._proof(as_of);check();task=self._task(sender,as_of);check();kpi=self._kpi(as_of,period,definitions,coverage_complete,coverage_observed_on,proof);check()
            proof2=self._proof(as_of);check()
            if _pack(proof2)!=_pack(proof):_deny()
            kpi2=self._kpi(as_of,period,definitions,coverage_complete,coverage_observed_on,proof2);check();proof3=self._proof(as_of);check()
            if _pack(proof3)!=_pack(proof) or _pack(kpi2)!=_pack(kpi):_deny()
            task2=self._task(sender,as_of);check()
            if _pack(task2)!=_pack(task):_deny()
            by_id={d.id:d for d in definitions};priorities={p.metric_id:p.priority for p in self._priorities};metricfacts=[]
            for line in kpi.lines:
                d=by_id[line.metric_id]
                status={'known':'переданное значение','provisional':'предварительные данные','unknown':'нет наблюдения'}[line.status]
                target={'met':'достигнута по переданным данным','missed':'не достигнута по переданным данным','informational':'информационный показатель','unknown':'неизвестно'}[line.target_state]
                value=str(line.value) if line.value is not None else 'нет наблюдения'
                text=f'{d.label}: {value} {line.unit}; данные: {status}; цель: {target}.'
                if len(text)>300:_deny()
                identifier='metric:'+_hash([proof.source.business,proof.source.owner_id,line.metric_id])
                metricfacts.append(reports.AgendaFact(identifier,text,'metric','unknown' if line.status=='unknown' else 'provisional',priorities[line.metric_id],line.sources))
            facts=task.agenda.facts+tuple(metricfacts);agenda=reports.agenda_preview(facts=facts,as_of=as_of,max_items=max_items);check()
            if type(agenda) is not reports.AgendaPreview or agenda.as_of!=as_of or type(agenda.facts) is not tuple or len(agenda.facts)>max_items or type(agenda.omitted) is not int or agenda.omitted!=len(facts)-len(agenda.facts):_deny()
            supplied={f.id:_pack(f) for f in facts}
            if len({f.id for f in agenda.facts})!=len(agenda.facts) or any(f.id not in supplied or _pack(f)!=supplied[f.id] for f in agenda.facts):_deny()
            text='\n'.join(f.text for f in agenda.facts)
            if len(text.encode('utf-16-le'))//2>3500 or len(text.encode())>8000:_deny()
            refs=tuple(sorted({ref for line in kpi.lines for ref in line.sources}|{'table-kpi:'+kpi.coverage_fingerprint}|{'table-kpi:'+fp for key,fp in kpi.observation_fingerprints}))
            return CombinedMetricsAgenda(agenda,task,kpi,proof,task.snapshot_digest,task.digest.sources,refs,kpi.observation_fingerprints,kpi.unobserved_metric_ids,inputs)
        except MetricsAgendaUnavailable:raise
        except Exception:raise MetricsAgendaUnavailable() from None
