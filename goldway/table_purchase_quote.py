"""Disabled durable supplied weight/tariff quote; no offer, advice or purchase right."""
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
from . import procurement_metrics as procurement


def _deny():raise PurchaseQuoteUnavailable()


class PurchaseQuoteUnavailable(ValueError):
    def __init__(self):super().__init__('table_purchase_quote_unavailable')


def _id(value,*,spaces=False):
    if type(value) is not str or not 0<len(value)<=128 or not value.strip() or (not spaces and value!=value.strip()) or len(value.encode())>512 or any(ord(c)<32 or ord(c)==127 for c in value):_deny()
    return value

def _hash(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def _aware(value):
    if type(value) is not datetime or value.utcoffset() is None:_deny()
    if value.astimezone(timezone.utc).astimezone(value.tzinfo).replace(tzinfo=None)!=value.replace(tzinfo=None):_deny()
    return value.astimezone(timezone.utc)

def _date_cell(value):
    if type(value) is not str or date.fromisoformat(value).isoformat()!=value:_deny()
    return date.fromisoformat(value)

def _amount(value,*,unknown=True,positive=False):
    if value is None:
        if not unknown:_deny()
        return None
    if type(value) is int:
        if not 0<=value<=10**18:_deny()
        value=Decimal(value)
    if type(value) is not Decimal:_deny()
    return procurement._decimal(value,positive=positive)


def _closed(value,keys):
    if type(value) is not dict or set(value)!=set(keys):_deny()
    return value


class SourceRole(Enum):PURCHASE_QUOTE='purchase_quote'


@dataclass(frozen=True)
class QuoteSource:
    business:str
    workbook_id:str
    role:SourceRole
    revision:int
    timezone_name:str
    valid_from:datetime
    expires_at:datetime
    revoked:bool=False
    def __post_init__(self):
        for value in (self.business,self.workbook_id,self.timezone_name):_id(value)
        if re.fullmatch(r'sqlite-book:[0-9a-f]{64}',self.workbook_id) is None or self.role is not SourceRole.PURCHASE_QUOTE or type(self.revision) is not int or not 1<=self.revision<2**53 or type(self.revoked) is not bool:_deny()
        ZoneInfo(self.timezone_name)
        if _aware(self.valid_from)>=_aware(self.expires_at):_deny()


@dataclass(frozen=True)
class RowBinding:
    sheet_id:str
    identity:tables.RowIdentity
    def __post_init__(self):
        _id(self.sheet_id)
        if type(self.identity) is not tables.RowIdentity:_deny()
        self.identity.__post_init__();_id(self.identity.column);_id(self.identity.value,spaces=True)


def _schema(value):
    values=tuple(getattr(value,key) for key in value.__dataclass_fields__)
    for name in values:_id(name)
    if len(values)!=len(set(values)):_deny()


@dataclass(frozen=True)
class BatchColumns:
    batch_id:str;sku:str;variant:str;quantity:str;quantity_unit:str;observed_on:str;weight_amount:str;weight_unit:str;weight_basis:str;weight_kind:str;tariff_id:str;freight_cost_id:str;fees_complete:str;transaction_kind:str
    def __post_init__(self):_schema(self)


@dataclass(frozen=True)
class TariffColumns:
    tariff_id:str;amount:str;currency:str;weight_unit:str;billable_kind:str;valid_from:str;valid_through:str
    def __post_init__(self):_schema(self)


@dataclass(frozen=True)
class ChargeColumns:
    cost_id:str;kind:str;amount:str;currency:str;basis:str;batch_id:str;sku:str;variant:str;observed_on:str;included_1:str;included_2:str;included_3:str;included_4:str
    def __post_init__(self):_schema(self)


@dataclass(frozen=True)
class FXColumns:
    from_currency:str;to_currency:str;target_per_source_unit:str;observed_on:str
    def __post_init__(self):_schema(self)


@dataclass(frozen=True)
class QuoteColumns:
    batch:BatchColumns
    tariff:TariffColumns
    charge:ChargeColumns
    fx:FXColumns
    def __post_init__(self):
        for value,cls in ((self.batch,BatchColumns),(self.tariff,TariffColumns),(self.charge,ChargeColumns),(self.fx,FXColumns)):
            if type(value) is not cls:_deny()
            value.__post_init__()


@dataclass(frozen=True)
class QuoteComponent:
    id:str
    kind:str
    source_amount:Decimal|None
    source_currency:str
    source_basis:str
    included_cost_ids:tuple[str,...]
    fx_target_per_source_unit:Decimal
    batch_amount:Decimal|None
    per_unit_amount:Decimal|None


@dataclass(frozen=True)
class FrozenQuote:
    status:str
    currency:str
    quantity:int
    quantity_basis:str
    components:tuple[QuoteComponent,...]
    missing:tuple[str,...]
    known_components_subtotal:Decimal
    total_batch:Decimal|None
    per_unit:Decimal|None


@dataclass(frozen=True)
class WorkbookPurchaseQuote:
    freight:procurement.Charge
    quote:FrozenQuote
    batch_id:str
    sku:str
    variant:str
    quote_on:date
    as_of:datetime
    source:QuoteSource
    source_fingerprint:str
    input_fingerprint:str
    workbook_fingerprint:str
    observation_fingerprints:tuple[str,...]
    scope:str=field(default='declared_synthetic_purchase_quote',init=False)
    requires_review:bool=field(default=True,init=False)
    live_authorized:bool=field(default=False,init=False)
    purchase_authorized:bool=field(default=False,init=False)


def _source_data(source):
    source.__post_init__()
    return [source.business,source.workbook_id,source.role.value,source.revision,source.timezone_name,_aware(source.valid_from).isoformat(),_aware(source.expires_at).isoformat(),source.revoked]


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
    return _hash(['table-quote-full-workbook:v1',book.id,book.revision,data])


def _result_number(value,*,unknown=False,positive=False):
    if value is None and unknown:return
    if type(value) is not Decimal or not value.is_finite() or value<0 or (positive and value==0) or len(value.as_tuple().digits)>100 or abs(value.as_tuple().exponent)>256:_deny()


def _freeze(result,charges,quantity,currency):
    keys={'status','currency','quantity','quantity_basis','components','missing','known_components_subtotal','total_batch','per_unit'}
    _closed(result,keys)
    if result['status'] not in ('known','unknown') or result['currency']!=currency or type(result['quantity']) is not int or result['quantity']!=quantity or result['quantity_basis']!='count_of_identical_units_in_one_batch' or type(result['components']) is not tuple or len(result['components'])!=len(charges) or len(charges)>5:_deny()
    missing=result['missing']
    allowed={'charge:purchase','charge:freight','charge:commission','fees:incomplete_enumeration'}|{'amount:'+row.id for row in charges}
    if type(missing) is not tuple or len(missing)>9 or len(set(missing))!=len(missing) or not set(missing)<=allowed:_deny()
    if result['status']=='known':
        if missing or result['total_batch'] is None or result['per_unit'] is None:_deny()
    elif not missing or result['total_batch'] is not None or result['per_unit'] is not None:_deny()
    for name in ('known_components_subtotal','total_batch','per_unit'):_result_number(result[name],unknown=name!='known_components_subtotal')
    components=[]
    for data,charge in zip(result['components'],charges):
        _closed(data,QuoteComponent.__dataclass_fields__)
        if data['id']!=charge.id or data['kind']!=charge.kind or data['source_currency']!=charge.money.currency or data['source_basis']!=charge.basis or type(data['included_cost_ids']) is not tuple or data['included_cost_ids']!=charge.included_cost_ids or tables._value(data['source_amount'])!=tables._value(charge.money.amount):_deny()
        for key in ('source_amount','batch_amount','per_unit_amount'):_result_number(data[key],unknown=True)
        _result_number(data['fx_target_per_source_unit'],positive=True)
        if (charge.money.amount is None)!=(data['batch_amount'] is None) or (data['batch_amount'] is None)!=(data['per_unit_amount'] is None):_deny()
        components.append(QuoteComponent(**data))
    return FrozenQuote(result['status'],currency,quantity,result['quantity_basis'],tuple(components),missing,result['known_components_subtotal'],result['total_batch'],result['per_unit'])


class TablePurchaseQuoteReader:
    def __init__(self,*,driver,current_source,expected_source,batch_binding,tariff_binding,charge_bindings,fx_bindings,columns,enabled=False):
        self._driver=driver;self._callback=current_source;self._source=expected_source;self._bindings=(batch_binding,tariff_binding,charge_bindings,fx_bindings);self._columns=columns;self._enabled=enabled is True
        method=getattr(driver,'snapshot',None);self._host=(driver,current_source,expected_source,self._bindings,columns,getattr(method,'__self__',None),getattr(method,'__func__',None))

    def _current(self,now):
        driver,callback,source,bindings,columns,owner,function=self._host
        def identity():
            method=getattr(driver,'snapshot',None)
            if getattr(method,'__self__',None) is not owner or getattr(method,'__func__',None) is not function:_deny()
        if self._driver is not driver or type(driver) is not SQLiteTableDriver or self._callback is not callback or not callable(callback) or self._source is not source or type(source) is not QuoteSource or self._bindings is not bindings or self._columns is not columns or type(columns) is not QuoteColumns:_deny()
        identity();source_data=_source_data(source);columns.__post_init__();batch,tariff,charges,fx=bindings
        if type(batch) is not RowBinding or type(tariff) is not RowBinding or type(charges) is not tuple or len(charges)>4 or type(fx) is not tuple or len(fx)>3:_deny()
        rows=(batch,tariff)+charges+fx
        if any(type(row) is not RowBinding for row in rows):_deny()
        for row in rows:row.__post_init__()
        if len({(row.sheet_id,row.identity.column,row.identity.value) for row in rows})!=len(rows):_deny()
        current=callback()
        if type(current) is not QuoteSource or _source_data(current)!=source_data or source.revoked or not _aware(source.valid_from)<=now<_aware(source.expires_at):_deny()
        identity()
        if self._driver is not driver or self._callback is not callback or self._source is not source or self._bindings is not bindings or self._columns is not columns:_deny()
        return _hash(['table-quote-source:v1',source_data,[[row.sheet_id,row.identity.column,row.identity.value] for row in rows],[len(charges),len(fx)],[[getattr(schema,key) for key in schema.__dataclass_fields__] for schema in (columns.batch,columns.tariff,columns.charge,columns.fx)]])

    def read_quote(self,*,batch_id,sku,variant,quote_on,currency,now):
        if not self._enabled:_deny()
        try:
            now=_aware(now);source_hash=self._current(now)
            for value in (batch_id,sku,variant):_id(value)
            procurement._currency(currency)
            if type(quote_on) is not date or quote_on>now.astimezone(ZoneInfo(self._source.timezone_name)).date():_deny()
            input_hash=_hash([batch_id,sku,variant,quote_on.isoformat(),currency,now.isoformat()]);book=self._driver.snapshot()
            if self._current(now)!=source_hash or type(book) is not tables.Workbook or book.id!=self._source.workbook_id:_deny()
            book_hash=_book_fingerprint(book);selected=set();proofs=[]
            def record(binding,schema):
                row=tables.lookup_row(book,workbook_id=self._source.workbook_id,sheet_id=binding.sheet_id,identity=binding.identity)
                key=(binding.sheet_id,row.position);names=tuple(getattr(schema,name) for name in schema.__dataclass_fields__)
                if key in selected or any(sum(cell.column==name for cell in row.cells)!=1 for name in names):_deny()
                selected.add(key);proofs.append(_hash(['table-quote-row:v1',source_hash,book_hash,binding.sheet_id,tables._row_data(row)]));return {name:next(cell.value for cell in row.cells if cell.column==getattr(schema,name)) for name in schema.__dataclass_fields__}
            batch_binding,tariff_binding,charge_bindings,fx_bindings=self._bindings
            batch=record(batch_binding,self._columns.batch);tariff=record(tariff_binding,self._columns.tariff)
            if batch['batch_id']!=batch_id or batch['sku']!=sku or batch['variant']!=variant or _date_cell(batch['observed_on'])!=quote_on or batch['quantity_unit']!='count_of_identical_units_in_one_batch' or batch['transaction_kind']!='purchase' or type(batch['fees_complete']) is not bool:_deny()
            procurement._quantity(batch['quantity']);_id(batch['tariff_id']);_id(batch['freight_cost_id'])
            if tariff['tariff_id']!=batch['tariff_id'] or not _date_cell(tariff['valid_from'])<=quote_on<=_date_cell(tariff['valid_through']):_deny()
            weight=procurement.Weight(_amount(batch['weight_amount']),batch['weight_unit'],batch['weight_basis'],batch['weight_kind'])
            rate=procurement.FreightTariff(procurement.Money(_amount(tariff['amount']),tariff['currency']),tariff['weight_unit'],tariff['billable_kind'])
            charges=[]
            for binding in charge_bindings:
                data=record(binding,self._columns.charge)
                if data['batch_id']!=batch_id or data['sku']!=sku or data['variant']!=variant or _date_cell(data['observed_on'])!=quote_on or data['kind'] not in ('purchase','commission','fee'):_deny()
                _id(data['cost_id']);included=tuple(data[key] for key in ('included_1','included_2','included_3','included_4') if data[key] is not None)
                for value in included:_id(value)
                charges.append(procurement.Charge(data['cost_id'],data['kind'],procurement.Money(_amount(data['amount']),data['currency']),data['basis'],included))
            fx=[]
            for binding in fx_bindings:
                data=record(binding,self._columns.fx)
                if data['to_currency']!=currency or _date_cell(data['observed_on'])!=quote_on:_deny()
                fx.append(procurement.FXRate(data['from_currency'],data['to_currency'],_amount(data['target_per_source_unit'],unknown=False,positive=True)))
            freight=procurement.freight_from_weight(batch['freight_cost_id'],weight,rate)
            if type(freight) is not procurement.Charge or freight.id!=batch['freight_cost_id'] or freight.kind!='freight' or type(freight.money) is not procurement.Money or freight.money.currency!=rate.money_per_weight_unit.currency or freight.basis!=weight.basis or freight.included_cost_ids!=():_deny()
            _result_number(freight.money.amount,unknown=True)
            charges=tuple(charges)+(freight,)
            actual=procurement.quote_purchase(quantity=batch['quantity'],currency=currency,charges=charges,fx_rates=tuple(fx),fees_complete=batch['fees_complete'],transaction_kind=batch['transaction_kind'])
            quote=_freeze(actual,charges,batch['quantity'],currency)
            if self._current(now)!=source_hash:_deny()
            if _book_fingerprint(self._driver.snapshot())!=book_hash or self._current(now)!=source_hash:_deny()
            return WorkbookPurchaseQuote(freight,quote,batch_id,sku,variant,quote_on,now,self._source,source_hash,input_hash,book_hash,tuple(proofs))
        except PurchaseQuoteUnavailable:raise
        except Exception:raise PurchaseQuoteUnavailable() from None
