"""Disabled durable declared sales sample; no complete-company accounting claim."""
from dataclasses import dataclass,field
from datetime import date,datetime,timedelta,timezone
from decimal import Decimal
from enum import Enum
import hashlib
import json
import re
from zoneinfo import ZoneInfo
from .table_sqlite_driver import SQLiteTableDriver
from . import table_change_plan as tables
from . import finance_metrics as finance


class SalesComparisonUnavailable(ValueError):
    def __init__(self):super().__init__('table_sales_comparison_unavailable')


def _deny():raise SalesComparisonUnavailable()

def _id(value):
    if type(value) is not str or not 0<len(value)<=128 or value!=value.strip() or len(value.encode())>512 or any(ord(c)<32 or ord(c)==127 for c in value):_deny()
    return value

def _hash(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def _aware(value):
    if type(value) is not datetime or value.utcoffset() is None or value.astimezone(timezone.utc).astimezone(value.tzinfo).replace(tzinfo=None)!=value.replace(tzinfo=None):_deny()
    return value.astimezone(timezone.utc)

def _date_cell(value):
    if type(value) is not str or len(value)!=10 or date.fromisoformat(value).isoformat()!=value:_deny()
    return date.fromisoformat(value)

def _amount(value,*,unknown=False):
    if unknown and value is None:return None
    if type(value) is int:
        if not 0<=value<=10**18:_deny()
        value=Decimal(value)
    if type(value) is not Decimal:_deny()
    return finance._amount(value)


class SourceRole(Enum):SALES_SAMPLE='sales_sample'


@dataclass(frozen=True)
class SalesSource:
    business:str
    account_scope:str
    workbook_id:str
    role:SourceRole
    revision:int
    timezone_name:str
    valid_from:datetime
    expires_at:datetime
    revoked:bool=False
    def __post_init__(self):
        for value in (self.business,self.account_scope,self.workbook_id,self.timezone_name):_id(value)
        if re.fullmatch(r'sqlite-book:[0-9a-f]{64}',self.workbook_id) is None or self.role is not SourceRole.SALES_SAMPLE or type(self.revision) is not int or not 1<=self.revision<2**53 or type(self.revoked) is not bool:_deny()
        ZoneInfo(self.timezone_name)
        if _aware(self.valid_from)>=_aware(self.expires_at):_deny()


@dataclass(frozen=True)
class SaleColumns:
    operation_id:str
    date:str
    currency:str
    revenue:str
    cogs:str
    barter:str
    transaction_kind:str
    def __post_init__(self):
        values=tuple(getattr(self,key) for key in self.__dataclass_fields__)
        for value in values:_id(value)
        if len(values)!=len(set(values)):_deny()


@dataclass(frozen=True)
class WorkbookSalesComparison:
    comparison:finance.WeekComparison
    flags:tuple[finance.CostFlag,...]
    source:SalesSource
    week_start:date
    currency:str
    as_of:datetime
    sample_count:int
    source_fingerprint:str
    input_fingerprint:str
    workbook_fingerprint:str
    observation_fingerprints:tuple[str,...]
    scope:str=field(default='declared_sales_sample',init=False)
    company_completeness:None=field(default=None,init=False)
    requires_review:bool=field(default=True,init=False)
    live_authorized:bool=field(default=False,init=False)


def _source_data(source):
    source.__post_init__()
    return [source.business,source.account_scope,source.workbook_id,source.role.value,source.revision,source.timezone_name,_aware(source.valid_from).isoformat(),_aware(source.expires_at).isoformat(),source.revoked]


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
    return _hash(['table-sales-full-workbook:v1',book.id,book.revision,data])


def _result_number(value,*,unknown=False,nonnegative=False):
    if value is None and unknown:return
    if type(value) is not Decimal or not value.is_finite() or len(value.as_tuple().digits)>100 or abs(value.as_tuple().exponent)>256 or (nonnegative and value<0):_deny()


def _results(comparison,flags,sales,week_start,currency):
    if type(comparison) is not finance.WeekComparison or type(flags) is not tuple or len(flags)>len(sales):_deny()
    for metric,start in ((comparison.current,week_start),(comparison.prior,week_start-timedelta(days=7))):
        if type(metric) is not finance.WeekMetrics or type(metric.start) is not date or type(metric.end) is not date or metric.start!=start or metric.end!=start+timedelta(days=7) or metric.currency!=currency:_deny()
        for name in ('sale_count','known_cost_count','unknown_cost_count','barter_count','unknown_barter_cost_count'):
            value=getattr(metric,name)
            if type(value) is not int or not 0<=value<=12:_deny()
        for name in ('revenue','known_cost_revenue','known_cogs','known_barter_cost'):_result_number(getattr(metric,name),nonnegative=True)
        for name in ('gross_profit','gross_margin_pct','cost_coverage'):_result_number(getattr(metric,name),unknown=True)
        if metric.cost_coverage is not None and not Decimal(0)<=metric.cost_coverage<=Decimal(1):_deny()
    _result_number(comparison.revenue_delta)
    for name in ('revenue_change_pct','known_scope_gross_profit_delta','gross_margin_change_pp'):_result_number(getattr(comparison,name),unknown=True)
    ids={row.id for row in sales};seen=set()
    for flag in flags:
        if type(flag) is not finance.CostFlag or flag.sale_id not in ids or flag.sale_id in seen or flag.kind not in ('missing_cost','under_cost'):_deny()
        seen.add(flag.sale_id)


class TableSalesComparisonReader:
    def __init__(self,*,driver,current_source,expected_source,sheet_id,columns,enabled=False):
        self._driver=driver;self._callback=current_source;self._source=expected_source;self._sheet=sheet_id;self._columns=columns;self._enabled=enabled is True
        method=getattr(driver,'snapshot',None);self._host=(driver,current_source,expected_source,sheet_id,columns,getattr(method,'__self__',None),getattr(method,'__func__',None))

    def _current(self,now):
        driver,callback,source,sheet,columns,owner,function=self._host
        def identity():
            method=getattr(driver,'snapshot',None)
            if getattr(method,'__self__',None) is not owner or getattr(method,'__func__',None) is not function:_deny()
        if self._driver is not driver or type(driver) is not SQLiteTableDriver or self._callback is not callback or not callable(callback) or self._source is not source or type(source) is not SalesSource or self._sheet is not sheet or self._columns is not columns or type(columns) is not SaleColumns:_deny()
        identity();data=_source_data(source);_id(sheet);columns.__post_init__();current=callback()
        if type(current) is not SalesSource or _source_data(current)!=data or source.revoked or not _aware(source.valid_from)<=now<_aware(source.expires_at):_deny()
        identity()
        if self._driver is not driver or self._callback is not callback or self._source is not source or self._sheet is not sheet or self._columns is not columns:_deny()
        return _hash(['table-sales-source:v1',data,sheet,[getattr(columns,key) for key in columns.__dataclass_fields__]])

    def read_weeks(self,*,week_start,currency,now):
        if not self._enabled:_deny()
        try:
            now=_aware(now);source_hash=self._current(now);finance._day(week_start);finance._currency(currency)
            if week_start.weekday()!=0:_deny()
            lower=week_start-timedelta(days=7);upper=week_start+timedelta(days=7);local_day=now.astimezone(ZoneInfo(self._source.timezone_name)).date()
            input_hash=_hash([week_start.isoformat(),currency,now.isoformat()]);book=self._driver.snapshot()
            if self._current(now)!=source_hash or type(book) is not tables.Workbook or book.id!=self._source.workbook_id:_deny()
            book_hash=_book_fingerprint(book);sheets=[sheet for sheet in book.sheets if sheet.id==self._sheet]
            if len(sheets)!=1 or len(sheets[0].rows)>12:_deny()
            names=tuple(getattr(self._columns,key) for key in self._columns.__dataclass_fields__);sales=[];ids=set();proofs=[]
            for selected in sheets[0].rows:
                if any(sum(cell.column==name for cell in selected.cells)!=1 for name in names):_deny()
                cells={cell.column:cell.value for cell in selected.cells};identifier=_id(cells[self._columns.operation_id])
                if identifier in ids:_deny()
                ids.add(identifier)
                row=tables.lookup_row(book,workbook_id=self._source.workbook_id,sheet_id=self._sheet,identity=tables.RowIdentity(self._columns.operation_id,identifier))
                if tables._row_data(row)!=tables._row_data(selected):_deny()
                observed=_date_cell(cells[self._columns.date]);row_currency=cells[self._columns.currency];finance._currency(row_currency)
                if not lower<=observed<upper or observed>local_day or row_currency!=currency or type(cells[self._columns.barter]) is not bool or cells[self._columns.transaction_kind]!='sale':_deny()
                sales.append(finance.Sale(identifier,observed,currency,_amount(cells[self._columns.revenue]),_amount(cells[self._columns.cogs],unknown=True),cells[self._columns.barter]))
                proofs.append(_hash(['table-sales-observation:v1',source_hash,input_hash,book_hash,tables._row_data(row)]))
            sales=tuple(sales)
            comparison=finance.compare_weeks(sales,week_start=week_start,currency=currency)
            flags=finance.cost_flags(sales,currency=currency)
            _results(comparison,flags,sales,week_start,currency)
            if self._current(now)!=source_hash:_deny()
            if _book_fingerprint(self._driver.snapshot())!=book_hash or self._current(now)!=source_hash:_deny()
            return WorkbookSalesComparison(comparison,flags,self._source,week_start,currency,now,len(sales),source_hash,input_hash,book_hash,tuple(proofs))
        except SalesComparisonUnavailable:raise
        except Exception:raise SalesComparisonUnavailable() from None
