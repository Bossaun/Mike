"""Default-disabled declared campaign count sample, without delivery authority."""
from dataclasses import dataclass,field
from datetime import datetime,timedelta,timezone
from decimal import Decimal
from enum import Enum
import hashlib,json,re
from .table_sqlite_driver import SQLiteTableDriver
from . import table_change_plan as tables
from . import marketing_drafts as marketing

class CampaignReaderUnavailable(ValueError):
    def __init__(self):super().__init__('table_campaign_report_unavailable')
def _deny():raise CampaignReaderUnavailable()

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
    return _hash(['table-campaign-full-workbook:v1',book.id,book.revision,data])


def _text(value,limit):
    if type(value) is not str or not value.strip() or len(value.encode('utf-16-le'))//2>limit or any(ord(c)<32 or ord(c)==127 for c in value):_deny()
    return value

def _count(value):
    if type(value) is int:
        if not 0<=value<=10**9:_deny()
        return Decimal(value)
    if type(value) is not Decimal or not value.is_finite() or len(value.as_tuple().digits)>50 or abs(value.as_tuple().exponent)>18 or not 0<=value<=10**9 or value!=value.to_integral_value():_deny()
    return value

class SourceRole(Enum):CAMPAIGN_SAMPLE='campaign_sample'
@dataclass(frozen=True)
class CampaignSource:
    business:str;account_scope:str;workbook_id:str;role:SourceRole;campaign_id:str;revision:int;valid_from:datetime;expires_at:datetime;revoked:bool=False
    def __post_init__(self):
        for v in (self.business,self.account_scope,self.workbook_id,self.campaign_id):_id(v)
        if re.fullmatch(r'sqlite-book:[0-9a-f]{64}',self.workbook_id) is None or self.role is not SourceRole.CAMPAIGN_SAMPLE or type(self.revision) is not int or not 1<=self.revision<2**53 or type(self.revoked) is not bool or _aware(self.valid_from)>=_aware(self.expires_at):_deny()

def _schema(schema):
    names=tuple(getattr(schema,k) for k in schema.__dataclass_fields__)
    for n in names:_id(n)
    if len(names)!=len(set(names)):_deny()
@dataclass(frozen=True)
class HeadColumns:
    campaign_id:str;start:str;end:str
    def __post_init__(self):_schema(self)
@dataclass(frozen=True)
class DefinitionColumns:
    campaign_id:str;metric_key:str;label:str;definition:str;unit:str
    def __post_init__(self):_schema(self)
@dataclass(frozen=True)
class ObservationColumns:
    id:str;campaign_id:str;metric_key:str;unit:str;value:str;start:str;end:str;observed_at:str;coverage:str
    def __post_init__(self):_schema(self)
@dataclass(frozen=True)
class CampaignColumns:
    row_id:str;kind:str;head:HeadColumns;definition:DefinitionColumns;observation:ObservationColumns
    def __post_init__(self):
        _id(self.row_id);_id(self.kind)
        if self.row_id==self.kind:_deny()
        for schema,cls in ((self.head,HeadColumns),(self.definition,DefinitionColumns),(self.observation,ObservationColumns)):
            if type(schema) is not cls:_deny()
            schema.__post_init__()
            if {self.row_id,self.kind}&{getattr(schema,k) for k in schema.__dataclass_fields__}:_deny()
@dataclass(frozen=True)
class WorkbookCampaignReport:
    report:marketing.CampaignReport
    draft:marketing.EditableDraft
    source:CampaignSource
    source_fingerprint:str
    input_fingerprint:str
    workbook_fingerprint:str
    row_fingerprints:tuple[str,...]
    scope:str=field(default='declared_synthetic_campaign_count_sample',init=False)
    requires_review:bool=field(default=True,init=False)
    live_authorized:bool=field(default=False,init=False)
    report_delivery_authorized:bool=field(default=False,init=False)

def _source_data(source):
    source.__post_init__()
    return [source.business,source.account_scope,source.workbook_id,source.role.value,source.campaign_id,source.revision,_aware(source.valid_from).isoformat(),_aware(source.expires_at).isoformat(),source.revoked]

class TableCampaignReader:
    def __init__(self,*,driver,current_source,expected_source,sheet_id,columns,enabled=False):
        self._driver=driver;self._callback=current_source;self._source=expected_source;self._sheet=sheet_id;self._columns=columns;self._enabled=enabled is True
        m=getattr(driver,'snapshot',None);self._host=(driver,current_source,expected_source,sheet_id,columns,getattr(m,'__self__',None),getattr(m,'__func__',None))
    def _current(self,now,campaign_id):
        driver,callback,source,sheet,columns,owner,function=self._host
        def identity():
            m=getattr(driver,'snapshot',None)
            if self._driver is not driver or self._callback is not callback or self._source is not source or self._sheet is not sheet or self._columns is not columns or getattr(m,'__self__',None) is not owner or getattr(m,'__func__',None) is not function:_deny()
        identity()
        if type(driver) is not SQLiteTableDriver or not callable(callback) or type(source) is not CampaignSource or type(columns) is not CampaignColumns:_deny()
        data=_source_data(source);_id(sheet);_id(campaign_id);columns.__post_init__()
        current=callback()
        if type(current) is not CampaignSource or _source_data(current)!=data or campaign_id!=source.campaign_id or source.revoked or not _aware(source.valid_from)<=now<_aware(source.expires_at):_deny()
        identity()
        return _hash(['table-campaign-source:v1',data,sheet,columns.row_id,columns.kind,[[getattr(s,k) for k in s.__dataclass_fields__] for s in (columns.head,columns.definition,columns.observation)]])
    def read_campaign(self,*,campaign_id,now):
        if not self._enabled:_deny()
        try:
            now=_aware(now);proof=self._current(now,campaign_id);book=self._driver.snapshot()
            if self._current(now,campaign_id)!=proof or type(book) is not tables.Workbook or book.id!=self._source.workbook_id:_deny()
            book_hash=_book_fingerprint(book);sheets=[s for s in book.sheets if s.id==self._sheet]
            if len(sheets)!=1 or not 2<=len(sheets[0].rows)<=7:_deny()
            heads=[];definitions=[];observations=[];ids=set();fingerprints=[]
            for row in sheets[0].rows:
                cells={c.column:c.value for c in row.cells};technical=_id(cells.get(self._columns.row_id));kind=cells.get(self._columns.kind)
                if technical in ids or type(kind) is not str or kind not in ('head','definition','observation'):_deny()
                ids.add(technical);schema=getattr(self._columns,kind);names={getattr(schema,k) for k in schema.__dataclass_fields__}|{self._columns.row_id,self._columns.kind}
                if set(cells)!=names or len(row.cells)!=len(names):_deny()
                selected=tables.lookup_row(book,workbook_id=self._source.workbook_id,sheet_id=self._sheet,identity=tables.RowIdentity(self._columns.row_id,technical))
                if tables._row_data(selected)!=tables._row_data(row):_deny()
                data={k:cells[getattr(schema,k)] for k in schema.__dataclass_fields__}
                if _id(data['campaign_id'])!=campaign_id:_deny()
                fp=_hash(['table-campaign-row:v1',proof,book_hash,tables._row_data(row)]);fingerprints.append(fp)
                if kind=='head':heads.append((_instant(data['start']),_instant(data['end'])))
                elif kind=='definition':
                    if data['unit']!='count' or type(data['unit']) is not str:_deny()
                    definitions.append(marketing.MetricDefinition(_id(data['metric_key']),_text(data['label'],80),_text(data['definition'],300),data['unit']))
                else:
                    if data['unit']!='count' or type(data['unit']) is not str:_deny()
                    observations.append(marketing.MetricObservation(_id(data['id']),campaign_id,_id(data['metric_key']),data['unit'],_count(data['value']),_instant(data['start']),_instant(data['end']),_instant(data['observed_at']),data['coverage'],'table-campaign:'+fp))
            if len(heads)!=1 or not 1<=len(definitions)<=3 or len(observations)>3 or heads[0][1]-heads[0][0]!=timedelta(days=7):_deny()
            campaign=marketing.Campaign(campaign_id,*heads[0],tuple(definitions))
            report=marketing.campaign_report(campaign,tuple(observations),now=now)
            self._report(report,campaign,tuple(observations),now)
            draft=marketing.render_campaign_report(report)
            refs=tuple(sorted(o.source_id for o in observations))
            if type(draft) is not marketing.EditableDraft or type(draft.text) is not str or not draft.text or len(draft.text.encode('utf-16-le'))//2>3500 or len(draft.text.encode())>8000 or draft.source_ids!=refs or draft.review_required is not True or draft.live_authorized is not False:_deny()
            if self._current(now,campaign_id)!=proof or _book_fingerprint(self._driver.snapshot())!=book_hash or self._current(now,campaign_id)!=proof:_deny()
            return WorkbookCampaignReport(report,draft,self._source,proof,_hash(['table-campaign-input:v1',campaign_id,now.isoformat()]),book_hash,tuple(fingerprints))
        except CampaignReaderUnavailable:raise
        except Exception:raise CampaignReaderUnavailable() from None
    def _report(self,report,campaign,observations,now):
        if type(report) is not marketing.CampaignReport or report.campaign is not campaign or report.as_of!=now or report.period_state not in ('before_start','in_progress','closed') or type(report.rows) is not tuple or len(report.rows)!=len(campaign.metrics) or report.review_required is not True or report.live_authorized is not False:_deny()
        known={o.metric_key:o for o in observations}
        for row,definition in zip(report.rows,campaign.metrics):
            if type(row) is not marketing.MetricRow or row.definition is not definition:_deny()
            observation=known.get(definition.key)
            if observation is None:
                if row.value is not None or row.coverage!='missing' or row.source_id is not None or row.observed_at is not None:_deny()
            elif type(row.value) is not Decimal or row.value.as_tuple()!=observation.value.as_tuple() or row.coverage!=observation.coverage or row.source_id!=observation.source_id or row.observed_at!=observation.observed_at:_deny()
