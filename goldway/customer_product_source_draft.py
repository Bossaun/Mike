"""Disabled current metadata candidate + explicit product row -> actual review draft."""
from dataclasses import dataclass,field
from datetime import datetime,timedelta,timezone
from decimal import Decimal
import hashlib,json,re
from .store import Store
from .customer_ingest import CustomerIngest,SourceRegistration,SourceProof,registration_fingerprint
from . import customer_rules as core
from .table_sqlite_driver import SQLiteTableDriver
from . import table_change_plan as tables
class ProductSourceUnavailable(ValueError):
    def __init__(self):super().__init__('customer_product_source_unavailable')
def _deny():raise ProductSourceUnavailable()

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
    return _hash(['customer-product-full-workbook:v1',book.id,book.revision,data])


def _conversation(v):
    if type(v) is not core.Conversation:_deny()
    v.__post_init__()
    return [_id(getattr(v,k)) for k in ('customer_id','channel','thread_id','source_id')]
@dataclass(frozen=True)
class ProductSource:
    business:str;conversation:core.Conversation;workbook_id:str;revision:int;valid_from:datetime;expires_at:datetime;revoked:bool;confirmed:bool
    def __post_init__(self):
        _id(self.business);_id(self.workbook_id);_conversation(self.conversation)
        if re.fullmatch('sqlite-book:[0-9a-f]{64}',self.workbook_id) is None or type(self.revision) is not int or not 1<=self.revision<2**53 or type(self.revoked) is not bool or type(self.confirmed) is not bool or _aware(self.valid_from)>=_aware(self.expires_at):_deny()
@dataclass(frozen=True)
class ProductBinding:
    sheet_id:str;identity:tables.RowIdentity
    def __post_init__(self):
        _id(self.sheet_id)
        if type(self.identity) is not tables.RowIdentity:_deny()
        self.identity.__post_init__();_id(self.identity.column);_id(self.identity.value)
@dataclass(frozen=True)
class ProductColumns:
    evidence_id:str;product_id:str;available:str;price:str;currency:str;delivery:str;valid_from:str;valid_until:str
    def __post_init__(self):
        names=tuple(getattr(self,k) for k in self.__dataclass_fields__)
        for n in names:_id(n)
        if len(names)!=len(set(names)):_deny()
@dataclass(frozen=True)
class CurrentProductDraft:
    draft:core.ReplyDraft
    candidate:core.Candidate
    source_proof:SourceProof
    consulted_product_source_id:str
    product_source_fingerprint:str
    workbook_fingerprint:str
    row_fingerprint:str
    input_fingerprint:str
    requires_review:bool=field(default=True,init=False)
    live_authorized:bool=field(default=False,init=False)
    customer_send_authorized:bool=field(default=False,init=False)
    source_facts_independently_verified:bool=field(default=False,init=False)

def _method(obj,name):
    m=getattr(obj,name,None);return (getattr(m,'__self__',None),getattr(m,'__func__',None))
def _product_data(s):
    if type(s) is not ProductSource:_deny()
    s.__post_init__()
    return [s.business,_conversation(s.conversation),s.workbook_id,s.revision,_aware(s.valid_from).isoformat(),_aware(s.expires_at).isoformat(),s.revoked,s.confirmed]
def _price(value):
    if type(value) is int:
        if not 0<=value<=10**18:_deny()
        return Decimal(value)
    if value is not None and (type(value) is not Decimal or not value.is_finite() or value<0 or len(value.as_tuple().digits)>50 or abs(value.as_tuple().exponent)>18):_deny()
    return value

class CustomerProductSourceDraft:
    def __init__(self,*,store,ingest,source,conversation,product_driver,current_product_source,expected_product_source,product_binding,columns,enabled=False):
        self._store=store;self._ingest=ingest;self._source=source;self._conversation=conversation;self._driver=product_driver;self._callback=current_product_source;self._product_source=expected_product_source;self._binding=product_binding;self._columns=columns;self._enabled=enabled is True
        self._host=(store,getattr(store,'db',None),ingest,source,conversation,product_driver,current_product_source,expected_product_source,product_binding,columns,_method(ingest,'source_proof'),_method(ingest,'preview_unanswered'),_method(product_driver,'snapshot'))
    def _pure(self):
        s,db,ingest,source,conv,driver,callback,product,binding,columns,proof,preview,snapshot=self._host
        if self._store is not s or type(s) is not Store or s.db is not db or self._ingest is not ingest or type(ingest) is not CustomerIngest or self._source is not source or type(source) is not SourceRegistration or self._conversation is not conv or self._driver is not driver or type(driver) is not SQLiteTableDriver or self._callback is not callback or not callable(callback) or self._product_source is not product or self._binding is not binding or type(binding) is not ProductBinding or self._columns is not columns or type(columns) is not ProductColumns:_deny()
        for actual,pinned in ((_method(ingest,'source_proof'),proof),(_method(ingest,'preview_unanswered'),preview),(_method(driver,'snapshot'),snapshot)):
            if actual[0] is not pinned[0] or actual[1] is not pinned[1]:_deny()
        convdata=_conversation(conv);registration=registration_fingerprint(source);productdata=_product_data(product);binding.__post_init__();columns.__post_init__()
        if conv.source_id!=source.source_id or conv.channel!=source.channel or product.business!=source.business or product.conversation!=conv or binding.identity.column!=columns.product_id:_deny()
        return _hash(['customer-product-scope:v1',registration,convdata,productdata,binding.sheet_id,binding.identity.column,binding.identity.value,[getattr(columns,k) for k in columns.__dataclass_fields__]])
    def _input(self,incoming,candidate,product,now,window,stale,dispositions):
        for value in (incoming,candidate,product):_id(value)
        if product!=self._binding.identity.value or type(window) is not core.Window or type(stale) is not timedelta or not timedelta(0)<stale<=timedelta(days=366) or type(dispositions) is not tuple or len(dispositions)>128:_deny()
        window.__post_init__();start=_aware(window.start);end=_aware(window.end)
        if not timedelta(0)<end-start<=timedelta(days=366):_deny()
        records=[]
        for d in dispositions:
            if type(d) is not core.Disposition or d.conversation!=self._conversation:_deny()
            d.__post_init__();records.append([_id(d.id),_id(d.candidate_id),_aware(d.at).isoformat(),d.state,_id(d.evidence_id)])
        return _hash(['customer-product-input:v1',incoming,candidate,product,now.isoformat(),start.isoformat(),end.isoformat(),[stale.days,stale.seconds,stale.microseconds],records])
    def _proof(self,now):
        proof=self._ingest.source_proof(now=now,expected_store=self._store)
        if type(proof) is not SourceProof or proof.source_id!=self._source.source_id or proof.business!=self._source.business or proof.channel!=self._source.channel or proof.registration_fingerprint!=registration_fingerprint(self._source) or proof.state_present is not True or type(proof.state_digest) is not str or re.fullmatch('[0-9a-f]{64}',proof.state_digest) is None:_deny()
        return proof
    def _candidate(self,incoming,identifier,now,window,stale,dispositions):
        preview=self._ingest.preview_unanswered(self._conversation,now=now,window=window,stale_after=stale,dispositions=dispositions)
        if type(preview) is not core.Preview or type(preview.candidates) is not tuple or len(preview.candidates)!=1 or type(preview.blocked) is not tuple or preview.blocked:_deny()
        c=preview.candidates[0]
        if type(c) is not core.Candidate or c.conversation!=self._conversation or c.kind!='unanswered' or c.target_id!=incoming or c.id!=identifier or c.owner_id is not None or c.draft_only is not True or _aware(c.due_at)>now or type(c.evidence_ids) is not tuple or not 1<=len(c.evidence_ids)<=2 or len(set(c.evidence_ids))!=len(c.evidence_ids) or type(c.draft) is not str or not c.draft.strip() or len(c.draft)>400:_deny()
        for value in (c.id,c.target_id,*c.evidence_ids):_id(value)
        return c
    def _product_current(self,now):
        data=_product_data(self._product_source);current=self._callback()
        if _product_data(current)!=data or self._product_source.revoked or not _aware(self._product_source.valid_from)<=now<_aware(self._product_source.expires_at):_deny()
        return _hash(['customer-product-source:v1',data])
    def read_draft(self,*,incoming_event_id,candidate_id,product_id,now,window,stale_after,dispositions):
        if not self._enabled:_deny()
        try:
            now=_aware(now);scope=self._pure();inputs=self._input(incoming_event_id,candidate_id,product_id,now,window,stale_after,dispositions)
            def check():
                if self._pure()!=scope or self._input(incoming_event_id,candidate_id,product_id,now,window,stale_after,dispositions)!=inputs:_deny()
            proof=self._proof(now);check();candidate=self._candidate(incoming_event_id,candidate_id,now,window,stale_after,dispositions);check()
            productproof=self._product_current(now);check();book=self._driver.snapshot();check()
            if type(book) is not tables.Workbook or book.id!=self._product_source.workbook_id:_deny()
            bookproof=_book_fingerprint(book);row=tables.lookup_row(book,workbook_id=book.id,sheet_id=self._binding.sheet_id,identity=self._binding.identity)
            names={getattr(self._columns,k) for k in self._columns.__dataclass_fields__};cells={c.column:c.value for c in row.cells}
            if set(cells)!=names or len(row.cells)!=8:_deny()
            data={k:cells[getattr(self._columns,k)] for k in self._columns.__dataclass_fields__}
            if _id(data['product_id'])!=product_id:_deny()
            rowproof=_hash(['customer-product-row:v1',scope,bookproof,tables._row_data(row)]);ref='customer-product:'+rowproof
            evidence=core.ProductEvidence(_id(data['evidence_id']),self._conversation,product_id,data['available'],_price(data['price']),data['currency'],data['delivery'],core.Window(_instant(data['valid_from']),_instant(data['valid_until'])),self._product_source.confirmed,ref)
            draft=core.product_draft(conversation=self._conversation,product_id=product_id,evidence=evidence,now=now)
            if type(draft) is not core.ReplyDraft or draft.conversation!=self._conversation or draft.product_id!=product_id or draft.draft_only is not True or type(draft.text) is not str or not draft.text or len(draft.text.encode('utf-16-le'))//2>3500 or len(draft.text.encode())>8000 or type(draft.missing) is not tuple or len(set(draft.missing))!=len(draft.missing) or not set(draft.missing)<={'availability','price','delivery'} or type(draft.source_ids) is not tuple or draft.source_ids not in ((),(ref,)):_deny()
            check()
            if self._product_current(now)!=productproof:_deny()
            check()
            if _book_fingerprint(self._driver.snapshot())!=bookproof:_deny()
            check();candidate2=self._candidate(incoming_event_id,candidate_id,now,window,stale_after,dispositions);check();proof2=self._proof(now);check()
            if candidate2!=candidate or proof2!=proof:_deny()
            if self._product_current(now)!=productproof:_deny()
            check()
            return CurrentProductDraft(draft,candidate,proof,ref,productproof,bookproof,rowproof,inputs)
        except ProductSourceUnavailable:raise
        except Exception:raise ProductSourceUnavailable() from None
