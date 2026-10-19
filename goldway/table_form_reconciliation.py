"""Disabled durable synthetic FORM/ACCOUNT consumer; questions are unsent drafts."""
from dataclasses import dataclass,field
from datetime import date,datetime,timezone
from decimal import Decimal
from enum import Enum
import hashlib
import json
import re
from zoneinfo import ZoneInfo
from .table_sqlite_driver import SQLiteTableDriver
from . import table_change_plan as tables
from . import operational_reports as reports


class FormReconciliationUnavailable(ValueError):
    def __init__(self):super().__init__('table_form_reconciliation_unavailable')


def _deny():raise FormReconciliationUnavailable()

def _id(value,*,spaces=False):
    if type(value) is not str or not 0<len(value)<=128 or not value.strip() or (not spaces and value!=value.strip()) or len(value.encode())>512 or any(ord(c)<32 or ord(c)==127 for c in value):_deny()
    return value

def _hash(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def _aware(value):reports._aware(value);return value.astimezone(timezone.utc)

def _date_cell(value):
    if type(value) is not str or date.fromisoformat(value).isoformat()!=value:_deny()
    return date.fromisoformat(value)

def _value(value):
    if value is None:return None
    if type(value) is int:
        if abs(value)>10**18:_deny()
        value=Decimal(value)
    reports._decimal(value);return value

def _submission(value):
    if value is None:return None
    if type(value) is not str or len(value)>40 or re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})',value) is None:_deny()
    return _aware(datetime.fromisoformat(value))


class SourceRole(Enum):
    FORM='form'
    ACCOUNT='account'


@dataclass(frozen=True)
class FormSource:
    business:str
    workbook_id:str
    owner_id:str
    role:SourceRole
    revision:int
    timezone_name:str
    valid_from:datetime
    expires_at:datetime
    revoked:bool=False

    def __post_init__(self):
        for value in (self.business,self.workbook_id,self.owner_id,self.timezone_name):_id(value)
        reports._id(self.owner_id)
        if re.fullmatch(r'sqlite-book:[0-9a-f]{64}',self.workbook_id) is None or type(self.role) is not SourceRole or type(self.revision) is not int or not 1<=self.revision<2**53 or type(self.revoked) is not bool:_deny()
        ZoneInfo(self.timezone_name)
        if _aware(self.valid_from)>=_aware(self.expires_at):_deny()


@dataclass(frozen=True)
class SourcePair:
    form:FormSource
    account:FormSource

    def __post_init__(self):
        if type(self.form) is not FormSource or type(self.account) is not FormSource:_deny()
        self.form.__post_init__();self.account.__post_init__()
        if self.form.role is not SourceRole.FORM or self.account.role is not SourceRole.ACCOUNT or self.form.business!=self.account.business or self.form.owner_id!=self.account.owner_id or self.form.timezone_name!=self.account.timezone_name or self.form.workbook_id==self.account.workbook_id:_deny()


@dataclass(frozen=True)
class RowSelector:
    sheet_id:str
    identity:tables.RowIdentity

    def __post_init__(self):
        _id(self.sheet_id)
        if type(self.identity) is not tables.RowIdentity:_deny()
        self.identity.__post_init__();_id(self.identity.column);_id(self.identity.value,spaces=True)


@dataclass(frozen=True)
class MetricPair:
    metric_id:str
    form:RowSelector
    account:RowSelector

    def __post_init__(self):
        _id(self.metric_id);reports._id(self.metric_id)
        for selector in (self.form,self.account):
            if type(selector) is not RowSelector:_deny()
            selector.__post_init__()


def _columns(value):
    values=tuple(getattr(value,key) for key in value.__dataclass_fields__)
    for name in values:_id(name)
    if len(set(values))!=len(values):_deny()


@dataclass(frozen=True)
class AccountColumns:
    owner:str
    metric_id:str
    period_start:str
    period_end:str
    present:str
    value:str
    unit:str
    currency:str
    observed_on:str
    def __post_init__(self):_columns(self)


@dataclass(frozen=True)
class FormColumns:
    owner:str
    metric_id:str
    period_start:str
    period_end:str
    present:str
    value:str
    unit:str
    currency:str
    observed_on:str
    submitted_at:str
    def __post_init__(self):_columns(self)


@dataclass(frozen=True)
class WorkbookFormReport:
    lines:tuple[reports.FormLine,...]
    sources:SourcePair
    period:reports.Period
    as_of:datetime
    due_at:datetime
    source_fingerprint:str
    input_fingerprint:str
    form_fingerprint:str
    account_fingerprint:str
    observation_fingerprints:tuple[tuple[str,str],...]
    scope:str=field(default='declared_synthetic_form_account_subset',init=False)
    requires_review:bool=field(default=True,init=False)
    live_authorized:bool=field(default=False,init=False)
    collection_authorized:bool=field(default=False,init=False)


def _source_data(pair):
    pair.__post_init__()
    return [[row.business,row.workbook_id,row.owner_id,row.role.value,row.revision,row.timezone_name,_aware(row.valid_from).isoformat(),_aware(row.expires_at).isoformat(),row.revoked] for row in (pair.form,pair.account)]


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
    return _hash(['table-form-full-workbook:v1',book.id,book.revision,data])


class TableFormReconciler:
    def __init__(self,*,form_driver,account_driver,current_sources,expected_sources,bindings,form_columns,account_columns,enabled=False):
        self._drivers=(form_driver,account_driver);self._callback=current_sources;self._sources=expected_sources;self._bindings=bindings;self._columns=(form_columns,account_columns);self._enabled=enabled is True
        methods=tuple((getattr(getattr(driver,'snapshot',None),'__self__',None),getattr(getattr(driver,'snapshot',None),'__func__',None)) for driver in self._drivers)
        self._host=(self._drivers,current_sources,expected_sources,bindings,self._columns,methods)

    def _current(self,now):
        drivers,callback,sources,bindings,columns,methods=self._host
        if self._drivers is not drivers or drivers[0] is drivers[1] or any(type(driver) is not SQLiteTableDriver for driver in drivers) or self._callback is not callback or not callable(callback) or self._sources is not sources or type(sources) is not SourcePair or self._bindings is not bindings or self._columns is not columns or type(columns[0]) is not FormColumns or type(columns[1]) is not AccountColumns:_deny()
        def identities():
            for driver,expected in zip(drivers,methods):
                method=getattr(driver,'snapshot',None)
                if getattr(method,'__self__',None) is not expected[0] or getattr(method,'__func__',None) is not expected[1]:_deny()
        identities();data=_source_data(sources)
        for schema in columns:schema.__post_init__()
        if type(bindings) is not tuple or not 1<=len(bindings)<=3 or any(type(row) is not MetricPair for row in bindings):_deny()
        for row in bindings:row.__post_init__()
        if len({row.metric_id for row in bindings})!=len(bindings):_deny()
        for name in ('form','account'):
            keys=[(getattr(row,name).sheet_id,getattr(row,name).identity.column,getattr(row,name).identity.value) for row in bindings]
            if len(keys)!=len(set(keys)):_deny()
        current=callback()
        if type(current) is not SourcePair or _source_data(current)!=data:_deny()
        for source in (sources.form,sources.account):
            if source.revoked or not _aware(source.valid_from)<=now<_aware(source.expires_at):_deny()
        identities()
        if self._drivers is not drivers or self._callback is not callback or self._sources is not sources or self._bindings is not bindings or self._columns is not columns:_deny()
        return _hash(['table-form-source:v1',data,[[row.metric_id,[[selector.sheet_id,selector.identity.column,selector.identity.value] for selector in (row.form,row.account)]] for row in bindings],[[getattr(schema,key) for key in schema.__dataclass_fields__] for schema in columns]])

    def _input(self,period,definitions,monday,complete,observed,now):
        if type(period) is not reports.Period or type(monday) is not date or type(observed) is not date or type(complete) is not bool or type(definitions) is not tuple or not 1<=len(definitions)<=3 or any(type(row) is not reports.MetricDefinition for row in definitions):_deny()
        period.__post_init__()
        for row in definitions:
            row.__post_init__()
            if row.direction!='informational' or row.target is not None or len(row.label)>120:_deny()
        if len({row.id for row in definitions})!=len(definitions) or {row.id for row in definitions}!={row.metric_id for row in self._bindings}:_deny()
        local_day=now.astimezone(ZoneInfo(self._sources.form.timezone_name)).date()
        if observed>local_day or period.start>local_day:_deny()
        return _hash(['table-form-input:v1',period.start.isoformat(),period.end.isoformat(),[[row.id,row.label,row.unit,row.currency,row.direction,row.target] for row in definitions],monday.isoformat(),complete,observed.isoformat(),now.isoformat()])

    def _observations(self,book,source,schema,which,period,definitions,source_hash,book_hash,coverage_hash):
        rows=[];proofs=[];selected=set();names=tuple(getattr(schema,key) for key in schema.__dataclass_fields__)
        for binding in self._bindings:
            selector=getattr(binding,which)
            row=tables.lookup_row(book,workbook_id=source.workbook_id,sheet_id=selector.sheet_id,identity=selector.identity)
            key=(selector.sheet_id,row.position)
            if key in selected or any(sum(cell.column==name for cell in row.cells)!=1 for name in names):_deny()
            selected.add(key);cells={cell.column:cell.value for cell in row.cells};definition=definitions[binding.metric_id]
            if type(cells[schema.owner]) is not str or cells[schema.owner]!=source.owner_id or type(cells[schema.metric_id]) is not str or cells[schema.metric_id]!=binding.metric_id or _date_cell(cells[schema.period_start])!=period.start or _date_cell(cells[schema.period_end])!=period.end or type(cells[schema.unit]) is not str or cells[schema.unit]!=definition.unit or cells[schema.currency]!=definition.currency or (definition.currency is None and cells[schema.currency] is not None):_deny()
            present=cells[schema.present]
            if type(present) is not bool:_deny()
            value=cells[schema.value];observed=cells[schema.observed_on];submitted=cells[schema.submitted_at] if which=='form' else None
            provenance=_hash(['table-form-observation:v1',source_hash,book_hash,binding.metric_id,source.role.value,selector.sheet_id,tables._row_data(row),coverage_hash]);proofs.append((source.role.value,provenance))
            if not present:
                if value is not None or observed is not None or submitted is not None:_deny()
                continue
            value=_value(value);observed=_date_cell(observed)
            observation=reports.MetricObservation(source.owner_id,binding.metric_id,period,value,definition.unit,definition.currency,observed,'table-form:'+provenance)
            rows.append(reports.FormValue(observation,_submission(submitted)) if which=='form' else observation)
        return tuple(rows),tuple(proofs)

    def read_reconciliation(self,*,period,definitions,monday,coverage_complete,coverage_observed_on,now):
        if not self._enabled:_deny()
        try:
            now=_aware(now);source_hash=self._current(now);inputs=self._input(period,definitions,monday,coverage_complete,coverage_observed_on,now)
            due=reports.monday_form_cutoff(monday=monday,timezone_name=self._sources.form.timezone_name)
            cutoff_hash=_hash([inputs,due.isoformat()]);books=[];fingerprints=[]
            for driver,source in zip(self._drivers,(self._sources.form,self._sources.account)):
                book=driver.snapshot()
                if self._current(now)!=source_hash or self._input(period,definitions,monday,coverage_complete,coverage_observed_on,now)!=inputs or type(book) is not tables.Workbook or book.id!=source.workbook_id:_deny()
                books.append(book);fingerprints.append(_book_fingerprint(book))
            coverage_hash=_hash(['table-form-coverage:v1',source_hash,cutoff_hash,fingerprints]);coverage=reports.Coverage(period,coverage_complete,coverage_observed_on,'table-form:'+coverage_hash)
            by_id={row.id:row for row in definitions}
            forms,form_proofs=self._observations(books[0],self._sources.form,self._columns[0],'form',period,by_id,source_hash,fingerprints[0],coverage_hash)
            accounts,account_proofs=self._observations(books[1],self._sources.account,self._columns[1],'account',period,by_id,source_hash,fingerprints[1],coverage_hash)
            if any(row.observation.observed_on>now.astimezone(due.tzinfo).date() for row in forms) or any(row.observed_on>now.astimezone(due.tzinfo).date() for row in accounts):_deny()
            lines=reports.reconcile_forms(definitions=definitions,owners=(self._sources.form.owner_id,),forms=forms,account=accounts,coverage=coverage,due_at=due,as_of=now)
            if type(lines) is not tuple or len(lines)!=len(definitions) or any(type(row) is not reports.FormLine for row in lines) or len({row.metric_id for row in lines})!=len(lines) or {row.metric_id for row in lines}!={row.id for row in definitions}:_deny()
            expected_sources={definition.id:('table-form:'+coverage_hash,)+tuple(row.observation.source_id for row in forms if row.observation.metric_id==definition.id)+tuple(row.source_id for row in accounts if row.metric_id==definition.id) for definition in definitions}
            for row in lines:
                if row.owner!=self._sources.form.owner_id or row.comparison not in ('provisional','missing_form','missing_account','unknown','match','discrepancy') or row.submission_state not in ('pending','missing_after_cutoff','unknown_time','on_time','late'):_deny()
                if type(row.sources) is not tuple or not 1<=len(row.sources)<=3 or len(set(row.sources))!=len(row.sources) or row.sources!=expected_sources[row.metric_id]:_deny()
                for value in (row.reported,row.accounted,row.difference):
                    if value is not None and (type(value) is not Decimal or not value.is_finite()):_deny()
                if row.question is not None and (type(row.question) is not str or not 0<len(row.question)<=500 or any(ord(c)<32 or ord(c)==127 for c in row.question)):_deny()
            if self._current(now)!=source_hash or self._input(period,definitions,monday,coverage_complete,coverage_observed_on,now)!=inputs:_deny()
            for driver,expected in zip(self._drivers,fingerprints):
                if _book_fingerprint(driver.snapshot())!=expected or self._current(now)!=source_hash or self._input(period,definitions,monday,coverage_complete,coverage_observed_on,now)!=inputs:_deny()
            return WorkbookFormReport(lines,self._sources,period,now,due,source_hash,cutoff_hash,fingerprints[0],fingerprints[1],form_proofs+account_proofs)
        except FormReconciliationUnavailable:raise
        except Exception:raise FormReconciliationUnavailable() from None
