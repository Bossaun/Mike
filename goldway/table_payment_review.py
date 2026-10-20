"""Default-off one-invoice SYNTHETIC accounting-record review.

Source reliability/identity are host assertions, not verified accounting or
settlement. Uses public driver snapshots only; no financial/contact action.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from types import FunctionType, MethodType
from zoneinfo import ZoneInfo
import hashlib, json, math, re

from . import customer_rules as rules
from . import table_change_plan as tables
from .table_sqlite_driver import SQLiteTableDriver
from . import selected_knowledge_command as private


class PaymentReviewUnavailable(ValueError):
    def __init__(self):super().__init__('table_payment_review_unavailable')


def _deny():raise PaymentReviewUnavailable()


def _id(value):
    if (type(value) is not str or not 0 < len(value) <= 128 or len(value.encode()) > 512
            or value != value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value)):_deny()
    return value


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _aware(value):
    if (type(value) is not datetime or type(value.tzinfo) not in (timezone, ZoneInfo)
            or value.utcoffset() is None):_deny()
    result=value.astimezone(timezone.utc)
    if result.astimezone(value.tzinfo).replace(tzinfo=None) != value.replace(tzinfo=None):_deny()
    return result


def _instant(value):
    if (type(value) is not str or len(value) > 40 or re.fullmatch(
            r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})', value) is None):_deny()
    parsed=datetime.fromisoformat(value)
    if parsed.isoformat() != (value[:-1]+'+00:00' if value.endswith('Z') else value):_deny()
    return _aware(parsed)


def _optional(value):
    if value is None or type(value) is str and not value.strip():return None
    return value


def _method(value):
    if isinstance(value, MethodType):return value.__self__, value.__func__
    if isinstance(value, FunctionType):return None, value
    return value, getattr(type(value), '__call__', None)


class SourceRole(Enum):
    ACCOUNTING_PAYMENT_RECORD='accounting_payment_record'


@dataclass(frozen=True)
class AccountingSource:
    business: str
    workbook_id: str
    source_id: str
    account_scope: str
    role: SourceRole
    revision: int
    valid_from: datetime
    expires_at: datetime
    revoked: bool
    allowed_reviewer: str
    reliable_accounting: bool

    def __post_init__(self):
        for value in (self.business,self.workbook_id,self.source_id,self.account_scope,self.allowed_reviewer):_id(value)
        if (re.fullmatch('sqlite-book:[0-9a-f]{64}',self.workbook_id) is None
                or self.role is not SourceRole.ACCOUNTING_PAYMENT_RECORD
                or type(self.revision) is not int or not 1 <= self.revision < 2**53
                or type(self.revoked) is not bool or type(self.reliable_accounting) is not bool
                or _aware(self.valid_from) >= _aware(self.expires_at)):_deny()


def _source_data(value):
    if type(value) is not AccountingSource:_deny()
    value.__post_init__()
    return [value.business,value.workbook_id,value.source_id,value.account_scope,value.role.value,value.revision,
            _aware(value.valid_from).isoformat(),_aware(value.expires_at).isoformat(),value.revoked,
            value.allowed_reviewer,value.reliable_accounting]


def _conversation(value):
    if type(value) is not rules.Conversation:_deny()
    value.__post_init__()
    return [_id(getattr(value,k)) for k in ('customer_id','channel','thread_id','source_id')]


@dataclass(frozen=True)
class InvoiceBinding:
    conversation: rules.Conversation
    invoice_id: str
    account_scope: str
    sheet_id: str
    identity: tables.RowIdentity

    def __post_init__(self):
        _conversation(self.conversation)
        for value in (self.invoice_id,self.account_scope,self.sheet_id):_id(value)
        if type(self.identity) is not tables.RowIdentity:_deny()
        self.identity.__post_init__();_id(self.identity.column);_id(self.identity.value)
        if self.identity.value != self.invoice_id:_deny()


@dataclass(frozen=True)
class PaymentColumns:
    evidence_id: str
    invoice_id: str
    customer_id: str
    channel: str
    thread_id: str
    conversation_source_id: str
    observed_at: str
    status: str
    payment_at: str
    currency: str

    def __post_init__(self):
        names=[_id(getattr(self,k)) for k in self.__dataclass_fields__]
        if len(names)!=len(set(names)):_deny()


def _book_fingerprint(book):
    # I66 public snapshot is already bounded. All returned typed values/revisions
    # are sealed; unsupported profile shape is rejected, never projected away.
    if type(book) is not tables.Workbook or type(book.sheets) is not tuple or not 1<=len(book.sheets)<=2:_deny()
    book.__post_init__();_id(book.id);_id(book.revision);total=0;data=[]
    for sheet in book.sheets:
        if type(sheet) is not tables.Sheet or type(sheet.rows) is not tuple:_deny()
        sheet.__post_init__();_id(sheet.id);_id(sheet.revision);total+=len(sheet.rows)
        if total>32:_deny()
        rows=[]
        for row in sheet.rows:
            if type(row) is not tables.Row or type(row.cells) is not tuple or not 1<=len(row.cells)<=16:_deny()
            row.__post_init__();_id(row.revision)
            for cell in row.cells:
                if type(cell) is not tables.Cell:_deny()
                _id(cell.column)
            rows.append(tables._row_data(row))
        data.append([sheet.id,sheet.revision,rows])
    return _hash(['accounting-payment-full-workbook:v1',book.id,book.revision,data])


@dataclass(frozen=True)
class PaymentReview:
    evidence: rules.PaymentEvidence
    state: str
    date_missing: bool
    currency: str | None
    as_of: datetime
    window: rules.Window
    source: AccountingSource
    source_fingerprint: str
    workbook_fingerprint: str
    row_fingerprint: str
    input_fingerprint: str
    text: str
    scope: str=field(default='selected_invoice_record_only',init=False)
    requires_review: bool=field(default=True,init=False)
    live_authorized: bool=field(default=False,init=False)
    payment_confirmation_authorized: bool=field(default=False,init=False)
    debt_assessment_authorized: bool=field(default=False,init=False)
    customer_contact_authorized: bool=field(default=False,init=False)
    source_independently_verified: bool=field(default=False,init=False)
    currency_reconciled: bool=field(default=False,init=False)


def _render(evidence,state,currency,now):
    labels={'unknown':'По выбранной записи статус неизвестен.',
            'paid_recorded':'В выбранной записи указан статус «оплачено».',
            'unconfirmed_recorded':'В выбранной записи указан статус «не подтверждено».'}
    if state not in labels:_deny()
    body=('Сверка одной выбранной учётной записи. Требует ручной проверки.\n'
          +'По состоянию на '+now.isoformat()+'.\n'+labels[state]+'\n'
          +('Дата оплаты в записи не указана. Пустая дата сама по себе не означает неоплату.' if evidence.payment_at is None
            else 'Дата оплаты как записана: '+evidence.payment_at.isoformat()+'.')+'\n'
          +'Валюта записи: '+(currency if currency is not None else 'не указана')+'; сверка сумм не выполнялась.\n'
          +'Записанный статус не подтверждает поступление денег и не разрешает действий с клиентом. '
          +'Надёжность учётного источника этим просмотром независимо не проверяется.')
    if len(body.encode('utf-16-le'))//2>3500 or len(body.encode())>8000:_deny()
    return body


class TablePaymentReview:
    def __init__(self,*,engine,driver,current_source,expected_source,binding,columns,enabled=False):
        self._engine=engine;self._driver=driver;self._callback=current_source
        self._source=expected_source;self._binding=binding;self._columns=columns;self._enabled=enabled is True
        self._private=private._Host(engine)
        self._host=(engine,driver,_method(getattr(driver,'snapshot',None)),current_source,_method(current_source),
                    expected_source,binding,columns,rules.payment_state,rules.PaymentEvidence,tables.lookup_row)

    def _pure(self,request):
        engine,driver,snapshot,callback,callback_method,source,binding,columns,state,evidence,lookup=self._host
        if (not self._enabled or self._engine is not engine or self._driver is not driver or type(driver) is not SQLiteTableDriver
                or self._callback is not callback or not callable(callback) or self._source is not source
                or self._binding is not binding or type(binding) is not InvoiceBinding
                or self._columns is not columns or type(columns) is not PaymentColumns
                or rules.payment_state is not state or rules.PaymentEvidence is not evidence or tables.lookup_row is not lookup):_deny()
        for saved,current in ((snapshot,_method(driver.snapshot)),(callback_method,_method(callback))):
            if any(a is not b for a,b in zip(saved,current)):_deny()
        scope,parts,policy=self._private.policy(request)
        data=_source_data(source);binding.__post_init__();columns.__post_init__()
        if (source.business!=engine.b or source.allowed_reviewer!=request['sender']
                or source.account_scope!=binding.account_scope or binding.identity.column!=columns.invoice_id):_deny()
        inputs=[data,_conversation(binding.conversation),binding.invoice_id,binding.account_scope,binding.sheet_id,
                binding.identity.column,binding.identity.value,[getattr(columns,k) for k in columns.__dataclass_fields__]]
        return policy,_hash(['accounting-payment-binding:v1',inputs])

    def _current(self,request,initial,stamps):
        if self._pure(request)!=initial:_deny()
        seconds=self._private.sample(request,initial[0],stamps)
        clock=datetime.fromtimestamp(seconds,timezone.utc)
        current=self._callback()
        if (_source_data(current)!=_source_data(self._source) or self._source.revoked
                or not _aware(self._source.valid_from)<=clock<_aware(self._source.expires_at)
                or self._pure(request)!=initial):_deny()
        return clock

    def read_payment(self,scope_payload,*,window,now):
        if not self._enabled:_deny()
        try:
            if type(scope_payload) is not dict or set(scope_payload)!={'sender','channel','source_id'}:_deny()
            request=dict(scope_payload);initial=self._pure(request)
            if type(window) is not rules.Window:_deny()
            window.__post_init__();start,end=_aware(window.start),_aware(window.end);as_of=_aware(now)
            if not timedelta(0)<end-start<=timedelta(days=366):_deny()
            input_data=_hash([initial,start.isoformat(),end.isoformat(),as_of.isoformat(),request])
            def check():
                if (scope_payload!=request or self._pure(request)!=initial
                        or _hash([initial,_aware(window.start).isoformat(),_aware(window.end).isoformat(),_aware(now).isoformat(),request])!=input_data):_deny()
            stamps=[];first_clock=self._current(request,initial,stamps)
            if as_of>first_clock:_deny()
            check()
            book=self._driver.snapshot();check()
            if book.id!=self._source.workbook_id:_deny()
            fingerprint=_book_fingerprint(book)
            row=tables.lookup_row(book,workbook_id=self._source.workbook_id,sheet_id=self._binding.sheet_id,identity=self._binding.identity)
            if len(book.sheets)!=1 or book.sheets[0].id!=self._binding.sheet_id or len(book.sheets[0].rows)!=1:_deny()
            values={c.column:c.value for c in row.cells};names={getattr(self._columns,k) for k in self._columns.__dataclass_fields__}
            if set(values)!=names or len(row.cells)!=10:_deny()
            value={k:values[getattr(self._columns,k)] for k in self._columns.__dataclass_fields__}
            conv=self._binding.conversation
            if [value[k] for k in ('customer_id','channel','thread_id','conversation_source_id')]!=_conversation(conv) or value['invoice_id']!=self._binding.invoice_id:_deny()
            _id(value['evidence_id']);observed=_instant(value['observed_at'])
            if not start<=observed<end:_deny()
            status=_optional(value['status'])
            if status is not None and (type(status) is not str or status not in ('paid','unconfirmed')):_deny()
            raw_date=_optional(value['payment_at']);payment_at=None if raw_date is None else _instant(raw_date)
            currency=_optional(value['currency'])
            if currency is not None and (type(currency) is not str or re.fullmatch('[A-Z]{3}',currency) is None):_deny()
            row_fingerprint=_hash(['accounting-payment-row:v1',tables._row_data(row)])
            source_fingerprint=_hash(_source_data(self._source))
            ref='accounting-payment:'+_hash([source_fingerprint,fingerprint,row_fingerprint])
            evidence=rules.PaymentEvidence(value['evidence_id'],conv,self._binding.invoice_id,observed,status,payment_at,self._source.reliable_accounting,ref)
            check()
            self._current(request,initial,stamps)  # LAST external clock/source/permission calls.
            check()
            final=self._driver.snapshot()         # LAST actual data read.
            if _book_fingerprint(final)!=fingerprint: _deny()
            check()
            # Actual unchanged core and literal rendering only after final data.
            state=rules.payment_state(evidence,now=as_of)
            body=_render(evidence,state,currency,as_of)
            check()
            return PaymentReview(evidence,state,payment_at is None,currency,as_of,window,self._source,
                source_fingerprint,fingerprint,row_fingerprint,input_data,body)
        except PaymentReviewUnavailable:raise
        except Exception:raise PaymentReviewUnavailable() from None
