"""Default-disabled literal table provider. Host policy alone owns source authority."""
from dataclasses import dataclass
from enum import Enum
import hashlib,json,re,math
from .table_sqlite_driver import SQLiteTableDriver
from . import table_change_plan as tables
from .trusted_provider import SourceSpec,SourceRequest,Document,ProviderSnapshot

class TableKnowledgeUnavailable(ValueError):
    def __init__(self):super().__init__('table_knowledge_provider_unavailable')
def _deny():raise TableKnowledgeUnavailable()

def _id(value):
    if type(value) is not str or not 0<len(value)<=128 or value!=value.strip() or len(value.encode())>512 or any(ord(c)<32 or ord(c)==127 for c in value):_deny()
    return value

def _hash(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

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
    return _hash(['table-knowledge-full-workbook:v1',book.id,book.revision,data])


def _label(v):
    if type(v) is not str or re.fullmatch('[A-Za-z0-9._-]{1,64}',v) is None:_deny()
    return v
def _integer(v,minimum=0):
    if type(v) is not int or not minimum<=v<2**53:_deny()
    return v
def _time(v):
    if type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<2**53:_deny()
    return v
class SourceRole(Enum):KNOWLEDGE_DOCUMENT_SAMPLE='knowledge_document_sample'
@dataclass(frozen=True)
class KnowledgeSource:
    business:str;workbook_id:str;provider_id:str;source_id:str;role:SourceRole;revision:int;valid_from:int;expires_at:int;revoked:bool=False
    def __post_init__(self):
        for v in (self.business,self.provider_id,self.source_id):_label(v)
        _id(self.workbook_id);_integer(self.revision,1);_integer(self.valid_from);_integer(self.expires_at)
        if re.fullmatch('sqlite-book:[0-9a-f]{64}',self.workbook_id) is None or self.role is not SourceRole.KNOWLEDGE_DOCUMENT_SAMPLE or self.valid_from>=self.expires_at or type(self.revoked) is not bool:_deny()
def _schema(s):
    names=tuple(getattr(s,k) for k in s.__dataclass_fields__)
    for n in names:_id(n)
    if len(names)!=len(set(names)):_deny()
@dataclass(frozen=True)
class HeadColumns:
    provider_id:str;source_id:str;version:str;observed_at:str
    def __post_init__(self):_schema(self)
@dataclass(frozen=True)
class DocumentColumns:
    id:str;present:str;title:str;text:str
    def __post_init__(self):_schema(self)
@dataclass(frozen=True)
class KnowledgeColumns:
    row_id:str;kind:str;head:HeadColumns;document:DocumentColumns
    def __post_init__(self):
        _id(self.row_id);_id(self.kind)
        if self.row_id==self.kind:_deny()
        for s,cls in ((self.head,HeadColumns),(self.document,DocumentColumns)):
            if type(s) is not cls:_deny()
            s.__post_init__()
            if {self.row_id,self.kind}&{getattr(s,k) for k in s.__dataclass_fields__}:_deny()
def _source_data(s):
    if type(s) is not KnowledgeSource:_deny()
    s.__post_init__()
    return [s.business,s.workbook_id,s.provider_id,s.source_id,s.role.value,s.revision,s.valid_from,s.expires_at,s.revoked]
def _policy(s):
    if type(s) is not SourceSpec:_deny()
    s.__post_init__();_integer(s.minimum_version,1)
    return [s.provider_id,s.source_id,sorted(s.audience),s.minimum_version,s.ttl_seconds,s.confirmed]
def _content(v,chars,bytecap):
    if type(v) is not str or not v.strip() or len(v)>chars or len(v.encode('utf-8',errors='strict'))>bytecap:_deny()
    return v

class TableKnowledgeProvider:
    def __init__(self,*,driver,current_source,expected_source,source_spec,credential,clock,sheet_id,columns,enabled=False):
        try:
            if type(driver) is not SQLiteTableDriver or not callable(current_source) or not callable(clock) or type(columns) is not KnowledgeColumns:_deny()
            data=_source_data(expected_source);policy=_policy(source_spec);_id(sheet_id);columns.__post_init__()
            if data[2:4]!=policy[:2]:_deny()
            method=getattr(driver,'snapshot',None)
            if getattr(method,'__self__',None) is not driver or not callable(getattr(method,'__func__',None)):_deny()
            self._driver=driver;self._callback=current_source;self._source=expected_source;self._spec=source_spec;self._credential=credential;self._clock=clock;self._sheet=sheet_id;self._columns=columns;self._enabled=enabled is True
            self._host=(driver,current_source,expected_source,source_spec,credential,clock,sheet_id,columns,method.__self__,method.__func__)
            self._seal=self._pure()
        except TableKnowledgeUnavailable:raise
        except Exception:raise TableKnowledgeUnavailable() from None
    def _pure(self):
        driver,callback,source,spec,credential,clock,sheet,columns,owner,function=self._host
        method=getattr(driver,'snapshot',None)
        if self._driver is not driver or self._callback is not callback or self._source is not source or self._spec is not spec or self._credential is not credential or self._clock is not clock or self._sheet is not sheet or self._columns is not columns or getattr(method,'__self__',None) is not owner or getattr(method,'__func__',None) is not function:_deny()
        data=_source_data(source);policy=_policy(spec);columns.__post_init__();_id(sheet)
        if data[2:4]!=policy[:2]:_deny()
        return _hash(['table-knowledge-policy:v1',data,policy,sheet,columns.row_id,columns.kind,[[getattr(s,k) for k in s.__dataclass_fields__] for s in (columns.head,columns.document)]])
    def _preflight(self,request,credential):
        if not self._enabled or credential is not self._host[4]:_deny()
        if self._pure()!=self._seal or type(request) is not SourceRequest:_deny()
        _label(request.provider_id);_label(request.source_id);_integer(request.minimum_version,1)
        if request.known_version is not None:_integer(request.known_version,1)
        if (request.provider_id,request.source_id,request.minimum_version)!=(self._spec.provider_id,self._spec.source_id,self._spec.minimum_version):_deny()
        return (request.provider_id,request.source_id,request.minimum_version,request.known_version)
    def _current(self):
        if self._pure()!=self._seal:_deny()
        now=_time(self._clock());current=self._callback()
        if _source_data(current)!=_source_data(self._source) or self._source.revoked or not self._source.valid_from<=now<self._source.expires_at or self._pure()!=self._seal:_deny()
        return now
    def fetch(self,request,credential):
        try:
            inputs=self._preflight(request,credential);first=self._current();book=self._driver.snapshot()
            if self._preflight(request,credential)!=inputs or self._pure()!=self._seal or type(book) is not tables.Workbook or book.id!=self._source.workbook_id:_deny()
            fingerprint=_book_fingerprint(book);sheets=[s for s in book.sheets if s.id==self._sheet]
            if len(sheets)!=1 or not 1<=len(sheets[0].rows)<=5:_deny()
            ids=set();docids=set();heads=[];docs=[];textbytes=0
            for row in sheets[0].rows:
                cells={c.column:c.value for c in row.cells};technical=_id(cells.get(self._columns.row_id));kind=cells.get(self._columns.kind)
                if technical in ids or type(kind) is not str or kind not in ('head','document'):_deny()
                ids.add(technical);schema=getattr(self._columns,kind);names={getattr(schema,k) for k in schema.__dataclass_fields__}|{self._columns.row_id,self._columns.kind}
                if set(cells)!=names or len(row.cells)!=len(names):_deny()
                selected=tables.lookup_row(book,workbook_id=self._source.workbook_id,sheet_id=self._sheet,identity=tables.RowIdentity(self._columns.row_id,technical))
                if tables._row_data(selected)!=tables._row_data(row):_deny()
                d={k:cells[getattr(schema,k)] for k in schema.__dataclass_fields__}
                if kind=='head':
                    if d['provider_id']!=self._spec.provider_id or d['source_id']!=self._spec.source_id:_deny()
                    version=_integer(d['version'],1);observed=_integer(d['observed_at'])
                    if version<max(request.minimum_version,request.known_version or request.minimum_version):_deny()
                    heads.append((version,observed))
                else:
                    identifier=_label(d['id'])
                    if identifier in docids or len(docids)>=4 or type(d['present']) is not bool:_deny()
                    docids.add(identifier)
                    if d['present']:
                        title=_content(d['title'],150,600);text=_content(d['text'],4096,16384);textbytes+=len(text.encode('utf-8'));docs.append(Document(identifier,title,text))
                    elif d['title'] is not None or d['text'] is not None:_deny()
            if len(heads)!=1 or textbytes>65536:_deny()
            encoded=json.dumps([[d.id,d.title,d.text] for d in docs],ensure_ascii=False,separators=(',',':')).encode('utf-8')
            if len(encoded)>128000:_deny()
            version,observed=heads[0];snapshot=ProviderSnapshot(self._spec.provider_id,self._spec.source_id,version,observed,tuple(docs))
            if _book_fingerprint(self._driver.snapshot())!=fingerprint:_deny()
            final=self._current()
            if final<first or self._preflight(request,credential)!=inputs:_deny()
            return snapshot
        except TableKnowledgeUnavailable:raise
        except Exception:raise TableKnowledgeUnavailable() from None
