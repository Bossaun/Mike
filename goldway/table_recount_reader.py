"""Disabled two-workbook synthetic recount sample, never stock or permission writes."""
from dataclasses import dataclass,field
from datetime import date,datetime,timezone
from decimal import Decimal
import hashlib
import json
import re
from zoneinfo import ZoneInfo
from .table_sqlite_driver import SQLiteTableDriver
from . import table_change_plan as tables
from . import inventory_metrics as inventory


class RecountUnavailable(ValueError):
    def __init__(self):super().__init__('table_recount_unavailable')


def _deny():raise RecountUnavailable()

def _id(value,*,spaces=False,maximum=128,bytes_max=512):
    if type(value) is not str or not 0<len(value)<=maximum or not value.strip() or (not spaces and value!=value.strip()) or len(value.encode())>bytes_max or any(ord(c)<32 or ord(c)==127 for c in value):_deny()
    return value

def _hash(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def _aware(value):
    if type(value) is not datetime or value.utcoffset() is None:_deny()
    restored=value.astimezone(timezone.utc).astimezone(value.tzinfo)
    if restored.replace(tzinfo=None)!=value.replace(tzinfo=None):_deny()
    return value.astimezone(timezone.utc)

def _date_cell(value):
    if type(value) is not str or date.fromisoformat(value).isoformat()!=value:_deny()
    return date.fromisoformat(value)

def _quantity(value):
    if value is None:return inventory.UNKNOWN
    if type(value) is int:
        if not 0<=value<=10**18:_deny()
        value=Decimal(value)
    if type(value) is not Decimal:_deny()
    inventory._quantity(value,'quantity')
    return value

def _dimension(value):
    return inventory.UNKNOWN if value is None else _id(value)


@dataclass(frozen=True)
class StockSource:
    business:str
    workbook_id:str
    role:inventory.StockKind
    revision:int
    timezone_name:str
    valid_from:datetime
    expires_at:datetime
    revoked:bool=False

    def __post_init__(self):
        _id(self.business);_id(self.workbook_id);_id(self.timezone_name)
        if re.fullmatch(r'sqlite-book:[0-9a-f]{64}',self.workbook_id) is None or type(self.role) is not inventory.StockKind or self.role not in (inventory.StockKind.BOOK,inventory.StockKind.PHYSICAL) or type(self.revision) is not int or not 1<=self.revision<2**53 or type(self.revoked) is not bool:_deny()
        ZoneInfo(self.timezone_name)
        if _aware(self.valid_from)>=_aware(self.expires_at):_deny()


@dataclass(frozen=True)
class SourcePair:
    book:StockSource
    physical:StockSource

    def __post_init__(self):
        if type(self.book) is not StockSource or type(self.physical) is not StockSource:_deny()
        self.book.__post_init__();self.physical.__post_init__()
        if self.book.role is not inventory.StockKind.BOOK or self.physical.role is not inventory.StockKind.PHYSICAL or self.book.business!=self.physical.business or self.book.timezone_name!=self.physical.timezone_name or self.book.workbook_id==self.physical.workbook_id:_deny()


@dataclass(frozen=True)
class RowBinding:
    sheet_id:str
    identity:tables.RowIdentity

    def __post_init__(self):
        _id(self.sheet_id)
        if type(self.identity) is not tables.RowIdentity:_deny()
        self.identity.__post_init__();_id(self.identity.column);_id(self.identity.value,spaces=True)


@dataclass(frozen=True)
class ColumnSchema:
    sku:str
    variant:str
    location:str
    batch:str
    quantity:str
    unit:str
    observed_on:str
    kind:str

    def __post_init__(self):
        values=tuple(getattr(self,key) for key in self.__dataclass_fields__)
        for value in values:_id(value)
        if len(set(values))!=8:_deny()


@dataclass(frozen=True)
class WorkbookRecountReport:
    lines:tuple[inventory.RecountLine,...]
    observed_on:date
    as_of:datetime
    sources:SourcePair
    book_count:int
    physical_count:int
    source_fingerprint:str
    book_fingerprint:str
    physical_fingerprint:str
    observation_fingerprints:tuple[tuple[str,str],...]
    scope:str=field(default='declared_two_workbook_sample',init=False)
    requires_review:bool=field(default=True,init=False)
    live_authorized:bool=field(default=False,init=False)
    warehouse_activation:bool=field(default=False,init=False)


def _source_data(pair):
    pair.__post_init__()
    return [[row.business,row.workbook_id,row.role.value,row.revision,row.timezone_name,_aware(row.valid_from).isoformat(),_aware(row.expires_at).isoformat(),row.revoked] for row in (pair.book,pair.physical)]


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
    return _hash(['table-recount-full-workbook:v1',book.id,book.revision,data])


class TableRecountReader:
    def __init__(self,*,book_driver,physical_driver,current_sources,expected_sources,book_bindings,physical_bindings,columns,enabled=False):
        self._drivers=(book_driver,physical_driver);self._callback=current_sources;self._sources=expected_sources;self._bindings=(book_bindings,physical_bindings);self._columns=columns;self._enabled=enabled is True
        methods=tuple((getattr(getattr(driver,'snapshot',None),'__self__',None),getattr(getattr(driver,'snapshot',None),'__func__',None)) for driver in self._drivers)
        self._host=(self._drivers,current_sources,expected_sources,self._bindings,columns,methods)

    def _current(self,now):
        drivers,callback,sources,bindings,columns,methods=self._host
        if self._drivers is not drivers or len(drivers)!=2 or drivers[0] is drivers[1] or any(type(driver) is not SQLiteTableDriver for driver in drivers) or self._callback is not callback or not callable(callback) or self._sources is not sources or type(sources) is not SourcePair or self._bindings is not bindings or self._columns is not columns or type(columns) is not ColumnSchema:_deny()
        def identities():
            for driver,expected in zip(drivers,methods):
                method=getattr(driver,'snapshot',None)
                if getattr(method,'__self__',None) is not expected[0] or getattr(method,'__func__',None) is not expected[1]:_deny()
        identities();data=_source_data(sources);columns.__post_init__()
        for rows in bindings:
            if type(rows) is not tuple or len(rows)>5 or any(type(row) is not RowBinding for row in rows):_deny()
            for row in rows:row.__post_init__()
            if len({(row.sheet_id,row.identity.column,row.identity.value) for row in rows})!=len(rows):_deny()
        if not bindings[0] and not bindings[1]:_deny()
        current=callback()
        if type(current) is not SourcePair or _source_data(current)!=data:_deny()
        for source in (sources.book,sources.physical):
            if source.revoked or not _aware(source.valid_from)<=now<_aware(source.expires_at):_deny()
        identities()
        if self._drivers is not drivers or self._callback is not callback or self._sources is not sources or self._bindings is not bindings or self._columns is not columns:_deny()
        return _hash(['table-recount-source:v1',data,[[[row.sheet_id,row.identity.column,row.identity.value] for row in rows] for rows in bindings],[getattr(columns,key) for key in columns.__dataclass_fields__]])

    def _observations(self,book,source,bindings,columns,observed_on,source_digest,book_digest):
        result=[];proofs=[];selected=set();names=tuple(getattr(columns,key) for key in columns.__dataclass_fields__)
        for binding in bindings:
            row=tables.lookup_row(book,workbook_id=source.workbook_id,sheet_id=binding.sheet_id,identity=binding.identity)
            key=(binding.sheet_id,row.position)
            if key in selected:_deny()
            selected.add(key)
            if any(sum(cell.column==name for cell in row.cells)!=1 for name in names):_deny()
            cells={cell.column:cell.value for cell in row.cells}
            sku=_id(cells[columns.sku]);variant=_dimension(cells[columns.variant]);location=_dimension(cells[columns.location]);batch=_dimension(cells[columns.batch])
            unit=_id(cells[columns.unit],maximum=40,bytes_max=160)
            if type(cells[columns.kind]) is not str or cells[columns.kind]!=source.role.value or _date_cell(cells[columns.observed_on])!=observed_on:_deny()
            stock_key=inventory.StockKey(inventory.ItemKey(sku,variant),location,batch)
            provenance=_hash(['table-recount-observation:v1',source_digest,book_digest,source.role.value,binding.sheet_id,tables._row_data(row),observed_on.isoformat()])
            result.append(inventory.StockSnapshot(stock_key,_quantity(cells[columns.quantity]),unit,observed_on,'table-recount:'+provenance,source.role));proofs.append((source.role.value,provenance))
        return tuple(result),tuple(proofs)

    def read_recount(self,*,observed_on,now):
        if not self._enabled:_deny()
        try:
            now=_aware(now);source_digest=self._current(now);sources=self._sources
            if type(observed_on) is not date or observed_on>now.astimezone(ZoneInfo(sources.book.timezone_name)).date():_deny()
            initial=[];fingerprints=[]
            for driver,source in zip(self._drivers,(sources.book,sources.physical)):
                book=driver.snapshot()
                if self._current(now)!=source_digest or type(book) is not tables.Workbook or book.id!=source.workbook_id:_deny()
                fingerprints.append(_book_fingerprint(book));initial.append(book)
            books,book_proofs=self._observations(initial[0],sources.book,self._bindings[0],self._columns,observed_on,source_digest,fingerprints[0])
            physical,physical_proofs=self._observations(initial[1],sources.physical,self._bindings[1],self._columns,observed_on,source_digest,fingerprints[1])
            union={row.key for row in books+physical}
            if not 1<=len(union)<=5:_deny()
            lines=inventory.recount(books,physical)
            if type(lines) is not tuple or len(lines)!=len(union) or any(type(row) is not inventory.RecountLine for row in lines) or {row.key for row in lines}!=union:_deny()
            if any(type(row.state) is not inventory.RecountState or row.observed_on!=observed_on for row in lines):_deny()
            if self._current(now)!=source_digest:_deny()
            for driver,expected in zip(self._drivers,fingerprints):
                if _book_fingerprint(driver.snapshot())!=expected or self._current(now)!=source_digest:_deny()
            return WorkbookRecountReport(lines,observed_on,now,sources,len(books),len(physical),source_digest,fingerprints[0],fingerprints[1],book_proofs+physical_proofs)
        except RecountUnavailable:raise
        except Exception:raise RecountUnavailable() from None
