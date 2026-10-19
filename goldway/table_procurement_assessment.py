"""Disabled durable declared weight/logistics sample; no operational authority."""
from dataclasses import dataclass,field
from datetime import date,datetime,timezone
from decimal import Decimal
from enum import Enum
from zoneinfo import ZoneInfo
from types import MappingProxyType
from typing import Mapping
import hashlib,json,re
from .table_sqlite_driver import SQLiteTableDriver
from . import table_change_plan as tables
from . import procurement_metrics as procurement
class AssessmentReaderUnavailable(ValueError):
    def __init__(self):super().__init__('table_procurement_assessment_unavailable')
def _deny():raise AssessmentReaderUnavailable()

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
    return _hash(['table-procurement-assessment-full-workbook:v1',book.id,book.revision,data])


def _date(value):
    if type(value) is not str or len(value)!=10 or re.fullmatch(r'\d{4}-\d{2}-\d{2}',value) is None:_deny()
    result=date.fromisoformat(value)
    if result.isoformat()!=value:_deny()
    return result

class SourceRole(Enum):PROCUREMENT_ASSESSMENT_SAMPLE='procurement_assessment_sample'
@dataclass(frozen=True)
class AssessmentSource:
    business:str;observation_scope:str;workbook_id:str;role:SourceRole;revision:int;timezone:str;valid_from:datetime;expires_at:datetime;revoked:bool=False
    def __post_init__(self):
        for v in (self.business,self.observation_scope,self.workbook_id,self.timezone):_id(v)
        ZoneInfo(self.timezone)
        if re.fullmatch(r'sqlite-book:[0-9a-f]{64}',self.workbook_id) is None or self.role is not SourceRole.PROCUREMENT_ASSESSMENT_SAMPLE or type(self.revision) is not int or not 1<=self.revision<2**53 or type(self.revoked) is not bool or _aware(self.valid_from)>=_aware(self.expires_at):_deny()
def _schema(s):
    names=tuple(getattr(s,k) for k in s.__dataclass_fields__)
    for n in names:_id(n)
    if len(names)!=len(set(names)):_deny()
@dataclass(frozen=True)
class CommonColumns:
    record_id:str;role:str;batch_id:str;sku:str;variant:str;observed_on:str
    def __post_init__(self):_schema(self)
@dataclass(frozen=True)
class BatchColumns:
    quantity:str;quantity_unit:str;weight_threshold:str;freight_threshold:str;costs_independent:str
    def __post_init__(self):_schema(self)
@dataclass(frozen=True)
class WeightColumns:
    amount:str;unit:str;basis:str;kind:str
    def __post_init__(self):_schema(self)
@dataclass(frozen=True)
class ChargeColumns:
    cost_id:str;amount:str;currency:str;basis:str;included_cost_id1:str;included_cost_id2:str;included_cost_id3:str;included_cost_id4:str
    def __post_init__(self):_schema(self)
@dataclass(frozen=True)
class AssessmentColumns:
    common:CommonColumns;batch:BatchColumns;weight:WeightColumns;charge:ChargeColumns
    def __post_init__(self):
        if type(self.common) is not CommonColumns:_deny()
        self.common.__post_init__();common={getattr(self.common,k) for k in self.common.__dataclass_fields__}
        for schema,cls in ((self.batch,BatchColumns),(self.weight,WeightColumns),(self.charge,ChargeColumns)):
            if type(schema) is not cls:_deny()
            schema.__post_init__()
            if common&{getattr(schema,k) for k in schema.__dataclass_fields__}:_deny()
@dataclass(frozen=True)
class BatchAssessment:
    batch_id:str;sku:str;variant:str;weight:Mapping;logistics:Mapping;source_ids:tuple[str,...]
@dataclass(frozen=True)
class WorkbookProcurementAssessments:
    batches:tuple[BatchAssessment,...]
    observed_on:date
    source:AssessmentSource
    source_fingerprint:str
    input_fingerprint:str
    workbook_fingerprint:str
    scope:str=field(default='declared_synthetic_procurement_assessment_sample',init=False)
    requires_review:bool=field(default=True,init=False)
    live_authorized:bool=field(default=False,init=False)
    business_norm_verified:bool=field(default=False,init=False)
    purchase_authorized:bool=field(default=False,init=False)

def _number(value,*,allow_int=False):
    if value is None:return None
    if type(value) is int and allow_int:
        if not 0<=value<=10**18:_deny()
        return Decimal(value)
    if type(value) is not Decimal or not value.is_finite() or value<0 or len(value.as_tuple().digits)>50 or abs(value.as_tuple().exponent)>18:_deny()
    return value

def _source_data(s):
    s.__post_init__()
    return [s.business,s.observation_scope,s.workbook_id,s.role.value,s.revision,s.timezone,_aware(s.valid_from).isoformat(),_aware(s.expires_at).isoformat(),s.revoked]

def _closed(result,kind,*,quantity,currency,threshold):
    if type(result) is not dict or type(result.get('status')) is not str or type(result.get('reason')) is not str:_deny()
    status=result['status'];reason=result['reason'];measure='relative_deviation' if kind=='weight' else 'ratio'
    if status=='unknown':
        reasons=('incomparable_net_gross','missing_weight_or_threshold','zero_expected_weight') if kind=='weight' else ('missing_amount_or_threshold','zero_purchase_baseline')
        if set(result)!={'status','reason',measure} or reason not in reasons or result[measure] is not None:_deny()
    elif status in ('flag','within_threshold'):
        extra={'actual_batch_kg','expected_batch_kg','max_relative_deviation'} if kind=='weight' else {'max_freight_to_purchase_ratio','currency','quantity_basis','quantity'}
        if set(result)!={'status','reason',measure}|extra or reason!='caller_threshold':_deny()
        numeric=('relative_deviation','actual_batch_kg','expected_batch_kg','max_relative_deviation') if kind=='weight' else ('ratio','max_freight_to_purchase_ratio')
        for k in numeric:
            value=result[k]
            if type(value) is not Decimal or not value.is_finite() or value<0 or len(value.as_tuple().digits)>200 or abs(value.as_tuple().exponent)>200:_deny()
        key='max_relative_deviation' if kind=='weight' else 'max_freight_to_purchase_ratio'
        if threshold is None or result[key].as_tuple()!=threshold.as_tuple():_deny()
        if kind=='logistics' and (result['currency']!=currency or result['quantity_basis']!='batch' or type(result['quantity']) is not int or result['quantity']!=quantity):_deny()
    else:_deny()
    return MappingProxyType(dict(result))

class TableProcurementAssessmentReader:
    def __init__(self,*,driver,current_source,expected_source,sheet_id,columns,enabled=False):
        self._driver=driver;self._callback=current_source;self._source=expected_source;self._sheet=sheet_id;self._columns=columns;self._enabled=enabled is True
        m=getattr(driver,'snapshot',None);self._host=(driver,current_source,expected_source,sheet_id,columns,getattr(m,'__self__',None),getattr(m,'__func__',None))
    def _current(self,now):
        driver,callback,source,sheet,columns,owner,function=self._host
        def identity():
            m=getattr(driver,'snapshot',None)
            if self._driver is not driver or self._callback is not callback or self._source is not source or self._sheet is not sheet or self._columns is not columns or getattr(m,'__self__',None) is not owner or getattr(m,'__func__',None) is not function:_deny()
        identity()
        if type(driver) is not SQLiteTableDriver or not callable(callback) or type(source) is not AssessmentSource or type(columns) is not AssessmentColumns:_deny()
        data=_source_data(source);_id(sheet);columns.__post_init__();current=callback()
        if type(current) is not AssessmentSource or _source_data(current)!=data or source.revoked or not _aware(source.valid_from)<=now<_aware(source.expires_at):_deny()
        identity()
        return _hash(['table-procurement-assessment-source:v1',data,sheet,[[getattr(s,k) for k in s.__dataclass_fields__] for s in (columns.common,columns.batch,columns.weight,columns.charge)]])
    def read_assessments(self,*,observed_on,now):
        if not self._enabled:_deny()
        try:
            now=_aware(now);proof=self._current(now)
            if type(observed_on) is not date or observed_on>now.astimezone(ZoneInfo(self._source.timezone)).date():_deny()
            book=self._driver.snapshot()
            if self._current(now)!=proof or type(book) is not tables.Workbook or book.id!=self._source.workbook_id:_deny()
            book_hash=_book_fingerprint(book);sheets=[s for s in book.sheets if s.id==self._sheet]
            if len(sheets)!=1 or not 5<=len(sheets[0].rows)<=15:_deny()
            batches={};ids=set();common=self._columns.common
            for row in sheets[0].rows:
                cells={c.column:c.value for c in row.cells};technical=_id(cells.get(common.record_id));role=cells.get(common.role)
                if technical in ids or type(role) is not str or role not in ('batch','measured','reference','purchase','freight'):_deny()
                ids.add(technical);schema=self._columns.batch if role=='batch' else self._columns.weight if role in ('measured','reference') else self._columns.charge
                names={getattr(s,k) for s in (common,schema) for k in s.__dataclass_fields__}
                if set(cells)!=names or len(row.cells)!=len(names):_deny()
                selected=tables.lookup_row(book,workbook_id=self._source.workbook_id,sheet_id=self._sheet,identity=tables.RowIdentity(common.record_id,technical))
                if tables._row_data(selected)!=tables._row_data(row):_deny()
                data={k:cells[getattr(s,k)] for s in (common,schema) for k in s.__dataclass_fields__}
                bid=_id(data['batch_id']);sku=_id(data['sku']);variant=_id(data['variant'])
                if _date(data['observed_on'])!=observed_on:_deny()
                batch=batches.setdefault(bid,dict(sku=sku,variant=variant,roles={},refs=[]))
                if len(batches)>3 or batch['sku']!=sku or batch['variant']!=variant or role in batch['roles']:_deny()
                batch['roles'][role]=data;batch['refs'].append('table-assessment:'+_hash(['table-procurement-assessment-row:v1',proof,book_hash,tables._row_data(row)]))
            output=[]
            for bid,batch in batches.items():
                roles=batch['roles']
                if set(roles)!= {'batch','measured','reference','purchase','freight'}:_deny()
                header=roles['batch'];quantity=header['quantity']
                if type(quantity) is not int or not 1<=quantity<=10**9 or header['quantity_unit']!='count_of_identical_units_in_one_batch' or type(header['quantity_unit']) is not str or header['costs_independent'] is not True:_deny()
                wt=_number(header['weight_threshold']);ft=_number(header['freight_threshold'])
                def weight(role):
                    d=roles[role];return procurement.Weight(_number(d['amount'],allow_int=True),d['unit'],d['basis'],d['kind'])
                def charge(role):
                    d=roles[role];included=tuple(_id(d['included_cost_id'+str(i)]) for i in range(1,5) if d['included_cost_id'+str(i)] is not None)
                    return procurement.Charge(_id(d['cost_id']),role,procurement.Money(_number(d['amount'],allow_int=True),d['currency']),d['basis'],included)
                actual,expected=weight('measured'),weight('reference');purchase,freight=charge('purchase'),charge('freight')
                if purchase.money.currency!=freight.money.currency:_deny()
                w=procurement.assess_weight(actual=actual,expected=expected,max_relative_deviation=wt,quantity=quantity)
                l=procurement.assess_logistics(purchase=purchase,freight=freight,quantity=quantity,max_freight_to_purchase_ratio=ft)
                output.append(BatchAssessment(bid,batch['sku'],batch['variant'],_closed(w,'weight',quantity=quantity,currency=purchase.money.currency,threshold=wt),_closed(l,'logistics',quantity=quantity,currency=purchase.money.currency,threshold=ft),tuple(batch['refs'])))
            if self._current(now)!=proof or _book_fingerprint(self._driver.snapshot())!=book_hash or self._current(now)!=proof:_deny()
            return WorkbookProcurementAssessments(tuple(output),observed_on,self._source,proof,_hash(['table-procurement-assessment-input:v1',observed_on.isoformat(),now.isoformat()]),book_hash)
        except AssessmentReaderUnavailable:raise
        except Exception:raise AssessmentReaderUnavailable() from None
