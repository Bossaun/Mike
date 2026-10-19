"""Disabled declared financial sample; no verified finance or spending authority."""
from dataclasses import dataclass,field
from datetime import datetime,timedelta,timezone
from enum import Enum
from decimal import Decimal
import hashlib
import json
import re
from zoneinfo import ZoneInfo
from .table_sqlite_driver import SQLiteTableDriver
from . import table_change_plan as tables
from . import influencer_metrics as metrics


class InfluencerFinancialUnavailable(ValueError):
    def __init__(self):super().__init__('table_influencer_financials_unavailable')


def _deny():raise InfluencerFinancialUnavailable()

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


class SourceRole(Enum):INFLUENCER_FINANCIAL_SAMPLE='influencer_financial_sample'


@dataclass(frozen=True)
class FinancialSource:
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
        if re.fullmatch(r'sqlite-book:[0-9a-f]{64}',self.workbook_id) is None or self.role is not SourceRole.INFLUENCER_FINANCIAL_SAMPLE or type(self.participant_ids) is not tuple or not 1<=len(self.participant_ids)<=3 or len(set(self.participant_ids))!=len(self.participant_ids) or type(self.revision) is not int or not 1<=self.revision<2**53 or type(self.revoked) is not bool:_deny()
        for value in self.participant_ids:_id(value)
        ZoneInfo(self.timezone_name)
        if _aware(self.valid_from)>=_aware(self.expires_at):_deny()


def _schema(value):
    values=tuple(getattr(value,key) for key in value.__dataclass_fields__)
    for name in values:_id(name)
    if len(values)!=len(set(values)):_deny()


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
    return _hash(['table-influencer-financial-full-workbook:v1',book.id,book.revision,data])


@dataclass(frozen=True)
class HeadColumns:
    target_id:str;window_start:str;window_end:str;currency:str;records_complete:str;limit_present:str;limit_amount:str;limit_currency:str
    def __post_init__(self):_schema(self)
@dataclass(frozen=True)
class BindingColumns:
    key:str;participant_id:str;confirmed:str
    def __post_init__(self):_schema(self)
@dataclass(frozen=True)
class ShipmentColumns:
    id:str;recipient_alias:str;at:str;barter_confirmed:str;cost_amount:str;cost_currency:str
    def __post_init__(self):_schema(self)
@dataclass(frozen=True)
class OrderColumns:
    id:str;at:str;promo_1:str;promo_2:str;referral_alias:str;revenue_amount:str;revenue_currency:str;margin_amount:str;margin_currency:str
    def __post_init__(self):_schema(self)
@dataclass(frozen=True)
class RefundColumns:
    id:str;order_id:str;at:str;revenue_reduction:str;revenue_currency:str;margin_reduction:str;margin_currency:str
    def __post_init__(self):_schema(self)
@dataclass(frozen=True)
class FinancialColumns:
    row_id:str;kind:str;head:HeadColumns;alias:BindingColumns;promo:BindingColumns;shipment:ShipmentColumns;order:OrderColumns;refund:RefundColumns
    def __post_init__(self):
        _id(self.row_id);_id(self.kind)
        if self.row_id==self.kind:_deny()
        for name,cls in (('head',HeadColumns),('alias',BindingColumns),('promo',BindingColumns),('shipment',ShipmentColumns),('order',OrderColumns),('refund',RefundColumns)):
            schema=getattr(self,name)
            if type(schema) is not cls:_deny()
            schema.__post_init__()
            if {self.row_id,self.kind}&{getattr(schema,k) for k in schema.__dataclass_fields__}:_deny()
@dataclass(frozen=True)
class WorkbookInfluencerFinancials:
    result:metrics.InfluencerMetrics
    source:FinancialSource
    as_of:datetime
    records_complete:bool
    barter_limit:metrics.Money|None
    consulted_source_ids:tuple[str,...]
    source_fingerprint:str
    input_fingerprint:str
    workbook_fingerprint:str
    scope:str=field(default='declared_synthetic_influencer_financial_sample',init=False)
    requires_review:bool=field(default=True,init=False)
    live_authorized:bool=field(default=False,init=False)
    financial_source_verified:bool=field(default=False,init=False)
    agreed_limit_verified:bool=field(default=False,init=False)
    spending_authorized:bool=field(default=False,init=False)

def _money(amount,currency):
    if type(amount) is int:
        if abs(amount)>10**18:_deny()
        amount=Decimal(amount)
    if amount is not None and (type(amount) is not Decimal or not amount.is_finite() or len(amount.as_tuple().digits)>50 or abs(amount.as_tuple().exponent)>18):_deny()
    return metrics.Money(amount,currency)
def _source_data(s):
    s.__post_init__()
    return [s.business,s.account_scope,s.workbook_id,s.role.value,s.participant_ids,s.revision,s.timezone_name,_aware(s.valid_from).isoformat(),_aware(s.expires_at).isoformat(),s.revoked]

class TableInfluencerFinancialReader:
    def __init__(self,*,driver,current_source,expected_source,sheet_id,columns,enabled=False):
        self._driver=driver;self._callback=current_source;self._source=expected_source;self._sheet=sheet_id;self._columns=columns;self._enabled=enabled is True
        m=getattr(driver,'snapshot',None);self._host=(driver,current_source,expected_source,sheet_id,columns,getattr(m,'__self__',None),getattr(m,'__func__',None))
    def _current(self,now):
        driver,callback,source,sheet,columns,owner,function=self._host
        def identity():
            m=getattr(driver,'snapshot',None)
            if self._driver is not driver or self._callback is not callback or self._source is not source or self._sheet is not sheet or self._columns is not columns or getattr(m,'__self__',None) is not owner or getattr(m,'__func__',None) is not function:_deny()
        identity()
        if type(driver) is not SQLiteTableDriver or not callable(callback) or type(source) is not FinancialSource or type(columns) is not FinancialColumns:_deny()
        data=_source_data(source);_id(sheet);columns.__post_init__();current=callback()
        if type(current) is not FinancialSource or _source_data(current)!=data or source.revoked or not _aware(source.valid_from)<=now<_aware(source.expires_at):_deny()
        identity()
        return _hash(['table-influencer-financial-source:v1',data,sheet,columns.row_id,columns.kind,[[name,[getattr(getattr(columns,name),k) for k in getattr(columns,name).__dataclass_fields__]] for name in ('head','alias','promo','shipment','order','refund')]])
    def _input(self,target,window,currency,now):
        _id(target);metrics._currency(currency)
        if target not in self._source.participant_ids or type(window) is not metrics.Window:_deny()
        window.__post_init__();start=_aware(window.start);end=_aware(window.end)
        if not timedelta(0)<end-start<=timedelta(days=366) or end>now:_deny()
        return _hash(['table-influencer-financial-input:v1',target,start.isoformat(),end.isoformat(),currency,now.isoformat()])
    def read_financials(self,*,influencer_id,window,currency,now):
        if not self._enabled:_deny()
        try:
            now=_aware(now);proof=self._current(now);inputs=self._input(influencer_id,window,currency,now);book=self._driver.snapshot()
            if self._current(now)!=proof or self._input(influencer_id,window,currency,now)!=inputs or type(book) is not tables.Workbook or book.id!=self._source.workbook_id:_deny()
            bh=_book_fingerprint(book);sheets=[s for s in book.sheets if s.id==self._sheet]
            if len(sheets)!=1 or not 2<=len(sheets[0].rows)<=17:_deny()
            caps=dict(head=1,alias=3,promo=3,shipment=3,order=4,refund=3);groups={k:[] for k in caps};semantic={k:set() for k in caps};ids=set();refs=[]
            for row in sheets[0].rows:
                cells={c.column:c.value for c in row.cells};technical=_id(cells.get(self._columns.row_id));kind=cells.get(self._columns.kind)
                if technical in ids or type(kind) is not str or kind not in caps or len(groups[kind])>=caps[kind]:_deny()
                ids.add(technical);schema=getattr(self._columns,kind);names={getattr(schema,k) for k in schema.__dataclass_fields__}|{self._columns.row_id,self._columns.kind}
                if set(cells)!=names or len(row.cells)!=len(names):_deny()
                selected=tables.lookup_row(book,workbook_id=self._source.workbook_id,sheet_id=self._sheet,identity=tables.RowIdentity(self._columns.row_id,technical))
                if tables._row_data(selected)!=tables._row_data(row):_deny()
                d={k:cells[getattr(schema,k)] for k in schema.__dataclass_fields__};ref='table-influencer-financial:'+_hash(['table-influencer-financial-row:v1',proof,bh,tables._row_data(row)]);refs.append(ref)
                if kind=='head':
                    if d['target_id']!=influencer_id or type(d['records_complete']) is not bool or type(d['limit_present']) is not bool or d['currency']!=currency or (_instant(d['window_start']),_instant(d['window_end']))!=(_aware(window.start),_aware(window.end)):_deny()
                    if d['limit_present']:limit=_money(d['limit_amount'],d['limit_currency']);metrics._money(limit,nonnegative=True)
                    else:
                        if d['limit_amount'] is not None or d['limit_currency'] is not None:_deny()
                        limit=None
                    value=(d['records_complete'],limit)
                else:
                    identifier=_id(d['key'] if kind in ('alias','promo') else d['id'])
                    if identifier in semantic[kind]:_deny()
                    semantic[kind].add(identifier)
                    if kind in ('alias','promo'):
                        if _id(d['participant_id']) not in self._source.participant_ids or type(d['confirmed']) is not bool:_deny()
                        value=metrics.Binding(identifier,d['participant_id'],d['confirmed'],ref)
                    elif kind=='shipment':value=metrics.Shipment(identifier,_id(d['recipient_alias']),_instant(d['at']),d['barter_confirmed'],_money(d['cost_amount'],d['cost_currency']),ref)
                    elif kind=='order':
                        codes=tuple(_id(d[k]) for k in ('promo_1','promo_2') if d[k] is not None)
                        alias=None if d['referral_alias'] is None else _id(d['referral_alias'])
                        value=metrics.Order(identifier,codes,alias,_instant(d['at']),_money(d['revenue_amount'],d['revenue_currency']),_money(d['margin_amount'],d['margin_currency']),ref)
                    else:value=metrics.Refund(identifier,_id(d['order_id']),_instant(d['at']),_money(d['revenue_reduction'],d['revenue_currency']),_money(d['margin_reduction'],d['margin_currency']),ref)
                groups[kind].append(value)
            if len(groups['head'])!=1 or not groups['shipment']:_deny()
            groups={k:tuple(v) for k,v in groups.items()};complete,limit=groups['head'][0]
            result=metrics.evaluate(influencer_id=influencer_id,window=window,currency=currency,aliases=groups['alias'],promos=groups['promo'],shipments=groups['shipment'],orders=groups['order'],refunds=groups['refund'],clicks=(),reports=(),records_complete=complete,barter_limit=limit)
            self._result(result,influencer_id,window,currency,groups,refs)
            if self._current(now)!=proof or self._input(influencer_id,window,currency,now)!=inputs or _book_fingerprint(self._driver.snapshot())!=bh or self._current(now)!=proof or self._input(influencer_id,window,currency,now)!=inputs:_deny()
            return WorkbookInfluencerFinancials(result,self._source,now,complete,limit,tuple(refs),proof,inputs,bh)
        except InfluencerFinancialUnavailable:raise
        except Exception:raise InfluencerFinancialUnavailable() from None
    def _result(self,r,target,window,currency,groups,refs):
        if type(r) is not metrics.InfluencerMetrics or r.influencer_id!=target or type(r.window) is not metrics.Window or (_aware(r.window.start),_aware(r.window.end))!=(_aware(window.start),_aware(window.end)) or r.limit_state not in ('unknown','within_limit','exceeded'):_deny()
        f=r.financials
        if type(f) is not metrics.Financials or f.state not in ('known','unknown','zero_denominator') or f.currency!=currency or type(f.reasons) is not tuple or len(set(f.reasons))!=len(f.reasons) or not set(f.reasons)<={'incomplete_observations','unresolved_attribution','unknown_barter','missing_amount','currency_conflict','zero_barter_cost'}:_deny()
        for k in ('net_revenue','margin_after_refunds','barter_cost','net_after_barter','roi'):
            v=getattr(f,k)
            if v is not None and (type(v) is not Decimal or not v.is_finite() or len(v.as_tuple().digits)>200 or abs(v.as_tuple().exponent)>200):_deny()
        if f.barter_cost is not None and f.barter_cost<0:_deny()
        if f.state=='unknown':
            if not f.reasons or 'zero_barter_cost' in f.reasons or any(getattr(f,k) is not None for k in ('net_revenue','margin_after_refunds','net_after_barter','roi')):_deny()
        elif f.state=='zero_denominator':
            if f.reasons!=('zero_barter_cost',) or f.barter_cost!=0 or f.roi is not None or any(getattr(f,k) is None for k in ('net_revenue','margin_after_refunds','net_after_barter')):_deny()
        elif f.reasons or any(getattr(f,k) is None for k in ('net_revenue','margin_after_refunds','barter_cost','net_after_barter','roi')) or f.barter_cost<=0:_deny()
        if r.limit_state!='unknown' and f.barter_cost is None:_deny()
        for selected,unattributed,kind in (('shipment_ids','unattributed_shipment_ids','shipment'),('order_ids','unattributed_order_ids','order'),('refund_ids','unattributed_refund_ids','refund')):
            allowed={v.id:v for v in groups[kind]};sets=[]
            for name in (selected,unattributed):
                values=getattr(r,name)
                if type(values) is not tuple or len(set(values))!=len(values) or not set(values)<=set(allowed):_deny()
                if any(not window.contains(allowed[v].shipped_at if kind=='shipment' else allowed[v].occurred_at) for v in values):_deny()
                sets.append(set(values))
            if sets[0]&sets[1]:_deny()
        if r.click_ids!=() or r.unattributed_click_ids!=() or type(r.barter_states) is not tuple or len(r.barter_states)!=len(r.shipment_ids):_deny()
        states={}
        for value in r.barter_states:
            if type(value) is not tuple or len(value)!=2 or value[0] not in r.shipment_ids or value[0] in states or type(value[1]) is not metrics.BarterState:_deny()
            states[value[0]]=value[1]
        if type(r.unmeasurable_barter_ids) is not tuple or len(set(r.unmeasurable_barter_ids))!=len(r.unmeasurable_barter_ids) or not set(r.unmeasurable_barter_ids)<=set(r.shipment_ids):_deny()
        if type(r.source_ids) is not tuple or len(set(r.source_ids))!=len(r.source_ids) or len(r.source_ids)>17 or not set(r.source_ids)<=set(refs):_deny()
