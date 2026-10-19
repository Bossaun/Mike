"""Disabled readonly declared transaction scenarios; no launch/finance authority."""
from dataclasses import dataclass,field
from datetime import datetime,timezone
from decimal import Decimal
import hashlib
import json
import re

from .table_sqlite_driver import SQLiteTableDriver
from . import table_change_plan as tables
from . import finance_metrics as finance

ONE_TRANSACTION_SAME_VOLUME='ONE_TRANSACTION_SAME_VOLUME'
_NUMBERS=('original_revenue','cogs','discount_pct','fee_pct','fixed_fee')


class PromotionScenarioUnavailable(ValueError):
    def __init__(self):super().__init__('table_promotion_scenario_unavailable')


def _deny():raise PromotionScenarioUnavailable()


def _id(value):
    if (type(value) is not str or not 0<len(value)<=128 or value!=value.strip() or
            len(value.encode())>512 or any(ord(c)<32 or ord(c)==127 for c in value)):_deny()
    return value


def _hash(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,
        separators=(',',':'),allow_nan=False).encode()).hexdigest()


def _aware(value):
    if (type(value) is not datetime or value.utcoffset() is None or
            value.astimezone(timezone.utc).astimezone(value.tzinfo).replace(tzinfo=None)!=value.replace(tzinfo=None)):_deny()
    return value.astimezone(timezone.utc)


def _currency(value):
    if value is not None and (type(value) is not str or re.fullmatch('[A-Z]{3}',value) is None):_deny()
    return value


def _number(value,percentage=False):
    if value is None:return None
    if (type(value) is not Decimal or not value.is_finite() or len(value.as_tuple().digits)>50 or
            abs(value.as_tuple().exponent)>18 or value<0 or len(str(value))>=80):_deny()
    if percentage and value>Decimal(100):_deny()
    return value


@dataclass(frozen=True)
class PromotionSource:
    business:str
    owner_id:str
    scenario_scope:str
    workbook_id:str
    revision:int
    valid_from:datetime
    expires_at:datetime
    revoked:bool=False

    def __post_init__(self):
        for value in (self.business,self.owner_id,self.scenario_scope,self.workbook_id):_id(value)
        if (re.fullmatch(r'sqlite-book:[0-9a-f]{64}',self.workbook_id) is None or
                type(self.revision) is not int or not 1<=self.revision<2**53 or type(self.revoked) is not bool or
                _aware(self.valid_from)>=_aware(self.expires_at)):_deny()


@dataclass(frozen=True)
class ScenarioBinding:
    scenario_id:str
    identity:tables.RowIdentity

    def __post_init__(self):
        _id(self.scenario_id)
        if type(self.identity) is not tables.RowIdentity:_deny()
        self.identity.__post_init__();_id(self.identity.column)
        if self.identity.value!=self.scenario_id:_deny()


@dataclass(frozen=True)
class PromotionColumns:
    scenario_id:str
    semantic_scope:str
    currency:str
    original_revenue:str
    cogs:str
    discount_pct:str
    fee_pct:str
    fixed_fee:str

    def __post_init__(self):
        names=tuple(getattr(self,key) for key in self.__dataclass_fields__)
        for name in names:_id(name)
        if len(names)!=len(set(names)):_deny()


@dataclass(frozen=True)
class PromotionScenario:
    scenario_id:str
    currency:str|None
    inputs:tuple[tuple[str,Decimal|None],...]
    result:finance.PromotionPreview|None
    missing:tuple[str,...]
    source_id:str
    row_fingerprint:str
    semantic_scope:str=field(default=ONE_TRANSACTION_SAME_VOLUME,init=False)


@dataclass(frozen=True)
class WorkbookPromotionScenarios:
    scenarios:tuple[PromotionScenario,...]
    source:PromotionSource
    expected_currency:str|None
    as_of:datetime
    source_fingerprint:str
    input_fingerprint:str
    workbook_fingerprint:str
    source_ids:tuple[str,...]
    scope:str=field(default='declared_transaction_scenario_sample',init=False)
    requires_review:bool=field(default=True,init=False)
    live_authorized:bool=field(default=False,init=False)
    accounting_facts_verified:bool=field(default=False,init=False)
    launch_authorized:bool=field(default=False,init=False)


def _source_data(source):
    source.__post_init__()
    return [source.business,source.owner_id,source.scenario_scope,source.workbook_id,source.revision,
            _aware(source.valid_from).isoformat(),_aware(source.expires_at).isoformat(),source.revoked]


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
    return _hash(['table-promotion-whole-book:v1',book.id,book.revision,data])


def _result(value,inputs,currency):
    if type(value) is not finance.PromotionPreview or type(value.currency) is not str or value.currency!=currency:_deny()
    for key in ('original_revenue','discounted_revenue','discount_amount','cogs','fee_amount',
                'contribution','contribution_margin_pct'):
        number=getattr(value,key)
        if key=='contribution_margin_pct' and number is None:continue
        if (type(number) is not Decimal or not number.is_finite() or len(number.as_tuple().digits)>100 or
                abs(number.as_tuple().exponent)>256):_deny()
        if key not in ('contribution','contribution_margin_pct') and number<0:_deny()
    if value.original_revenue.as_tuple()!=inputs['original_revenue'].as_tuple() or value.cogs.as_tuple()!=inputs['cogs'].as_tuple():_deny()
    return value


class TablePromotionScenarioReader:
    def __init__(self,*,driver,current_source,expected_source,business,owner_id,sheet_id,bindings,columns,enabled=False):
        self._driver=driver;self._callback=current_source;self._source=expected_source;self._business=business
        self._owner=owner_id;self._sheet=sheet_id;self._bindings=bindings;self._columns=columns;self._enabled=enabled is True
        method=getattr(driver,'snapshot',None)
        self._host=(driver,current_source,expected_source,business,owner_id,sheet_id,bindings,columns,
                    self._enabled,getattr(method,'__self__',None),getattr(method,'__func__',None))

    def _current(self,now):
        driver,callback,source,business,owner,sheet,bindings,columns,enabled,method_owner,function=self._host
        def identity():
            method=getattr(driver,'snapshot',None)
            if (self._driver is not driver or self._callback is not callback or self._source is not source or
                    self._business is not business or self._owner is not owner or self._sheet is not sheet or
                    self._bindings is not bindings or self._columns is not columns or self._enabled is not enabled or
                    getattr(method,'__self__',None) is not method_owner or getattr(method,'__func__',None) is not function):_deny()
        identity()
        if type(driver) is not SQLiteTableDriver or not callable(callback) or type(source) is not PromotionSource or type(columns) is not PromotionColumns:_deny()
        data=_source_data(source);_id(business);_id(owner);_id(sheet);columns.__post_init__()
        if source.business!=business or source.owner_id!=owner:_deny()
        if type(bindings) is not tuple or not 1<=len(bindings)<=3 or any(type(row) is not ScenarioBinding for row in bindings):_deny()
        for row in bindings:
            row.__post_init__()
            if row.identity.column!=columns.scenario_id:_deny()
        if len({row.scenario_id for row in bindings})!=len(bindings):_deny()
        current=callback()
        if type(current) is not PromotionSource or _source_data(current)!=data or source.revoked or not _aware(source.valid_from)<=now<_aware(source.expires_at):_deny()
        identity()
        return _hash(['table-promotion-source:v1',data,business,owner,sheet,
            [[row.scenario_id,row.identity.column,row.identity.value] for row in bindings],
            [getattr(columns,key) for key in columns.__dataclass_fields__]])

    def read_scenarios(self,*,currency,now):
        if not self._enabled:_deny()
        try:
            now=_aware(now);proof=self._current(now);currency=_currency(currency)
            book=self._driver.snapshot()
            if self._current(now)!=proof or type(book) is not tables.Workbook or book.id!=self._source.workbook_id:_deny()
            book_hash=_book_fingerprint(book);plans=[];positions=set();columns=self._columns
            for binding in self._bindings:
                row=tables.lookup_row(book,workbook_id=self._source.workbook_id,sheet_id=self._sheet,identity=binding.identity)
                if row.position in positions:_deny()
                positions.add(row.position);cells={cell.column:cell.value for cell in row.cells}
                names=tuple(getattr(columns,key) for key in columns.__dataclass_fields__)
                if len(row.cells)!=len(names) or set(cells)!=set(names):_deny()
                data={key:cells[getattr(columns,key)] for key in columns.__dataclass_fields__}
                if data['scenario_id']!=binding.scenario_id or type(data['semantic_scope']) is not str or data['semantic_scope']!=ONE_TRANSACTION_SAME_VOLUME:_deny()
                row_currency=_currency(data['currency'])
                if row_currency is not None and row_currency!=currency:_deny()
                inputs={key:_number(data[key],key in ('discount_pct','fee_pct')) for key in _NUMBERS}
                missing=tuple(key for key in ('currency',*_NUMBERS) if (row_currency if key=='currency' else inputs[key]) is None)
                fingerprint=_hash(['table-promotion-row:v1',proof,book_hash,tables._row_data(row)])
                plans.append((binding.scenario_id,row_currency,inputs,missing,fingerprint))
            output=[]
            for scenario_id,row_currency,inputs,missing,fingerprint in plans:
                value=None if missing else _result(finance.preview_promotion(currency=row_currency,**inputs),inputs,row_currency)
                output.append(PromotionScenario(scenario_id,row_currency,tuple(inputs.items()),value,missing,
                    'table-promotion:'+fingerprint,fingerprint))
            if self._current(now)!=proof or _book_fingerprint(self._driver.snapshot())!=book_hash or self._current(now)!=proof:_deny()
            return WorkbookPromotionScenarios(tuple(output),self._source,currency,now,proof,
                _hash(['table-promotion-input:v1',currency,now.isoformat()]),book_hash,tuple(row.source_id for row in output))
        except PromotionScenarioUnavailable:raise
        except Exception:raise PromotionScenarioUnavailable() from None
