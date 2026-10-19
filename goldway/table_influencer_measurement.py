"""Disabled metadata-only selected attribution sample; never finance or contact rights."""
from dataclasses import dataclass,field
from datetime import datetime,timedelta,timezone
from enum import Enum
import hashlib
import json
import re
from zoneinfo import ZoneInfo
from .table_sqlite_driver import SQLiteTableDriver
from . import table_change_plan as tables
from . import influencer_metrics as metrics


class InfluencerMeasurementUnavailable(ValueError):
    def __init__(self):super().__init__('table_influencer_measurement_unavailable')


def _deny():raise InfluencerMeasurementUnavailable()

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


class SourceRole(Enum):INFLUENCER_SAMPLE='influencer_sample'


@dataclass(frozen=True)
class MeasurementSource:
    business:str
    account_scope:str
    workbook_id:str
    role:SourceRole
    participant_ids:tuple[str,...]
    revision:int
    timezone_name:str
    valid_from:datetime
    expires_at:datetime
    revoked:bool=False
    def __post_init__(self):
        for value in (self.business,self.account_scope,self.workbook_id,self.timezone_name):_id(value)
        if re.fullmatch(r'sqlite-book:[0-9a-f]{64}',self.workbook_id) is None or self.role is not SourceRole.INFLUENCER_SAMPLE or type(self.participant_ids) is not tuple or not 1<=len(self.participant_ids)<=3 or len(set(self.participant_ids))!=len(self.participant_ids) or type(self.revision) is not int or not 1<=self.revision<2**53 or type(self.revoked) is not bool:_deny()
        for value in self.participant_ids:_id(value)
        ZoneInfo(self.timezone_name)
        if _aware(self.valid_from)>=_aware(self.expires_at):_deny()


def _schema(value):
    values=tuple(getattr(value,key) for key in value.__dataclass_fields__)
    for name in values:_id(name)
    if len(values)!=len(set(values)):_deny()


@dataclass(frozen=True)
class BindingColumns:
    key:str;participant_id:str;confirmed:str
    def __post_init__(self):_schema(self)
@dataclass(frozen=True)
class ShipmentColumns:
    id:str;recipient_alias:str;at:str;barter_confirmed:str
    def __post_init__(self):_schema(self)
@dataclass(frozen=True)
class ClickColumns:
    id:str;promo_code:str;at:str
    def __post_init__(self):_schema(self)
@dataclass(frozen=True)
class OrderColumns:
    id:str;at:str;promo_1:str;promo_2:str;referral_alias:str
    def __post_init__(self):_schema(self)
@dataclass(frozen=True)
class RefundColumns:
    id:str;order_id:str;at:str
    def __post_init__(self):_schema(self)
@dataclass(frozen=True)
class ReportColumns:
    id:str;shipment_id:str;at:str;result_present:str
    def __post_init__(self):_schema(self)


@dataclass(frozen=True)
class MeasurementColumns:
    row_id:str
    kind:str
    alias:BindingColumns
    promo:BindingColumns
    shipment:ShipmentColumns
    click:ClickColumns
    order:OrderColumns
    refund:RefundColumns
    report:ReportColumns
    def __post_init__(self):
        _id(self.row_id);_id(self.kind)
        if self.row_id==self.kind:_deny()
        for name,cls in (('alias',BindingColumns),('promo',BindingColumns),('shipment',ShipmentColumns),('click',ClickColumns),('order',OrderColumns),('refund',RefundColumns),('report',ReportColumns)):
            schema=getattr(self,name)
            if type(schema) is not cls:_deny()
            schema.__post_init__()
            if {self.row_id,self.kind}&{getattr(schema,key) for key in schema.__dataclass_fields__}:_deny()


@dataclass(frozen=True)
class MeasurementProjection:
    influencer_id:str
    window:metrics.Window
    shipment_ids:tuple[str,...]
    click_ids:tuple[str,...]
    order_ids:tuple[str,...]
    refund_ids:tuple[str,...]
    unattributed_order_ids:tuple[str,...]
    unattributed_shipment_ids:tuple[str,...]
    unattributed_click_ids:tuple[str,...]
    unattributed_refund_ids:tuple[str,...]
    barter_states:tuple[tuple[str,metrics.BarterState],...]
    unmeasurable_barter_ids:tuple[str,...]
    source_ids:tuple[str,...]


@dataclass(frozen=True)
class WorkbookInfluencerMeasurement:
    measurement:MeasurementProjection
    due:tuple[metrics.DueCandidate,...]
    source:MeasurementSource
    as_of:datetime
    report_after_hours:int
    source_fingerprint:str
    input_fingerprint:str
    workbook_fingerprint:str
    observation_fingerprints:tuple[str,...]
    scope:str=field(default='declared_synthetic_influencer_metadata_sample',init=False)
    requires_review:bool=field(default=True,init=False)
    live_authorized:bool=field(default=False,init=False)
    request_authorized:bool=field(default=False,init=False)


def _source_data(source):
    source.__post_init__()
    return [source.business,source.account_scope,source.workbook_id,source.role.value,source.participant_ids,source.revision,source.timezone_name,_aware(source.valid_from).isoformat(),_aware(source.expires_at).isoformat(),source.revoked]


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
    return _hash(['table-influencer-full-workbook:v1',book.id,book.revision,data])


class TableInfluencerMeasurementReader:
    def __init__(self,*,driver,current_source,expected_source,sheet_id,columns,enabled=False):
        self._driver=driver;self._callback=current_source;self._source=expected_source;self._sheet=sheet_id;self._columns=columns;self._enabled=enabled is True
        method=getattr(driver,'snapshot',None);self._host=(driver,current_source,expected_source,sheet_id,columns,getattr(method,'__self__',None),getattr(method,'__func__',None))

    def _current(self,now):
        driver,callback,source,sheet,columns,owner,function=self._host
        def identity():
            method=getattr(driver,'snapshot',None)
            if getattr(method,'__self__',None) is not owner or getattr(method,'__func__',None) is not function:_deny()
        if self._driver is not driver or type(driver) is not SQLiteTableDriver or self._callback is not callback or not callable(callback) or self._source is not source or type(source) is not MeasurementSource or self._sheet is not sheet or self._columns is not columns or type(columns) is not MeasurementColumns:_deny()
        identity();data=_source_data(source);_id(sheet);columns.__post_init__();current=callback()
        if type(current) is not MeasurementSource or _source_data(current)!=data or source.revoked or not _aware(source.valid_from)<=now<_aware(source.expires_at):_deny()
        identity()
        if self._driver is not driver or self._callback is not callback or self._source is not source or self._sheet is not sheet or self._columns is not columns:_deny()
        return _hash(['table-influencer-source:v1',data,sheet,columns.row_id,columns.kind,[[name,[getattr(getattr(columns,name),key) for key in getattr(columns,name).__dataclass_fields__]] for name in ('alias','promo','shipment','click','order','refund','report')]])

    def _input(self,influencer_id,window,currency,hours,now):
        _id(influencer_id);metrics._currency(currency)
        if influencer_id not in self._source.participant_ids or type(window) is not metrics.Window or type(hours) is not int or hours!=168:_deny()
        window.__post_init__();start=_aware(window.start);end=_aware(window.end)
        if not timedelta(0)<end-start<=timedelta(days=366) or end>now:_deny()
        return _hash(['table-influencer-input:v1',influencer_id,start.isoformat(),end.isoformat(),currency,hours,now.isoformat()])

    def read_measurement(self,*,influencer_id,window,currency,report_after_hours,now):
        if not self._enabled:_deny()
        try:
            now=_aware(now);source_hash=self._current(now);input_hash=self._input(influencer_id,window,currency,report_after_hours,now);book=self._driver.snapshot()
            if self._current(now)!=source_hash or self._input(influencer_id,window,currency,report_after_hours,now)!=input_hash or type(book) is not tables.Workbook or book.id!=self._source.workbook_id:_deny()
            book_hash=_book_fingerprint(book);sheets=[sheet for sheet in book.sheets if sheet.id==self._sheet]
            if len(sheets)!=1 or len(sheets[0].rows)>24:_deny()
            caps=dict(alias=3,promo=3,shipment=3,click=4,order=4,refund=3,report=4);groups={name:[] for name in caps};semantic={name:set() for name in caps};ids=set();proofs=[]
            money=metrics.Money(None,currency)
            for row in sheets[0].rows:
                cells={cell.column:cell.value for cell in row.cells};technical=_id(cells.get(self._columns.row_id));kind=cells.get(self._columns.kind)
                if technical in ids or type(kind) is not str or kind not in caps:_deny()
                ids.add(technical);schema=getattr(self._columns,kind);names={getattr(schema,key) for key in schema.__dataclass_fields__}|{self._columns.row_id,self._columns.kind}
                if set(cells)!=names or len(row.cells)!=len(names) or len(groups[kind])>=caps[kind]:_deny()
                selected=tables.lookup_row(book,workbook_id=self._source.workbook_id,sheet_id=self._sheet,identity=tables.RowIdentity(self._columns.row_id,technical))
                if tables._row_data(selected)!=tables._row_data(row):_deny()
                data={name:cells[getattr(schema,name)] for name in schema.__dataclass_fields__};identifier=_id(data['key'] if kind in ('alias','promo') else data['id'])
                if identifier in semantic[kind]:_deny()
                semantic[kind].add(identifier);proof=_hash(['table-influencer-row:v1',source_hash,book_hash,tables._row_data(row)]);proofs.append(proof);source_id='table-influencer:'+proof
                if kind in ('alias','promo'):
                    _id(data['participant_id'])
                    if data['participant_id'] not in self._source.participant_ids or type(data['confirmed']) is not bool:_deny()
                    result=metrics.Binding(identifier,data['participant_id'],data['confirmed'],source_id)
                elif kind=='shipment':
                    _id(data['recipient_alias']);metrics._flag(data['barter_confirmed']);result=metrics.Shipment(identifier,data['recipient_alias'],_instant(data['at']),data['barter_confirmed'],money,source_id)
                elif kind=='click':_id(data['promo_code']);result=metrics.Click(identifier,data['promo_code'],_instant(data['at']),source_id)
                elif kind=='order':
                    codes=tuple(data[name] for name in ('promo_1','promo_2') if data[name] is not None)
                    for code in codes:_id(code)
                    if data['referral_alias'] is not None:_id(data['referral_alias'])
                    result=metrics.Order(identifier,codes,data['referral_alias'],_instant(data['at']),money,money,source_id)
                elif kind=='refund':_id(data['order_id']);result=metrics.Refund(identifier,data['order_id'],_instant(data['at']),money,money,source_id)
                else:
                    _id(data['shipment_id'])
                    if type(data['result_present']) is not bool:_deny()
                    result=metrics.PlacementReport(identifier,data['shipment_id'],_instant(data['at']),data['result_present'],source_id)
                groups[kind].append(result)
            groups={name:tuple(rows) for name,rows in groups.items()};groups['report']=tuple(sorted(groups['report'],key=lambda row:(_aware(row.observed_at),row.id)))
            result=metrics.evaluate(influencer_id=influencer_id,window=window,currency=currency,aliases=groups['alias'],promos=groups['promo'],shipments=groups['shipment'],clicks=groups['click'],orders=groups['order'],refunds=groups['refund'],reports=groups['report'],records_complete=False,barter_limit=None)
            projection=self._projection(result,influencer_id,window,currency,groups,proofs)
            ships=tuple(row for row in groups['shipment'] if row.id in result.shipment_ids);reports=tuple(row for row in groups['report'] if row.shipment_id in result.shipment_ids)
            due=metrics.report_due_candidates(ships,reports,now=now)
            if type(due) is not tuple or len(due)>len(ships) or len({row.shipment_id for row in due if type(row) is metrics.DueCandidate})!=len(due):_deny()
            by_id={row.id:row for row in ships}
            for row in due:
                if type(row) is not metrics.DueCandidate or row.shipment_id not in by_id or row.recipient_alias!=by_id[row.shipment_id].recipient_alias or row.source_id!=by_id[row.shipment_id].source_id or _aware(row.due_at)>now:_deny()
            if self._current(now)!=source_hash or self._input(influencer_id,window,currency,report_after_hours,now)!=input_hash:_deny()
            if _book_fingerprint(self._driver.snapshot())!=book_hash or self._current(now)!=source_hash or self._input(influencer_id,window,currency,report_after_hours,now)!=input_hash:_deny()
            return WorkbookInfluencerMeasurement(projection,due,self._source,now,report_after_hours,source_hash,input_hash,book_hash,tuple(proofs))
        except InfluencerMeasurementUnavailable:raise
        except Exception:raise InfluencerMeasurementUnavailable() from None

    def _projection(self,result,target,window,currency,groups,proofs):
        if type(result) is not metrics.InfluencerMetrics or result.influencer_id!=target or type(result.window) is not metrics.Window or (_aware(result.window.start),_aware(result.window.end))!=(_aware(window.start),_aware(window.end)) or result.limit_state!='unknown':_deny()
        financials=result.financials
        if type(financials) is not metrics.Financials or financials.state!='unknown' or financials.currency!=currency or any(getattr(financials,key) is not None for key in ('net_revenue','margin_after_refunds','barter_cost','net_after_barter','roi')) or type(financials.reasons) is not tuple or 'incomplete_observations' not in financials.reasons or len(financials.reasons)>6 or not set(financials.reasons)<={'incomplete_observations','unresolved_attribution','unknown_barter','missing_amount','currency_conflict'}:_deny()
        for selected,unattributed,kind in (('shipment_ids','unattributed_shipment_ids','shipment'),('click_ids','unattributed_click_ids','click'),('order_ids','unattributed_order_ids','order'),('refund_ids','unattributed_refund_ids','refund')):
            allowed={row.id for row in groups[kind]};by_id={row.id:row for row in groups[kind]};sets=[]
            for name in (selected,unattributed):
                values=getattr(result,name)
                if type(values) is not tuple or len(values)>len(allowed) or len(set(values))!=len(values) or not set(values)<=allowed:_deny()
                if any(not window.contains(by_id[value].shipped_at if kind=='shipment' else by_id[value].occurred_at) for value in values):_deny()
                sets.append(set(values))
            if sets[0]&sets[1]:_deny()
        if type(result.barter_states) is not tuple or len(result.barter_states)!=len(result.shipment_ids):_deny()
        states={}
        for item in result.barter_states:
            if type(item) is not tuple or len(item)!=2 or item[0] not in result.shipment_ids or item[0] in states or type(item[1]) is not metrics.BarterState:_deny()
            states[item[0]]=item[1]
        if set(states)!=set(result.shipment_ids) or type(result.unmeasurable_barter_ids) is not tuple or len(set(result.unmeasurable_barter_ids))!=len(result.unmeasurable_barter_ids) or not set(result.unmeasurable_barter_ids)<=set(result.shipment_ids):_deny()
        allowed={'table-influencer:'+proof for proof in proofs}
        if type(result.source_ids) is not tuple or len(result.source_ids)>24 or len(set(result.source_ids))!=len(result.source_ids) or not set(result.source_ids)<=allowed:_deny()
        return MeasurementProjection(*(getattr(result,name) for name in MeasurementProjection.__dataclass_fields__))
