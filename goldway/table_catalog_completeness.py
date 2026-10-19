"""Default-disabled readonly declared catalog sample; no photo/price verification."""
from dataclasses import dataclass,field
from datetime import date,datetime,timezone
from decimal import Decimal
from enum import Enum
from zoneinfo import ZoneInfo
import hashlib,json,re
from .table_sqlite_driver import SQLiteTableDriver
from . import table_change_plan as tables
from . import inventory_metrics as inventory

class CatalogReaderUnavailable(ValueError):
    def __init__(self):super().__init__('table_catalog_completeness_unavailable')
def _deny():raise CatalogReaderUnavailable()

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
    return _hash(['table-catalog-full-workbook:v1',book.id,book.revision,data])


class SourceRole(Enum):CATALOG_SAMPLE='catalog_sample'
@dataclass(frozen=True)
class CatalogSource:
    business:str;catalog_scope:str;workbook_id:str;role:SourceRole;revision:int;timezone:str;valid_from:datetime;expires_at:datetime;revoked:bool=False
    def __post_init__(self):
        for value in (self.business,self.catalog_scope,self.workbook_id,self.timezone):_id(value)
        ZoneInfo(self.timezone)
        if re.fullmatch(r'sqlite-book:[0-9a-f]{64}',self.workbook_id) is None or self.role is not SourceRole.CATALOG_SAMPLE or type(self.revision) is not int or not 1<=self.revision<2**53 or type(self.revoked) is not bool or _aware(self.valid_from)>=_aware(self.expires_at):_deny()
@dataclass(frozen=True)
class CatalogColumns:
    record_id:str;sku:str;variant:str;observed_on:str;photo_present:str;price_state:str;price_amount:str;currency:str
    def __post_init__(self):
        names=tuple(getattr(self,k) for k in self.__dataclass_fields__)
        for name in names:_id(name)
        if len(names)!=len(set(names)):_deny()
@dataclass(frozen=True)
class CatalogReference:
    item:inventory.ItemKey
    source_id:str
    row_fingerprint:str
@dataclass(frozen=True)
class WorkbookCatalogCompleteness:
    completeness:inventory.Completeness
    selected_count:int
    observed_on:date
    currency:str|None
    source:CatalogSource
    references:tuple[CatalogReference,...]
    source_fingerprint:str
    input_fingerprint:str
    workbook_fingerprint:str
    scope:str=field(default='declared_synthetic_catalog_sample',init=False)
    requires_review:bool=field(default=True,init=False)
    live_authorized:bool=field(default=False,init=False)
    whole_catalog_verified:bool=field(default=False,init=False)
    photo_content_verified:bool=field(default=False,init=False)
    price_verified:bool=field(default=False,init=False)

def _source_data(source):
    source.__post_init__()
    return [source.business,source.catalog_scope,source.workbook_id,source.role.value,source.revision,source.timezone,_aware(source.valid_from).isoformat(),_aware(source.expires_at).isoformat(),source.revoked]
def _price(value):
    if type(value) is int:
        if not 0<=value<=10**18:_deny()
        return Decimal(value)
    if type(value) is not Decimal or not value.is_finite() or value<0 or len(value.as_tuple().digits)>50 or abs(value.as_tuple().exponent)>18:_deny()
    return value

def _currency(value):
    if value is not None and (type(value) is not str or re.fullmatch('[A-Z]{3}',value) is None):_deny()
    return value

def _date(value):
    if type(value) is not str or len(value)!=10 or re.fullmatch(r'\d{4}-\d{2}-\d{2}',value) is None:_deny()
    result=date.fromisoformat(value)
    if result.isoformat()!=value:_deny()
    return result

class TableCatalogReader:
    def __init__(self,*,driver,current_source,expected_source,sheet_id,columns,enabled=False):
        self._driver=driver;self._callback=current_source;self._source=expected_source;self._sheet=sheet_id;self._columns=columns;self._enabled=enabled is True
        m=getattr(driver,'snapshot',None);self._host=(driver,current_source,expected_source,sheet_id,columns,getattr(m,'__self__',None),getattr(m,'__func__',None))
    def _current(self,now):
        driver,callback,source,sheet,columns,owner,function=self._host
        def identity():
            m=getattr(driver,'snapshot',None)
            if self._driver is not driver or self._callback is not callback or self._source is not source or self._sheet is not sheet or self._columns is not columns or getattr(m,'__self__',None) is not owner or getattr(m,'__func__',None) is not function:_deny()
        identity()
        if type(driver) is not SQLiteTableDriver or not callable(callback) or type(source) is not CatalogSource or type(columns) is not CatalogColumns:_deny()
        data=_source_data(source);_id(sheet);columns.__post_init__();current=callback()
        if type(current) is not CatalogSource or _source_data(current)!=data or source.revoked or not _aware(source.valid_from)<=now<_aware(source.expires_at):_deny()
        identity()
        return _hash(['table-catalog-source:v1',data,sheet,[getattr(columns,k) for k in columns.__dataclass_fields__]])
    def read_catalog(self,*,observed_on,currency,now):
        if not self._enabled:_deny()
        try:
            now=_aware(now);proof=self._current(now);currency=_currency(currency)
            if type(observed_on) is not date or observed_on>now.astimezone(ZoneInfo(self._source.timezone)).date():_deny()
            inputs=_hash(['table-catalog-input:v1',observed_on.isoformat(),currency,now.isoformat()]);book=self._driver.snapshot()
            if self._current(now)!=proof or type(book) is not tables.Workbook or book.id!=self._source.workbook_id:_deny()
            book_hash=_book_fingerprint(book);sheets=[s for s in book.sheets if s.id==self._sheet]
            if len(sheets)!=1 or len(sheets[0].rows)>12:_deny()
            observations=[];references=[];ids=set();items=set();names={getattr(self._columns,k) for k in self._columns.__dataclass_fields__}
            for row in sheets[0].rows:
                cells={c.column:c.value for c in row.cells}
                if set(cells)!=names or len(row.cells)!=len(names):_deny()
                data={k:cells[getattr(self._columns,k)] for k in self._columns.__dataclass_fields__};technical=_id(data['record_id'])
                if technical in ids:_deny()
                ids.add(technical)
                selected=tables.lookup_row(book,workbook_id=self._source.workbook_id,sheet_id=self._sheet,identity=tables.RowIdentity(self._columns.record_id,technical))
                if tables._row_data(selected)!=tables._row_data(row):_deny()
                item=inventory.ItemKey(_id(data['sku']),_id(data['variant']))
                if item in items or _date(data['observed_on'])!=observed_on or _currency(data['currency'])!=currency:_deny()
                items.add(item);photo=data['photo_present']
                if photo is None:photo=inventory.UNKNOWN
                elif type(photo) is not bool:_deny()
                state=data['price_state'];amount=data['price_amount']
                if type(state) is not str or state not in ('known','missing','unknown'):_deny()
                if state=='known':
                    if currency is None:_deny()
                    price=_price(amount)
                else:
                    if amount is not None:_deny()
                    price=None if state=='missing' else inventory.UNKNOWN
                fp=_hash(['table-catalog-row:v1',proof,book_hash,tables._row_data(row)]);source_id='table-catalog:'+fp
                observations.append(inventory.CatalogObservation(item,photo,price,observed_on,source_id));references.append(CatalogReference(item,source_id,fp))
            result=inventory.completeness(tuple(observations));self._result(result,items)
            if self._current(now)!=proof or _book_fingerprint(self._driver.snapshot())!=book_hash or self._current(now)!=proof:_deny()
            return WorkbookCatalogCompleteness(result,len(observations),observed_on,currency,self._source,tuple(references),proof,inputs,book_hash)
        except CatalogReaderUnavailable:raise
        except Exception:raise CatalogReaderUnavailable() from None
    def _result(self,result,items):
        if type(result) is not inventory.Completeness:_deny()
        groups=[]
        for name in ('missing_photos','unknown_photos','missing_prices','unknown_prices'):
            values=getattr(result,name)
            if type(values) is not tuple or len(values)>12 or any(type(item) is not inventory.ItemKey or item not in items for item in values) or len(set(values))!=len(values):_deny()
            groups.append(set(values))
        if groups[0]&groups[1] or groups[2]&groups[3]:_deny()
