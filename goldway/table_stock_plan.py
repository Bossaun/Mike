"""Disabled durable FREE/SALES/PLAN sample; after stock is hypothetical only."""
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
from . import inventory_metrics as inventory


class StockPlanUnavailable(ValueError):
    def __init__(self):super().__init__('table_stock_plan_unavailable')


def _deny():raise StockPlanUnavailable()

def _id(value,*,maximum=128,bytes_max=512):
    if type(value) is not str or not 0<len(value)<=maximum or value!=value.strip() or len(value.encode())>bytes_max or any(ord(c)<32 or ord(c)==127 for c in value):_deny()
    return value

def _hash(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def _aware(value):
    if type(value) is not datetime or value.utcoffset() is None or value.astimezone(timezone.utc).astimezone(value.tzinfo).replace(tzinfo=None)!=value.replace(tzinfo=None):_deny()
    return value.astimezone(timezone.utc)

def _date_cell(value):
    if type(value) is not str or len(value)!=10 or date.fromisoformat(value).isoformat()!=value:_deny()
    return date.fromisoformat(value)

def _quantity(value):
    if value is None:return inventory.UNKNOWN
    if type(value) is int:
        if not 0<=value<=10**18:_deny()
        value=Decimal(value)
    if type(value) is not Decimal:_deny()
    inventory._quantity(value,'units');return value

def _dimension(value):return inventory.UNKNOWN if value is None else _id(value)


class SourceRole(Enum):STOCK_PLAN_SAMPLE='stock_plan_sample'


@dataclass(frozen=True)
class StockPlanSource:
    business:str
    warehouse_source_scope:str
    workbook_id:str
    role:SourceRole
    revision:int
    timezone_name:str
    valid_from:datetime
    expires_at:datetime
    revoked:bool=False
    def __post_init__(self):
        for value in (self.business,self.warehouse_source_scope,self.workbook_id,self.timezone_name):_id(value)
        if re.fullmatch(r'sqlite-book:[0-9a-f]{64}',self.workbook_id) is None or self.role is not SourceRole.STOCK_PLAN_SAMPLE or type(self.revision) is not int or not 1<=self.revision<2**53 or type(self.revoked) is not bool:_deny()
        ZoneInfo(self.timezone_name)
        if _aware(self.valid_from)>=_aware(self.expires_at):_deny()


@dataclass(frozen=True)
class StockPlanColumns:
    record_id:str
    role:str
    sku:str
    variant:str
    location:str
    batch:str
    unit:str
    observed_on:str
    units:str
    days:str
    def __post_init__(self):
        values=tuple(getattr(self,key) for key in self.__dataclass_fields__)
        for value in values:_id(value)
        if len(values)!=len(set(values)):_deny()


@dataclass(frozen=True)
class StockPlanLine:
    key:inventory.StockKey
    preview:inventory.ProcurementPreview
    hypothetical_after:inventory.StockSnapshot
    before:inventory.Coverage
    after:inventory.Coverage
    before_flags:inventory.StockFlags|None
    after_flags:inventory.StockFlags|None
    hypothetical:bool=field(default=True,init=False)


@dataclass(frozen=True)
class WorkbookStockPlan:
    lines:tuple[StockPlanLine,...]
    source:StockPlanSource
    observed_on:date
    as_of:datetime
    month_days:Decimal
    thresholds:inventory.Thresholds|None
    source_fingerprint:str
    input_fingerprint:str
    workbook_fingerprint:str
    observation_fingerprints:tuple[str,...]
    scope:str=field(default='declared_synthetic_stock_plan_sample',init=False)
    requires_review:bool=field(default=True,init=False)
    live_authorized:bool=field(default=False,init=False)
    warehouse_activation:bool=field(default=False,init=False)
    purchase_authorized:bool=field(default=False,init=False)


def _source_data(source):
    source.__post_init__()
    return [source.business,source.warehouse_source_scope,source.workbook_id,source.role.value,source.revision,source.timezone_name,_aware(source.valid_from).isoformat(),_aware(source.expires_at).isoformat(),source.revoked]


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
    return _hash(['table-stock-plan-full-workbook:v1',book.id,book.revision,data])


def _coverage(result):
    if type(result) is not inventory.Coverage or type(result.state) is not inventory.CoverageState or type(result.reasons) is not tuple or len(result.reasons)>3 or len(set(result.reasons))!=len(result.reasons) or not set(result.reasons)<={'stock','sales','scope','zero_stock_and_zero_sales'}:_deny()
    if result.state is inventory.CoverageState.FINITE:
        if type(result.months) is not Decimal or not result.months.is_finite() or result.months<0 or len(result.months.as_tuple().digits)>28 or abs(result.months.as_tuple().exponent)>256 or result.reasons:_deny()
    elif result.months is not None or (result.state is inventory.CoverageState.INFINITE and result.reasons):_deny()
    return result


def _flags(result):
    if type(result) is not inventory.StockFlags:_deny()
    _coverage(result.coverage)
    for value in (result.low_stock,result.overstock,result.no_sales):
        if value is not inventory.UNKNOWN and type(value) is not bool:_deny()
    return result


class TableStockPlanReader:
    def __init__(self,*,driver,current_source,expected_source,sheet_id,columns,enabled=False):
        self._driver=driver;self._callback=current_source;self._source=expected_source;self._sheet=sheet_id;self._columns=columns;self._enabled=enabled is True
        method=getattr(driver,'snapshot',None);self._host=(driver,current_source,expected_source,sheet_id,columns,getattr(method,'__self__',None),getattr(method,'__func__',None))

    def _current(self,now):
        driver,callback,source,sheet,columns,owner,function=self._host
        def identity():
            method=getattr(driver,'snapshot',None)
            if getattr(method,'__self__',None) is not owner or getattr(method,'__func__',None) is not function:_deny()
        if self._driver is not driver or type(driver) is not SQLiteTableDriver or self._callback is not callback or not callable(callback) or self._source is not source or type(source) is not StockPlanSource or self._sheet is not sheet or self._columns is not columns or type(columns) is not StockPlanColumns:_deny()
        identity();data=_source_data(source);_id(sheet);columns.__post_init__();current=callback()
        if type(current) is not StockPlanSource or _source_data(current)!=data or source.revoked or not _aware(source.valid_from)<=now<_aware(source.expires_at):_deny()
        identity()
        if self._driver is not driver or self._callback is not callback or self._source is not source or self._sheet is not sheet or self._columns is not columns:_deny()
        return _hash(['table-stock-plan-source:v1',data,sheet,[getattr(columns,key) for key in columns.__dataclass_fields__]])

    def _input(self,observed_on,month_days,thresholds,now):
        if type(observed_on) is not date or observed_on>now.astimezone(ZoneInfo(self._source.timezone_name)).date() or type(month_days) is not Decimal:_deny()
        inventory._quantity(month_days,'month_days')
        if not Decimal(0)<month_days<=Decimal(36600):_deny()
        values=None
        if thresholds is not None:
            if type(thresholds) is not inventory.Thresholds or type(thresholds.low_months) is not Decimal or type(thresholds.overstock_months) is not Decimal:_deny()
            thresholds.__post_init__();values=[tables._value(thresholds.low_months),tables._value(thresholds.overstock_months),thresholds.no_sales_days]
        return _hash(['table-stock-plan-input:v1',observed_on.isoformat(),tables._value(month_days),values,now.isoformat()])

    def read_plan(self,*,observed_on,month_days,thresholds,now):
        if not self._enabled:_deny()
        try:
            now=_aware(now);source_hash=self._current(now);input_hash=self._input(observed_on,month_days,thresholds,now);book=self._driver.snapshot()
            if self._current(now)!=source_hash or self._input(observed_on,month_days,thresholds,now)!=input_hash or type(book) is not tables.Workbook or book.id!=self._source.workbook_id:_deny()
            book_hash=_book_fingerprint(book);sheets=[sheet for sheet in book.sheets if sheet.id==self._sheet]
            if len(sheets)!=1 or not 3<=len(sheets[0].rows)<=9:_deny()
            names=tuple(getattr(self._columns,key) for key in self._columns.__dataclass_fields__);groups={};ids=set();proofs=[]
            for selected in sheets[0].rows:
                if any(sum(cell.column==name for cell in selected.cells)!=1 for name in names):_deny()
                cells={cell.column:cell.value for cell in selected.cells};identifier=_id(cells[self._columns.record_id])
                if identifier in ids:_deny()
                ids.add(identifier);row=tables.lookup_row(book,workbook_id=self._source.workbook_id,sheet_id=self._sheet,identity=tables.RowIdentity(self._columns.record_id,identifier))
                if tables._row_data(row)!=tables._row_data(selected):_deny()
                role=cells[self._columns.role]
                if type(role) is not str or role not in ('free','sales','plan') or _date_cell(cells[self._columns.observed_on])!=observed_on:_deny()
                key=inventory.StockKey(inventory.ItemKey(_id(cells[self._columns.sku]),_dimension(cells[self._columns.variant])),_dimension(cells[self._columns.location]),_dimension(cells[self._columns.batch]))
                unit=_id(cells[self._columns.unit],maximum=40,bytes_max=160);units=_quantity(cells[self._columns.units]);days=cells[self._columns.days]
                provenance=_hash(['table-stock-plan-row:v1',source_hash,input_hash,book_hash,tables._row_data(row)]);proofs.append(provenance)
                if role=='sales':
                    inventory._days(days,'sales days');observation=inventory.SalesWindow(key,units,days,unit,observed_on,'table-stock-plan:'+provenance)
                else:
                    if days is not None:_deny()
                    observation=inventory.StockSnapshot(key,units,unit,observed_on,'table-stock-plan:'+provenance,inventory.StockKind.FREE) if role=='free' else inventory.PurchasePlan(key,units,unit,observed_on,'table-stock-plan:'+provenance)
                group=groups.setdefault(key,{})
                if role in group:_deny()
                group[role]=observation
            if not 1<=len(groups)<=3 or any(set(group)!= {'free','sales','plan'} for group in groups.values()):_deny()
            lines=[]
            for key,group in groups.items():
                stock,sales,plan=group['free'],group['sales'],group['plan']
                preview=inventory.procurement_preview(stock,plan)
                if type(preview) is not inventory.ProcurementPreview or preview.stock is not stock or preview.plan is not plan:_deny()
                expected=preview.expected_free_units
                if expected is not inventory.UNKNOWN and type(expected) is not Decimal:_deny()
                inventory._quantity(expected,'expected units')
                after=inventory.StockSnapshot(key,expected,stock.unit,observed_on,'hypothetical-stock-plan:'+_hash([source_hash,input_hash,book_hash,stock.source_id,plan.source_id]),inventory.StockKind.FREE)
                if thresholds is None:
                    before_coverage=_coverage(inventory.coverage(stock,sales,month_days=month_days));after_coverage=_coverage(inventory.coverage(after,sales,month_days=month_days));before_flags=after_flags=None
                else:
                    before_flags=_flags(inventory.stock_flags(stock,sales,month_days=month_days,thresholds=thresholds));after_flags=_flags(inventory.stock_flags(after,sales,month_days=month_days,thresholds=thresholds));before_coverage=before_flags.coverage;after_coverage=after_flags.coverage
                lines.append(StockPlanLine(key,preview,after,before_coverage,after_coverage,before_flags,after_flags))
            if self._current(now)!=source_hash or self._input(observed_on,month_days,thresholds,now)!=input_hash:_deny()
            if _book_fingerprint(self._driver.snapshot())!=book_hash or self._current(now)!=source_hash or self._input(observed_on,month_days,thresholds,now)!=input_hash:_deny()
            return WorkbookStockPlan(tuple(lines),self._source,observed_on,now,month_days,thresholds,source_hash,input_hash,book_hash,tuple(proofs))
        except StockPlanUnavailable:raise
        except Exception:raise StockPlanUnavailable() from None
