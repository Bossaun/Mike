"""Default-disabled synthetic workbook KPI subset; no SQL, authority or write API."""
from dataclasses import dataclass,field
from datetime import datetime,date,timezone
from decimal import Decimal
import hashlib
import json
import re
from zoneinfo import ZoneInfo
from .table_sqlite_driver import SQLiteTableDriver
from . import table_change_plan as tables
from . import operational_reports as reports


class TableKpiUnavailable(ValueError):
    def __init__(self):super().__init__('table_kpi_unavailable')


def _deny():raise TableKpiUnavailable()

def _id(value,*,spaces=False):
    if type(value) is not str or not 0<len(value)<=128 or not value.strip() or (not spaces and value!=value.strip()) or len(value.encode())>512 or any(ord(c)<32 or ord(c)==127 for c in value):_deny()
    return value

def _hash(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def _aware(value):
    reports._aware(value)
    return value.astimezone(timezone.utc)

def _number(value,*,allow_int=False):
    if allow_int and type(value) is int:
        if not 0<=value<=10**18:_deny()
        value=Decimal(value)
    reports._decimal(value)
    if value<0:_deny()
    return value

def _date_cell(value):
    if type(value) is not str or date.fromisoformat(value).isoformat()!=value:_deny()
    return date.fromisoformat(value)


@dataclass(frozen=True)
class KpiSource:
    business:str
    workbook_id:str
    owner_id:str
    timezone_name:str
    revision:int
    valid_from:datetime
    expires_at:datetime
    revoked:bool=False

    def __post_init__(self):
        _id(self.business);_id(self.workbook_id);reports._id(self.owner_id);_id(self.owner_id);_id(self.timezone_name)
        if re.fullmatch(r'sqlite-book:[0-9a-f]{64}',self.workbook_id) is None or type(self.revision) is not int or not 1<=self.revision<2**53 or type(self.revoked) is not bool:_deny()
        ZoneInfo(self.timezone_name)
        if _aware(self.valid_from)>=_aware(self.expires_at):_deny()


@dataclass(frozen=True)
class KpiSourceProof:
    source:KpiSource
    fingerprint:str


@dataclass(frozen=True)
class MetricBinding:
    metric_id:str
    sheet_id:str
    identity:tables.RowIdentity

    def __post_init__(self):
        reports._id(self.metric_id);_id(self.metric_id);_id(self.sheet_id)
        if type(self.identity) is not tables.RowIdentity:_deny()
        self.identity.__post_init__();_id(self.identity.column);_id(self.identity.value,spaces=True)


@dataclass(frozen=True)
class ColumnSchema:
    owner:str
    period_start:str
    period_end:str
    value:str
    unit:str
    currency:str
    observed_on:str

    def __post_init__(self):
        values=tuple(getattr(self,key) for key in self.__dataclass_fields__)
        for value in values:_id(value)
        if len(set(values))!=7:_deny()


@dataclass(frozen=True)
class WorkbookKpiReport:
    lines:tuple[reports.MetricLine,...]
    source:KpiSource
    period:reports.Period
    as_of:datetime
    source_fingerprint:str
    workbook_fingerprint:str
    coverage_fingerprint:str
    observation_fingerprints:tuple[tuple[str,str],...]
    unobserved_metric_ids:tuple[str,...]
    scope:str=field(default='configured_synthetic_workbook_subset',init=False)
    requires_review:bool=field(default=True,init=False)
    live_authorized:bool=field(default=False,init=False)
    staff_assessment_authorized:bool=field(default=False,init=False)


def _source_data(source):
    source.__post_init__()
    return [source.business,source.workbook_id,source.owner_id,source.timezone_name,source.revision,_aware(source.valid_from).isoformat(),_aware(source.expires_at).isoformat(),source.revoked]


def _workbook_fingerprint(book):
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
    return _hash(['table-kpi-full-workbook:v1',book.id,book.revision,data])


class TableKpiReader:
    def __init__(self,*,driver,current_source,expected_source,bindings,columns,enabled=False):
        self._driver=driver;self._callback=current_source;self._source=expected_source;self._bindings=bindings;self._columns=columns;self._enabled=enabled is True
        method=getattr(driver,'snapshot',None)
        self._host=(driver,current_source,expected_source,bindings,columns,getattr(method,'__self__',None),getattr(method,'__func__',None))

    def _current(self,now):
        driver,callback,source,bindings,columns,owner,function=self._host
        method=getattr(driver,'snapshot',None)
        if self._driver is not driver or type(driver) is not SQLiteTableDriver or self._callback is not callback or not callable(callback) or self._source is not source or type(source) is not KpiSource or self._bindings is not bindings or self._columns is not columns or type(columns) is not ColumnSchema or getattr(method,'__self__',None) is not owner or getattr(method,'__func__',None) is not function:_deny()
        source_data=_source_data(source);columns.__post_init__()
        if type(bindings) is not tuple or not 1<=len(bindings)<=3 or any(type(row) is not MetricBinding for row in bindings):_deny()
        for binding in bindings:binding.__post_init__()
        if len({row.metric_id for row in bindings})!=len(bindings) or len({(row.sheet_id,row.identity.column,row.identity.value) for row in bindings})!=len(bindings):_deny()
        current=callback()
        if type(current) is not KpiSource or _source_data(current)!=source_data or source.revoked or not _aware(source.valid_from)<=now<_aware(source.expires_at):_deny()
        if self._driver is not driver or self._callback is not callback or self._source is not source or self._bindings is not bindings or self._columns is not columns or getattr(driver.snapshot,'__self__',None) is not owner or getattr(driver.snapshot,'__func__',None) is not function:_deny()
        return _hash(['table-kpi-source:v1',source_data,[[row.metric_id,row.sheet_id,row.identity.column,row.identity.value] for row in bindings],[getattr(columns,key) for key in columns.__dataclass_fields__]])

    def source_proof(self,*,now):
        """Bound-source metadata only; no SQL, snapshots or KPI cells."""
        if not self._enabled:_deny()
        try:
            now=_aware(now);source=self._source;data=_source_data(source)
            fingerprint=self._current(now)
            if self._source is not source or _source_data(source)!=data:_deny()
            return KpiSourceProof(source,fingerprint)
        except TableKpiUnavailable:raise
        except Exception:raise TableKpiUnavailable() from None

    def read_daily(self,*,period,definitions,coverage_complete,coverage_observed_on,now):
        if not self._enabled:_deny()
        try:
            now=_aware(now);fingerprint=self._current(now);source=self._source;bindings=self._bindings;columns=self._columns;local_day=now.astimezone(ZoneInfo(source.timezone_name)).date()
            if type(period) is not reports.Period:_deny()
            period.__post_init__()
            if (period.end-period.start).days!=1 or period.start>local_day or type(definitions) is not tuple or not 1<=len(definitions)<=3 or any(type(row) is not reports.MetricDefinition for row in definitions):_deny()
            for definition in definitions:
                definition.__post_init__()
                if definition.target is not None:_number(definition.target)
            if len({row.id for row in definitions})!=len(definitions) or {row.id for row in definitions}!={row.metric_id for row in bindings}:_deny()
            if type(coverage_complete) is not bool or type(coverage_observed_on) is not date or coverage_observed_on>local_day:_deny()
            book=self._driver.snapshot()
            if self._current(now)!=fingerprint or type(book) is not tables.Workbook or book.id!=source.workbook_id:_deny()
            book_digest=_workbook_fingerprint(book)
            coverage_digest=_hash(['table-kpi-coverage:v1',fingerprint,book_digest,period.start.isoformat(),period.end.isoformat(),coverage_complete,coverage_observed_on.isoformat()])
            coverage=reports.Coverage(period,coverage_complete,coverage_observed_on,'table-kpi:'+coverage_digest)
            by_id={definition.id:definition for definition in definitions};observations=[];unobserved=[];proofs=[];used_rows=set()
            for binding in bindings:
                row=tables.lookup_row(book,workbook_id=source.workbook_id,sheet_id=binding.sheet_id,identity=binding.identity)
                row_key=(binding.sheet_id,row.position)
                if row_key in used_rows:_deny()
                used_rows.add(row_key);cells={cell.column:cell.value for cell in row.cells}
                names=tuple(getattr(columns,key) for key in columns.__dataclass_fields__)
                if any(sum(cell.column==name for cell in row.cells)!=1 for name in names):_deny()
                definition=by_id[binding.metric_id]
                if type(cells[columns.owner]) is not str or cells[columns.owner]!=source.owner_id or _date_cell(cells[columns.period_start])!=period.start or _date_cell(cells[columns.period_end])!=period.end or type(cells[columns.unit]) is not str or cells[columns.unit]!=definition.unit or cells[columns.currency]!=definition.currency or (definition.currency is None and cells[columns.currency] is not None):_deny()
                value=cells[columns.value];observed=cells[columns.observed_on]
                if value is not None:value=_number(value,allow_int=True)
                if observed is None:
                    if value is not None:_deny()
                    unobserved.append(binding.metric_id)
                else:
                    observed=_date_cell(observed)
                    if observed>local_day:_deny()
                provenance=_hash(['table-kpi-observation:v1',fingerprint,book_digest,binding.metric_id,binding.sheet_id,tables._row_data(row),coverage_digest])
                proofs.append((binding.metric_id,provenance))
                if observed is not None:observations.append(reports.MetricObservation(source.owner_id,binding.metric_id,period,value,definition.unit,definition.currency,observed,'table-kpi:'+provenance))
            lines=reports.kpi_snapshot(definitions=definitions,owners=(source.owner_id,),observations=tuple(observations),coverage=coverage,require_daily=True)
            if type(lines) is not tuple or len(lines)!=len(definitions) or any(type(row) is not reports.MetricLine for row in lines):_deny()
            if {row.metric_id for row in lines}!={row.id for row in definitions} or any(row.owner!=source.owner_id for row in lines):_deny()
            if self._current(now)!=fingerprint:_deny()
            fresh=self._driver.snapshot()
            if _workbook_fingerprint(fresh)!=book_digest or self._current(now)!=fingerprint:_deny()
            return WorkbookKpiReport(lines,source,period,now,fingerprint,book_digest,coverage_digest,tuple(proofs),tuple(unobserved))
        except TableKpiUnavailable:raise
        except Exception:raise TableKpiUnavailable() from None
