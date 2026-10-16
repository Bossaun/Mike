"""Pure synthetic customer-event rules. Every output is a draft or local flag.

No NLP, import, account lookup, clock, storage, runtime hook or send operation.
Confirmed structured inputs are an upstream contract, not verified here by an API.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import re

MAX_RECORDS = 1000


def _id(value):
    if type(value) is not str or not value or value != value.strip() or len(value) > 200:
        raise ValueError('Explicit bounded identifier required')


def _utc(value):
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError('Explicit timezone-aware timestamp required')
    return value.astimezone(timezone.utc)


def _bool(value, *, unknown=False):
    if type(value) is not bool and not (unknown and value is None):
        raise ValueError('Explicit boolean or permitted unknown required')


@dataclass(frozen=True)
class Conversation:
    customer_id: str
    channel: str
    thread_id: str
    source_id: str  # account/export scope, not an inferred shared identity

    def __post_init__(self):
        for value in (self.customer_id, self.channel, self.thread_id, self.source_id): _id(value)


def _scope(value):
    if type(value) is not Conversation: raise ValueError('Explicit Conversation required')


@dataclass(frozen=True)
class Window:
    start: datetime
    end: datetime

    def __post_init__(self):
        if _utc(self.start) >= _utc(self.end): raise ValueError('Positive explicit window required')

    def contains(self, at):
        return _utc(self.start) <= _utc(at) < _utc(self.end)


@dataclass(frozen=True)
class Event:
    id: str
    conversation: Conversation
    at: datetime
    evidence_id: str
    direction: str  # incoming / outgoing
    confirmed: bool  # actual observed message; False is not a send receipt
    reply_to: str | None = None

    def __post_init__(self):
        _id(self.id); _scope(self.conversation); _utc(self.at); _id(self.evidence_id); _bool(self.confirmed)
        if self.direction not in ('incoming', 'outgoing'): raise ValueError('Explicit message direction required')
        if self.reply_to is not None: _id(self.reply_to)


def _rows(values, cls):
    result = {}
    for index, value in enumerate(values):
        if index >= MAX_RECORDS or type(value) is not cls: raise ValueError('Invalid or excessive records')
        key = (value.conversation, value.id)
        if cls is Event:
            c = value.conversation
            key = (c.channel, c.source_id, c.thread_id, value.id)
        if key in result and result[key] != value: raise ValueError('Conflicting duplicate identity')
        result[key] = value
    return tuple(result.values())


def _events(values):
    rows = _rows(values, Event)
    result = {(row.conversation, row.id): row for row in rows}
    for row in rows:
        if row.reply_to is not None:
            parent = result.get((row.conversation, row.reply_to))
            # Incremental batches may omit an older parent. It cannot close
            # another event; a supplied parent must still be chronological.
            if parent is not None and _utc(parent.at) >= _utc(row.at):
                raise ValueError('Reply reference requires an earlier event in exact conversation')
    return result


@dataclass(frozen=True)
class BatchManifest:
    mode: str
    source_id: str
    channel: str
    cursor_before: str | None
    cursor_after: str | None
    events: tuple[Event, ...]


def normalize_batch(events, *, mode, source_id, channel, cursor_before, cursor_after):
    """Offline normalized-event/cursor manifest; archive is never replayed/imported."""
    if mode not in ('incremental', 'archive'): raise ValueError('Explicit batch mode required')
    _id(source_id); _id(channel)
    for cursor in (cursor_before, cursor_after):
        if cursor is not None: _id(cursor)
    rows = tuple(_events(events).values())
    if any(e.conversation.source_id != source_id or e.conversation.channel != channel for e in rows):
        raise ValueError('Batch crosses channel or source account')
    return BatchManifest(mode, source_id, channel, cursor_before, cursor_after, rows)


@dataclass(frozen=True)
class Candidate:
    id: str
    conversation: Conversation
    kind: str
    target_id: str
    owner_id: str | None
    due_at: datetime
    evidence_ids: tuple[str, ...]
    draft: str
    draft_only: bool = field(default=True, init=False)


@dataclass(frozen=True)
class Blocked:
    conversation: Conversation
    target_id: str
    reason: str


@dataclass(frozen=True)
class Preview:
    candidates: tuple[Candidate, ...]
    blocked: tuple[Blocked, ...]


@dataclass(frozen=True)
class Disposition:
    id: str
    conversation: Conversation
    candidate_id: str
    at: datetime
    state: str  # acknowledged / resolved
    evidence_id: str

    def __post_init__(self):
        _id(self.id); _scope(self.conversation); _id(self.candidate_id); _utc(self.at); _id(self.evidence_id)
        if self.state not in ('acknowledged', 'resolved'): raise ValueError('Explicit disposition required')


def _candidate(scope, kind, target, owner, due, evidence, draft):
    identity = [scope.customer_id, scope.channel, scope.thread_id, scope.source_id, kind, target]
    return Candidate(hashlib.sha256(json.dumps(identity).encode()).hexdigest(), scope, kind, target, owner, due, evidence, draft)


def _closed(candidate, dispositions, now, anchor):
    return any(row.conversation == candidate.conversation and row.candidate_id == candidate.id
               and _utc(anchor) <= _utc(row.at) <= now for row in dispositions)


def unanswered(events, *, now, window: Window, stale_after: timedelta, dispositions) -> Preview:
    """Latest unanswered incoming; unread state/text is never used as a heuristic."""
    now = _utc(now)
    if type(window) is not Window or type(stale_after) is not timedelta or not timedelta(0) < stale_after <= timedelta(days=36600):
        raise ValueError('Explicit bounded positive stale threshold/window required')
    rows = tuple(_events(events).values()); closed = _rows(dispositions, Disposition)
    candidates, blocked = [], []
    for scope in dict.fromkeys(row.conversation for row in rows):
        current = [e for e in rows if e.conversation == scope and _utc(e.at) <= now]
        pending = [e for e in current if e.direction == 'incoming' and e.confirmed and window.contains(e.at)
                   and not any(o.direction == 'outgoing' and o.confirmed and _utc(o.at) > _utc(e.at)
                               and (o.reply_to is None or o.reply_to == e.id) for o in current)]
        if not pending: continue
        latest = max(pending, key=lambda e: (_utc(e.at), e.id))
        if any(not e.confirmed and _utc(e.at) >= _utc(latest.at) for e in current):
            blocked.append(Blocked(scope, latest.id, 'unconfirmed_message_state')); continue
        if now - _utc(latest.at) < stale_after: continue
        try: due = _utc(latest.at) + stale_after
        except OverflowError: raise ValueError('Stale timestamp outside supported range') from None
        candidate = _candidate(scope, 'unanswered', latest.id, None, due, (latest.evidence_id,),
                               'Проверьте диалог: входящее сообщение без подтверждённого ответа.')
        if not _closed(candidate, closed, now, latest.at): candidates.append(candidate)
    return Preview(tuple(candidates), tuple(blocked))


@dataclass(frozen=True)
class Promise:
    id: str
    conversation: Conversation
    event_id: str
    owner_id: str
    due_at: datetime | None
    confirmed: bool

    def __post_init__(self):
        for value in (self.id, self.event_id, self.owner_id): _id(value)
        _scope(self.conversation); _bool(self.confirmed)
        if self.due_at is not None: _utc(self.due_at)


@dataclass(frozen=True)
class PromiseResult:
    id: str
    conversation: Conversation
    promise_id: str
    event_id: str
    confirmed: bool

    def __post_init__(self):
        for value in (self.id, self.promise_id, self.event_id): _id(value)
        _scope(self.conversation); _bool(self.confirmed)


def _anchor(events, scope, event_id, direction=None):
    event = events.get((scope, event_id))
    if event is None or (direction is not None and event.direction != direction):
        raise ValueError('Evidence event absent or outside exact conversation/direction')
    return event


def promise_preview(events, promises, results, *, now, dispositions) -> Preview:
    now = _utc(now); event_map = _events(events)
    promises = _rows(promises, Promise); results = _rows(results, PromiseResult); closed = _rows(dispositions, Disposition)
    by_id = {(p.conversation, p.id): p for p in promises}
    fulfilled = set()
    for result in results:
        promise = by_id.get((result.conversation, result.promise_id))
        if promise is None: raise ValueError('Result references an absent scoped promise')
        declared = _anchor(event_map, promise.conversation, promise.event_id, 'outgoing')
        event = _anchor(event_map, result.conversation, result.event_id, 'outgoing')
        if _utc(event.at) <= _utc(declared.at): raise ValueError('Promise result must follow declaration')
        if result.confirmed and event.confirmed and _utc(event.at) <= now: fulfilled.add((promise.conversation, promise.id))
    candidates, blocked = [], []
    for promise in promises:
        event = _anchor(event_map, promise.conversation, promise.event_id, 'outgoing')
        if _utc(event.at) > now: continue
        if not promise.confirmed or not event.confirmed or promise.due_at is None:
            blocked.append(Blocked(promise.conversation, promise.id, 'unconfirmed_promise_or_due')); continue
        if _utc(promise.due_at) < _utc(event.at): raise ValueError('Promise deadline predates declaration')
        if (promise.conversation, promise.id) in fulfilled or now < _utc(promise.due_at): continue
        candidate = _candidate(promise.conversation, 'promise', promise.id, promise.owner_id, promise.due_at,
                               (event.evidence_id,), 'Проверьте результат подтверждённого обещания клиенту.')
        if not _closed(candidate, closed, now, event.at): candidates.append(candidate)
    return Preview(tuple(candidates), tuple(blocked))


@dataclass(frozen=True)
class ContactPermission:
    id: str
    conversation: Conversation
    event_id: str
    granted: bool | None
    opt_out: bool
    window: Window | None

    def __post_init__(self):
        _id(self.id); _scope(self.conversation); _id(self.event_id); _bool(self.granted, unknown=True); _bool(self.opt_out)
        if self.window is not None and type(self.window) is not Window: raise ValueError('Explicit consent window required')
        if self.granted is True and self.opt_out: raise ValueError('Consent contradicts opt-out')


@dataclass(frozen=True)
class TouchPlan:
    id: str
    conversation: Conversation
    event_id: str
    owner_id: str
    permission_id: str
    readiness: bool | None
    window: Window
    draft: str

    def __post_init__(self):
        for value in (self.id, self.event_id, self.owner_id, self.permission_id): _id(value)
        _scope(self.conversation); _bool(self.readiness, unknown=True)
        if type(self.window) is not Window or type(self.draft) is not str or not self.draft.strip() or len(self.draft) > 2000:
            raise ValueError('Explicit touch window and bounded nonempty draft required')


@dataclass(frozen=True)
class TouchReceipt:
    id: str
    conversation: Conversation
    plan_id: str
    event_id: str
    confirmed: bool

    def __post_init__(self):
        for value in (self.id, self.plan_id, self.event_id): _id(value)
        _scope(self.conversation); _bool(self.confirmed)


def _touch_receipts(plans, receipts, events, now, *, before=None):
    by_id = {(p.conversation, p.id): p for p in plans}; done, uncertain, used = {}, set(), {}
    for receipt in receipts:
        key = (receipt.conversation, receipt.plan_id)
        if key not in by_id: raise ValueError('Receipt references absent scoped touch plan')
        plan = by_id[key]
        event = _anchor(events, receipt.conversation, receipt.event_id, 'outgoing')
        basis = _anchor(events, plan.conversation, plan.event_id)
        if _utc(event.at) <= _utc(basis.at) or not plan.window.contains(event.at):
            raise ValueError('Touch receipt outside chronological plan scope/window')
        event_key = (receipt.conversation, event.id)
        if event_key in used and used[event_key] != plan.id: raise ValueError('One message cannot count as two touches')
        used[event_key] = plan.id
        if _utc(event.at) <= now and (before is None or _utc(event.at) < before):
            if receipt.confirmed and event.confirmed: done[key] = event
            else: uncertain.add(key)
    return done, uncertain - set(done)


def touch_preview(events, plans, permissions, receipts, *, now, dispositions) -> Preview:
    now = _utc(now); event_map = _events(events)
    plans = _rows(plans, TouchPlan); permissions = _rows(permissions, ContactPermission)
    closed = _rows(dispositions, Disposition)
    done, uncertain = _touch_receipts(plans, _rows(receipts, TouchReceipt), event_map, now)
    latest = {}
    for permission in permissions:
        event = _anchor(event_map, permission.conversation, permission.event_id, 'incoming')
        if _utc(event.at) > now: continue
        previous = latest.get(permission.conversation)
        if previous and _utc(event.at) == _utc(previous[1].at) and permission != previous[0]:
            raise ValueError('Conflicting simultaneous consent observations')
        if previous is None or _utc(event.at) > _utc(previous[1].at): latest[permission.conversation] = (permission, event)
    candidates, blocked = [], []
    for plan in plans:
        event = _anchor(event_map, plan.conversation, plan.event_id)
        if _utc(plan.window.start) < _utc(event.at): raise ValueError('Plan window predates basis event')
        key = (plan.conversation, plan.id)
        if key in done: continue
        reason = None
        current = latest.get(plan.conversation)
        if not event.confirmed: reason = 'unconfirmed_basis'
        elif key in uncertain: reason = 'unknown_delivery'
        elif current is None: reason = 'unknown_consent'
        elif current[0].opt_out: reason = 'opt_out'
        elif not current[1].confirmed or current[0].granted is not True or current[0].window is None: reason = 'unknown_or_declined_consent'
        elif current[0].id != plan.permission_id: reason = 'stale_permission_reference'
        elif (not current[0].window.contains(now) or _utc(plan.window.start) < _utc(current[0].window.start)
              or _utc(plan.window.end) > _utc(current[0].window.end)): reason = 'outside_consent_window'
        elif plan.readiness is not True: reason = 'unknown_or_not_ready'
        elif not plan.window.contains(now): reason = 'outside_plan_window'
        if reason:
            blocked.append(Blocked(plan.conversation, plan.id, reason)); continue
        candidate = _candidate(plan.conversation, 'touch', plan.id, plan.owner_id, plan.window.start,
                               (event.evidence_id, current[1].evidence_id), plan.draft)
        if not _closed(candidate, closed, now, event.at): candidates.append(candidate)
    return Preview(tuple(candidates), tuple(blocked))


@dataclass(frozen=True)
class TouchSummary:
    planned_ids: tuple[str, ...]
    confirmed_ids: tuple[str, ...]
    pending_ids: tuple[str, ...]
    uncertain_ids: tuple[str, ...]


def reconcile_touches(events, plans, receipts, *, conversation, window: Window, now) -> TouchSummary:
    _scope(conversation)
    if type(window) is not Window: raise ValueError('Explicit reporting window required')
    now = min(_utc(now), _utc(window.end))
    event_map = _events(events); plans = _rows(plans, TouchPlan)
    done, uncertain = _touch_receipts(plans, _rows(receipts, TouchReceipt), event_map, now, before=_utc(window.end))
    selected = [p for p in plans if p.conversation == conversation and window.contains(p.window.start) and _utc(p.window.start) <= now]
    return TouchSummary(tuple(p.id for p in selected), tuple(p.id for p in selected if (p.conversation, p.id) in done),
        tuple(p.id for p in selected if (p.conversation, p.id) not in done and (p.conversation, p.id) not in uncertain),
        tuple(p.id for p in selected if (p.conversation, p.id) in uncertain))


@dataclass(frozen=True)
class ProductEvidence:
    id: str
    conversation: Conversation
    product_id: str
    available: bool | None
    price: Decimal | None
    currency: str | None
    delivery: str | None
    window: Window
    confirmed: bool
    source_id: str

    def __post_init__(self):
        for value in (self.id, self.product_id, self.source_id): _id(value)
        _scope(self.conversation); _bool(self.available, unknown=True); _bool(self.confirmed)
        if type(self.window) is not Window: raise ValueError('Explicit fact validity window required')
        if self.price is not None and (type(self.price) is not Decimal or not self.price.is_finite() or self.price < 0
                or len(self.price.as_tuple().digits) > 50 or abs(self.price.as_tuple().exponent) > 18): raise ValueError('Explicit bounded Decimal price required')
        if self.currency is not None and (type(self.currency) is not str or re.fullmatch('[A-Z]{3}', self.currency) is None): raise ValueError('Explicit currency required')
        if self.delivery is not None and (type(self.delivery) is not str or not self.delivery.strip() or len(self.delivery) > 400): raise ValueError('Bounded supplied delivery fact required')


@dataclass(frozen=True)
class ReplyDraft:
    conversation: Conversation
    product_id: str
    text: str
    missing: tuple[str, ...]
    source_ids: tuple[str, ...]
    draft_only: bool = field(default=True, init=False)


def product_draft(*, conversation, product_id, evidence: ProductEvidence, now) -> ReplyDraft:
    _scope(conversation); _id(product_id); now = _utc(now)
    if type(evidence) is not ProductEvidence or evidence.conversation != conversation or evidence.product_id != product_id:
        raise ValueError('Product evidence outside exact customer/product scope')
    valid = evidence.confirmed and evidence.window.contains(now)
    available = evidence.available if valid else None
    price = evidence.price if valid and evidence.currency is not None else None
    delivery = evidence.delivery if valid else None
    missing = tuple(name for name, value in (('availability', available), ('price', price), ('delivery', delivery)) if value is None)
    text = 'Наличие: ' + ('уточняется' if available is None else 'в наличии' if available else 'нет в наличии')
    text += '\nЦена: ' + ('уточняется' if price is None else f'{price} {evidence.currency}')
    text += '\nДоставка: ' + ('уточняется' if delivery is None else delivery)
    return ReplyDraft(conversation, product_id, text, missing, (evidence.source_id,) if valid else ())


@dataclass(frozen=True)
class PaymentEvidence:
    id: str
    conversation: Conversation
    invoice_id: str
    at: datetime
    status: str | None  # paid / unconfirmed, never inferred from an empty date
    payment_at: datetime | None
    reliable_accounting: bool
    source_id: str

    def __post_init__(self):
        for value in (self.id, self.invoice_id, self.source_id): _id(value)
        _scope(self.conversation); _utc(self.at); _bool(self.reliable_accounting)
        if self.status not in (None, 'paid', 'unconfirmed'): raise ValueError('Explicit supported accounting status required')
        if self.payment_at is not None: _utc(self.payment_at)


def payment_state(evidence: PaymentEvidence, *, now) -> str:
    """Local recorded status only. Never declares debt or confirms a payment."""
    if type(evidence) is not PaymentEvidence: raise ValueError('Explicit accounting evidence required')
    if _utc(evidence.at) > _utc(now) or not evidence.reliable_accounting or evidence.status is None: return 'unknown'
    if evidence.payment_at is not None and _utc(evidence.payment_at) > _utc(evidence.at): return 'unknown'
    return evidence.status + '_recorded'
