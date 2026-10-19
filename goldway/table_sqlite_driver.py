"""Disabled durable SYNTHETIC workbook driver; no issuer, external write or auth UI."""
from dataclasses import dataclass,field,replace
from decimal import Decimal
import hashlib
import json
import re

from . import table_change_plan as core
from .store import Store

MAX_STATE_BYTES=262144
_FORMAT='sqlite-table-driver-v1'
_KEYS={'format','version','namespace','workbook','used_nonces'}


class TableDriverUnavailable(ValueError):
    def __init__(self):super().__init__('sqlite_table_driver_unavailable')


def _deny():raise TableDriverUnavailable()


def _id(value,*,spaces=False):
    if type(value) is not str or not 0<len(value)<=128 or not value.strip() or (not spaces and value!=value.strip()) or len(value.encode())>512 or any(ord(c)<32 or ord(c)==127 for c in value):_deny()
    return value


def _json(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def _closed(value,keys):
    if type(value) is not dict or set(value)!=set(keys):_deny()
    return value


def _pairs(pairs):
    result={}
    for key,value in pairs:
        if key in result:_deny()
        result[key]=value
    return result


@dataclass(frozen=True)
class TableContext:
    business:str
    driver_id:str
    approval_domain:str
    logical_workbook_id:str
    required_approver:str
    key_id:str
    verification_key:bytes=field(repr=False)

    @property
    def namespace(self):return dict(business=self.business,driver_id=self.driver_id,approval_domain=self.approval_domain,logical_workbook_id=self.logical_workbook_id)

    @property
    def namespace_hash(self):return hashlib.sha256(_json(self.namespace).encode()).hexdigest()

    @property
    def workbook_id(self):return 'sqlite-book:'+self.namespace_hash

    @property
    def runtime_key(self):return 'table-driver:v1:'+self.namespace_hash


@dataclass(frozen=True)
class ApplyReceipt:
    workbook_id:str
    sheet_id:str
    workbook_revision:str
    sheet_revision:str
    row_revision:str
    plan_digest:str
    used_nonce_count:int
    state:str=field(default='local_fake_committed',init=False)
    live_authorized:bool=field(default=False,init=False)


def _value(value):
    result=core._value(value)
    if type(value) is str:value.encode('utf-8')
    return result


def _scalar(tagged):
    if type(tagged) is not list or len(tagged)!=2:_deny()
    tag,raw=tagged
    if tag=='null' and raw is None:value=None
    elif tag=='bool' and type(raw) is bool:value=raw
    elif tag=='str' and type(raw) is str and len(raw)<=4096:value=raw
    elif tag=='int' and type(raw) is str and re.fullmatch(r'-?(?:0|[1-9][0-9]{0,18})',raw):value=int(raw)
    elif tag=='decimal' and type(raw) is str and len(raw)<=80 and re.fullmatch(r'-?(?:0|[1-9][0-9]{0,49})(?:\.[0-9]{1,50})?(?:E[+-]?[0-9]{1,2})?',raw):value=Decimal(raw)
    else:_deny()
    if _value(value)!=tagged:_deny()
    return value


def _row(row):
    if type(row) is not core.Row or type(row.cells) is not tuple or not 1<=len(row.cells)<=16:_deny()
    _id(row.revision)
    for cell in row.cells:
        if type(cell) is not core.Cell:_deny()
        _id(cell.column);_value(cell.value)
    return replace(row)


def _book(workbook,context):
    if type(workbook) is not core.Workbook or workbook.id!=context.workbook_id or type(workbook.sheets) is not tuple or not 1<=len(workbook.sheets)<=2:_deny()
    _id(workbook.id);_id(workbook.revision);total=0
    for sheet in workbook.sheets:
        if type(sheet) is not core.Sheet or type(sheet.rows) is not tuple:_deny()
        _id(sheet.id);_id(sheet.revision);total+=len(sheet.rows)
        if total>32:_deny()
        for row in sheet.rows:_row(row)
        replace(sheet)
    return replace(workbook)


def _nonces(values):
    if type(values) is not list or len(values)>256:_deny()
    for value in values:_id(value)
    if len(set(values))!=len(values):_deny()
    return frozenset(values)


def _header(meta,context):
    _closed(meta,{'format','version','namespace','used_nonces','keys'})
    if type(meta['keys']) is not list or len(meta['keys'])!=5 or set(meta['keys'])!=_KEYS:_deny()
    if meta['format']!=_FORMAT or type(meta['version']) is not int or meta['version']!=1:_deny()
    _closed(meta['namespace'],context.namespace)
    if meta['namespace']!=context.namespace:_deny()
    return _nonces(meta['used_nonces'])


def _decode(raw,context):
    state=json.loads(raw,object_pairs_hook=_pairs);_closed(state,_KEYS)
    used=_header(dict(format=state['format'],version=state['version'],namespace=state['namespace'],used_nonces=state['used_nonces'],keys=list(state)),context)
    book=_closed(state['workbook'],{'id','revision','sheets'})
    sheets=[];count=0
    if type(book['sheets']) is not list or not 1<=len(book['sheets'])<=2:_deny()
    for data in book['sheets']:
        data=_closed(data,{'id','revision','rows'})
        if type(data['rows']) is not list:_deny()
        count+=len(data['rows'])
        if count>32:_deny()
        rows=[]
        for record in data['rows']:
            record=_closed(record,{'position','revision','cells'})
            if type(record['cells']) is not list or not 1<=len(record['cells'])<=16:_deny()
            cells=[]
            for cell in record['cells']:
                if type(cell) is not list or len(cell)!=2:_deny()
                cells.append(core.Cell(_id(cell[0]),_scalar(cell[1])))
            rows.append(core.Row(record['position'],_id(record['revision']),tuple(cells)))
        sheets.append(core.Sheet(_id(data['id']),_id(data['revision']),tuple(rows)))
    workbook=core.Workbook(_id(book['id']),_id(book['revision']),tuple(sheets))
    return _book(workbook,context),used


def _encoded(workbook,used,context):
    _book(workbook,context);_nonces(sorted(used))
    state=dict(format=_FORMAT,version=1,namespace=context.namespace,workbook=dict(id=workbook.id,revision=workbook.revision,sheets=[dict(id=s.id,revision=s.revision,rows=[dict(position=r.position,revision=r.revision,cells=[[c.column,_value(c.value)] for c in r.cells]) for r in s.rows]) for s in workbook.sheets]),used_nonces=sorted(used))
    encoded=_json(state)
    if len(encoded.encode())>MAX_STATE_BYTES:_deny()
    return encoded


class SQLiteTableDriver:
    def __init__(self,*,store,current_context,enabled=False,allow_fake_writes=False):
        self._store=store;self._callback=current_context;self._enabled=enabled is True;self._writes=allow_fake_writes is True
        self._host=(store,getattr(store,'db',None),current_context);self._context=None;self._key=None

    def _current(self):
        s,db,callback=self._host
        if self._store is not s or self._store.db is not db or self._callback is not callback or type(s) is not Store:_deny()
        context=self._callback()
        if type(context) is not TableContext:_deny()
        for value in (*context.namespace.values(),context.required_approver,context.key_id):_id(value)
        if type(context.verification_key) is not bytes or not 32<=len(context.verification_key)<=128:_deny()
        if self._context is None:self._context=context;self._key=context.verification_key
        if context is not self._context or context.verification_key is not self._key:_deny()
        if self._store is not s or self._store.db is not db or self._callback is not callback:_deny()
        return context

    def _full(self,context):
        rows=self._store.execute("SELECT CASE WHEN typeof(value)='text' AND length(CAST(value AS BLOB))<=? THEN value END AS value FROM runtime WHERE key=? LIMIT 2",(MAX_STATE_BYTES,context.runtime_key)).fetchall()
        if len(rows)!=1 or type(rows[0][0]) is not str:_deny()
        return rows[0][0]

    def _metadata(self,context):
        rows=self._store.execute('''SELECT CASE WHEN typeof(value)='text' AND length(CAST(value AS BLOB))<=? THEN
            CASE WHEN json_valid(value) THEN CASE WHEN json_type(value,'$.format')='text' AND json_type(value,'$.version')='integer' AND json_type(value,'$.namespace')='object' AND json_type(value,'$.used_nonces')='array' AND json_type(value,'$.workbook') IS NOT NULL AND json_remove(value,'$.format','$.version','$.namespace','$.workbook','$.used_nonces')='{}' THEN json_object('format',json_extract(value,'$.format'),
            'version',json_extract(value,'$.version'),'namespace',json_extract(value,'$.namespace'),
            'used_nonces',json_extract(value,'$.used_nonces'),'keys',json('["format","version","namespace","workbook","used_nonces"]')) END END END
            FROM runtime WHERE key=? LIMIT 2''',(MAX_STATE_BYTES,context.runtime_key)).fetchall()
        if len(rows)!=1 or type(rows[0][0]) is not str:_deny()
        return _header(json.loads(rows[0][0],object_pairs_hook=_pairs),context)

    def snapshot(self):
        if not self._enabled:_deny()
        try:
            context=self._current();raw=self._full(context);book,used=_decode(raw,context)
            if self._current() is not context or self._full(context)!=raw:_deny()
            return book
        except TableDriverUnavailable:raise
        except Exception:raise TableDriverUnavailable() from None

    def build_plan(self,sheet_id,identity,desired):
        if not self._enabled:_deny()
        try:
            _id(sheet_id)
            if type(identity) is not core.RowIdentity or type(desired) is not tuple or not 1<=len(desired)<=8:_deny()
            _id(identity.column);_id(identity.value,spaces=True)
            for cell in desired:
                if type(cell) is not core.Cell:_deny()
                _id(cell.column);_value(cell.value)
            book=self.snapshot()
            return core.build_plan(book,workbook_id=book.id,sheet_id=sheet_id,identity=identity,desired=desired)
        except (TableDriverUnavailable,core.LookupError):raise
        except Exception:raise TableDriverUnavailable() from None

    def apply(self,plan,token,*,now,next_workbook_revision,next_sheet_revision,next_row_revision):
        if not self._enabled or not self._writes:_deny()
        try:
            context=self._current()
            if self._store.db.in_transaction:_deny()
            if type(plan) is not core.ChangePlan or type(plan.changes) is not tuple or not 1<=len(plan.changes)<=8:_deny()
            for value in (plan.workbook_id,plan.workbook_revision,plan.sheet_id,plan.sheet_revision,plan.identity.column):_id(value)
            _id(plan.identity.value,spaces=True);_row(plan.baseline)
            for change in plan.changes:
                if type(change) is not core.Change:_deny()
                _id(change.column);_value(change.before);_value(change.after)
            for value in (next_workbook_revision,next_sheet_revision,next_row_revision):_id(value)
            with self._store.transaction():
                if self._current() is not context:_deny()
                used=self._metadata(context)
                core.validate_approval(plan,token,required_approver=context.required_approver,now=now,signing_key=context.verification_key,used_nonces=used)
                raw=self._full(context);workbook,current_used=_decode(raw,context)
                if current_used!=used:_deny()
                result=core.apply_to_fake_workbook(workbook,plan,token,required_approver=context.required_approver,now=now,signing_key=context.verification_key,used_nonces=used,next_workbook_revision=next_workbook_revision,next_sheet_revision=next_sheet_revision,next_row_revision=next_row_revision)
                if type(result) is not core.ApplyResult or type(result.used_nonces) is not frozenset or result.used_nonces!=used|{token.nonce}:_deny()
                encoded=_encoded(result.workbook,result.used_nonces,context)
                if self._current() is not context:_deny()
                changed=self._store.execute('UPDATE runtime SET value=? WHERE key=? AND value=?',(encoded,context.runtime_key,raw)).rowcount
                if changed!=1:_deny()
                if self._current() is not context or self._full(context)!=encoded:_deny()
            return ApplyReceipt(result.workbook.id,plan.sheet_id,next_workbook_revision,next_sheet_revision,next_row_revision,core.plan_digest(plan),len(result.used_nonces))
        except (TableDriverUnavailable,core.ApprovalError,core.ConflictError):raise
        except Exception:raise TableDriverUnavailable() from None
