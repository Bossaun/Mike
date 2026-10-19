"""Disabled durable declared publication observation sample; no account monitoring."""
from dataclasses import dataclass,field
from datetime import datetime,timedelta,timezone
from enum import Enum
import hashlib
import json
import re
from zoneinfo import ZoneInfo
from .table_sqlite_driver import SQLiteTableDriver
from . import table_change_plan as tables
from . import marketing_drafts as marketing


class PublicationReaderUnavailable(ValueError):
    def __init__(self):super().__init__('table_publication_observations_unavailable')


def _deny():raise PublicationReaderUnavailable()

def _id(value):
    if type(value) is not str or not 0<len(value)<=128 or value!=value.strip() or len(value.encode())>512 or any(ord(c)<32 or ord(c)==127 for c in value):_deny()
    return value

def _hash(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def _aware(value):
    if type(value) is not datetime or value.utcoffset() is None or value.astimezone(timezone.utc).astimezone(value.tzinfo).replace(tzinfo=None)!=value.replace(tzinfo=None):_deny()
    return value.astimezone(timezone.utc)

def _instant(value):
    if type(value) is not str or len(value)>40 or re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})',value) is None:_deny()
    parsed=datetime.fromisoformat(value)
    if parsed.isoformat()!=(value[:-1]+'+00:00' if value.endswith('Z') else value):_deny()
    return _aware(parsed)


class SourceRole(Enum):PUBLICATION_SAMPLE='publication_sample'


@dataclass(frozen=True)
class PublicationSource:
    business:str
    account_scope:str
    workbook_id:str
    role:SourceRole
    channel_ids:tuple[str,...]
    revision:int
    valid_from:datetime
    expires_at:datetime
    revoked:bool=False
    def __post_init__(self):
        for value in (self.business,self.account_scope,self.workbook_id):_id(value)
        if re.fullmatch(r'sqlite-book:[0-9a-f]{64}',self.workbook_id) is None or self.role is not SourceRole.PUBLICATION_SAMPLE or type(self.channel_ids) is not tuple or not 1<=len(self.channel_ids)<=3 or len(set(self.channel_ids))!=len(self.channel_ids) or type(self.revision) is not int or not 1<=self.revision<2**53 or type(self.revoked) is not bool:_deny()
        for value in self.channel_ids:_id(value)
        if _aware(self.valid_from)>=_aware(self.expires_at):_deny()


@dataclass(frozen=True)
class ChannelPolicy:
    channel_id:str
    threshold:timedelta
    max_observation_age:timedelta
    def __post_init__(self):
        _id(self.channel_id);marketing._duration(self.threshold);marketing._duration(self.max_observation_age)


def _schema(value):
    names=tuple(getattr(value,key) for key in value.__dataclass_fields__)
    for name in names:_id(name)
    if len(names)!=len(set(names)):_deny()


@dataclass(frozen=True)
class HeadColumns:
    channel_id:str;present:str;observed_at:str;coverage_start:str;coverage_end:str;coverage:str
    def __post_init__(self):_schema(self)
@dataclass(frozen=True)
class PublicationColumns:
    id:str;channel_id:str;published_at:str
    def __post_init__(self):_schema(self)
@dataclass(frozen=True)
class ObservationColumns:
    row_id:str
    kind:str
    head:HeadColumns
    publication:PublicationColumns
    def __post_init__(self):
        _id(self.row_id);_id(self.kind)
        if self.row_id==self.kind:_deny()
        for value,cls in ((self.head,HeadColumns),(self.publication,PublicationColumns)):
            if type(value) is not cls:_deny()
            value.__post_init__()
            if {self.row_id,self.kind}&{getattr(value,key) for key in value.__dataclass_fields__}:_deny()


@dataclass(frozen=True)
class ObservationHead:
    channel_id:str
    present:bool
    observed_at:datetime|None
    coverage_start:datetime|None
    coverage_end:datetime|None
    coverage:str


@dataclass(frozen=True)
class WorkbookPublicationGaps:
    gaps:tuple[marketing.PublicationGap,...]
    observations:tuple[ObservationHead,...]
    policies:tuple[ChannelPolicy,...]
    source:PublicationSource
    as_of:datetime
    source_fingerprint:str
    input_fingerprint:str
    workbook_fingerprint:str
    observation_fingerprints:tuple[str,...]
    scope:str=field(default='declared_synthetic_publication_sample',init=False)
    requires_review:bool=field(default=True,init=False)
    live_authorized:bool=field(default=False,init=False)
    monitoring_authorized:bool=field(default=False,init=False)
    reminder_authorized:bool=field(default=False,init=False)


def _source_data(source):
    source.__post_init__()
    return [source.business,source.account_scope,source.workbook_id,source.role.value,source.channel_ids,source.revision,_aware(source.valid_from).isoformat(),_aware(source.expires_at).isoformat(),source.revoked]


def _book_fingerprint(book):
    if type(book) is not tables.Workbook or type(book.sheets) is not tuple or not 1<=len(book.sheets)<=2:_deny()
    book.__post_init__();_id(book.id);_id(book.revision);count=0;data=[]
    for sheet in book.sheets:
        if type(sheet) is not tables.Sheet or type(sheet.rows) is not tuple:_deny()
        sheet.__post_init__();_id(sheet.id);_id(sheet.revision);count+=len(sheet.rows)
        if count>32:_deny()
        rows=[]
        for row in sheet.rows:
            if type(row) is not tables.Row or type(row.cells) is not tuple or not 1<=len(row.cells)<=16:_deny()
            row.__post_init__();_id(row.revision)
            for cell in row.cells:
                if type(cell) is not tables.Cell:_deny()
                _id(cell.column)
            rows.append(tables._row_data(row))
        data.append([sheet.id,sheet.revision,rows])
    return _hash(['table-publication-full-workbook:v1',book.id,book.revision,data])


class TablePublicationReader:
    def __init__(self,*,driver,current_source,expected_source,sheet_id,columns,policies,enabled=False):
        self._driver=driver;self._callback=current_source;self._source=expected_source;self._sheet=sheet_id;self._columns=columns;self._policies=policies;self._enabled=enabled is True
        method=getattr(driver,'snapshot',None);self._host=(driver,current_source,expected_source,sheet_id,columns,policies,getattr(method,'__self__',None),getattr(method,'__func__',None))

    def _current(self,now):
        driver,callback,source,sheet,columns,policies,owner,function=self._host
        def identity():
            method=getattr(driver,'snapshot',None)
            if getattr(method,'__self__',None) is not owner or getattr(method,'__func__',None) is not function:_deny()
        if self._driver is not driver or type(driver) is not SQLiteTableDriver or self._callback is not callback or not callable(callback) or self._source is not source or type(source) is not PublicationSource or self._sheet is not sheet or self._columns is not columns or type(columns) is not ObservationColumns or self._policies is not policies:_deny()
        identity();data=_source_data(source);_id(sheet);columns.__post_init__()
        if type(policies) is not tuple or len(policies)!=len(source.channel_ids) or any(type(policy) is not ChannelPolicy for policy in policies):_deny()
        for policy in policies:policy.__post_init__()
        if {row.channel_id for row in policies}!=set(source.channel_ids):_deny()
        current=callback()
        if type(current) is not PublicationSource or _source_data(current)!=data or source.revoked or not _aware(source.valid_from)<=now<_aware(source.expires_at):_deny()
        identity()
        if self._driver is not driver or self._callback is not callback or self._source is not source or self._sheet is not sheet or self._columns is not columns or self._policies is not policies:_deny()
        def duration(value):return [value.days,value.seconds,value.microseconds]
        return _hash(['table-publication-source:v1',data,sheet,columns.row_id,columns.kind,[[getattr(schema,key) for key in schema.__dataclass_fields__] for schema in (columns.head,columns.publication)],[[policy.channel_id,duration(policy.threshold),duration(policy.max_observation_age)] for policy in policies]])

    def read_gaps(self,*,now):
        if not self._enabled:_deny()
        try:
            now=_aware(now);source_hash=self._current(now);input_hash=_hash(['table-publication-input:v1',now.isoformat()]);book=self._driver.snapshot()
            if self._current(now)!=source_hash or type(book) is not tables.Workbook or book.id!=self._source.workbook_id:_deny()
            book_hash=_book_fingerprint(book);sheets=[sheet for sheet in book.sheets if sheet.id==self._sheet]
            if len(sheets)!=1 or len(sheets[0].rows)>24:_deny()
            heads={};children={channel:[] for channel in self._source.channel_ids};semantic={channel:set() for channel in self._source.channel_ids};ids=set();proofs=[];total_children=0
            for row in sheets[0].rows:
                cells={cell.column:cell.value for cell in row.cells};technical=_id(cells.get(self._columns.row_id));kind=cells.get(self._columns.kind)
                if technical in ids or type(kind) is not str or kind not in ('head','publication'):_deny()
                ids.add(technical);schema=self._columns.head if kind=='head' else self._columns.publication;names={getattr(schema,key) for key in schema.__dataclass_fields__}|{self._columns.row_id,self._columns.kind}
                if set(cells)!=names or len(row.cells)!=len(names):_deny()
                selected=tables.lookup_row(book,workbook_id=self._source.workbook_id,sheet_id=self._sheet,identity=tables.RowIdentity(self._columns.row_id,technical))
                if tables._row_data(selected)!=tables._row_data(row):_deny()
                data={name:cells[getattr(schema,name)] for name in schema.__dataclass_fields__};channel=_id(data['channel_id'])
                if channel not in children:_deny()
                proof=_hash(['table-publication-row:v1',source_hash,book_hash,tables._row_data(row)]);proofs.append(proof);source_id='table-publication:'+proof
                if kind=='head':
                    if channel in heads or type(data['present']) is not bool:_deny()
                    if data['present']:
                        observed,start,end=(_instant(data[key]) for key in ('observed_at','coverage_start','coverage_end'));marketing._coverage(data['coverage'])
                        head=ObservationHead(channel,True,observed,start,end,data['coverage'])
                    else:
                        if any(data[key] is not None for key in ('observed_at','coverage_start','coverage_end','coverage')):_deny()
                        head=ObservationHead(channel,False,None,None,None,'missing')
                    heads[channel]=(head,source_id)
                else:
                    identifier=_id(data['id']);total_children+=1
                    if identifier in semantic[channel] or len(children[channel])>=12 or total_children>21:_deny()
                    semantic[channel].add(identifier);children[channel].append(marketing.Publication(identifier,channel,_instant(data['published_at']),source_id))
            if set(heads)!=set(self._source.channel_ids):_deny()
            gaps=[];contexts=[]
            for policy in self._policies:
                head,source_id=heads[policy.channel_id];publications=tuple(children[policy.channel_id]);observation=None
                if head.present:observation=marketing.PublicationObservation(head.channel_id,head.observed_at,head.coverage_start,head.coverage_end,head.coverage,source_id,publications)
                elif publications:_deny()
                result=marketing.publication_gap(observation,channel_id=policy.channel_id,now=now,threshold=policy.threshold,max_observation_age=policy.max_observation_age)
                self._result(result,head,source_id,publications);gaps.append(result);contexts.append(head)
            if self._current(now)!=source_hash:_deny()
            if _book_fingerprint(self._driver.snapshot())!=book_hash or self._current(now)!=source_hash:_deny()
            return WorkbookPublicationGaps(tuple(gaps),tuple(contexts),self._policies,self._source,now,source_hash,input_hash,book_hash,tuple(proofs))
        except PublicationReaderUnavailable:raise
        except Exception:raise PublicationReaderUnavailable() from None

    def _result(self,result,head,source_id,publications):
        if type(result) is not marketing.PublicationGap or result.channel_id!=head.channel_id or result.state not in ('gap','recent_publication','unknown') or result.review_required is not True or result.live_authorized is not False or result.coverage!=head.coverage:_deny()
        refs={source_id}|{row.source_id for row in publications} if head.present else set()
        if type(result.source_ids) is not tuple or len(result.source_ids)>13 or len(set(result.source_ids))!=len(result.source_ids) or set(result.source_ids)!=refs:_deny()
        if not head.present:
            if result.state!='unknown' or any(value is not None for value in (result.as_of,result.window_start,result.window_publication_count,result.last_observed_publication)):_deny()
        else:
            if _aware(result.as_of)!=head.coverage_end or _aware(result.window_start)>=head.coverage_end:_deny()
            if result.window_publication_count is not None and (type(result.window_publication_count) is not int or not 0<=result.window_publication_count<=len(publications)):_deny()
            if result.last_observed_publication is not None and _aware(result.last_observed_publication) not in {row.published_at for row in publications}:_deny()
