"""Default-off local review of one actual promotion calculation.

Host request/consent callbacks are assertions, not Telegram authentication.
Generated prose is unverified data. No provider, persistence or delivery hook.
"""
from dataclasses import dataclass, field
import hashlib
import json
import math
import sqlite3
from types import FunctionType, MethodType

from . import promotion_preview_command as promotion
from . import finance_metrics as finance
from .engine import Engine
from .store import Store
from .intelligence import Intelligence, intelligence_enabled
from .knowledge import KnowledgeIndex
from .intake import validate_team
from .answer_citations import _scope

PROFILE = 'promotion_scenario_v1'
SCOPE = 'supplied_scenario_only'
MAX_INPUT_CHARS = 2000
MAX_CODE_UNITS = 3500
MAX_CODE_BYTES = 8000
MAX_RECEIPT_BYTES = 16384
MAX_PROSE_UNITS = 1200
MAX_PROSE_BYTES = 4000
MAX_OUTPUT_UNITS = 3500
MAX_OUTPUT_BYTES = 8000
MAX_VALIDITY_SECONDS = 3600


class ExplanationUnavailable(ValueError):
    def __init__(self):
        super().__init__('promotion_explanation_unavailable')


def _deny():
    raise ExplanationUnavailable()


def _method(value):
    if isinstance(value, MethodType):
        return value.__self__, value.__func__
    if isinstance(value, FunctionType):
        return None, value
    return value, getattr(type(value), '__call__', None)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def _digest(value):
    return hashlib.sha256(_json(value)).hexdigest()


def _time(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 2**53-1:
        _deny()
    return value


def _text(value, units, byte_limit):
    if (type(value) is not str or not value.strip()
            or len(value.encode('utf-16-le')) // 2 > units
            or len(value.encode('utf-8')) > byte_limit
            or any((ord(c) < 32 and c != '\n') or ord(c) == 127 for c in value)):
        _deny()
    return value


@dataclass(frozen=True)
class ReviewRequest:
    business: str
    sender: str
    channel: str
    source_id: str
    form: str = field(repr=False)
    explanation_requested: bool
    valid_from: int | float
    expires_at: int | float


def _request_data(request):
    if type(request) is not ReviewRequest:
        _deny()
    for value, maximum in ((request.business, 64), (request.sender, 128),
                           (request.channel, 16), (request.source_id, 1024)):
        if type(value) is not str or not 0 < len(value.encode()) <= maximum:
            _deny()
    if (type(request.form) is not str or not 0 < len(request.form) <= MAX_INPUT_CHARS
            or len(request.form.encode()) > 8000 or request.explanation_requested is not True):
        _deny()
    start, end = _time(request.valid_from), _time(request.expires_at)
    if not 0 < end - start <= MAX_VALIDITY_SECONDS:
        _deny()
    return (request.business, request.sender, request.channel, request.source_id,
            request.form, request.explanation_requested, start, end)


@dataclass(frozen=True)
class CalculationReceipt:
    code_text: str = field(repr=False)
    digest: str
    kind: str = field(default=PROFILE, init=False)
    scope: str = field(default=SCOPE, init=False)


def _receipt(code_text):
    _text(code_text, MAX_CODE_UNITS, MAX_CODE_BYTES)
    digest = _digest(['goldway-promotion-explanation-v1', PROFILE, SCOPE, code_text])
    receipt = CalculationReceipt(code_text, digest)
    if len(_json(dict(kind=receipt.kind, scope=receipt.scope,
                      code_text=receipt.code_text, digest=receipt.digest))) > MAX_RECEIPT_BYTES:
        _deny()
    return receipt


@dataclass(frozen=True)
class ExplanationDraft:
    receipt_digest: str
    text: str = field(repr=False)


@dataclass(frozen=True)
class ExplanationReview:
    calculation: promotion.PromotionDraft
    receipt: CalculationReceipt
    explanation: ExplanationDraft
    text: str = field(repr=False)
    review_required: bool = field(default=True, init=False)
    explanation_verified: bool = field(default=False, init=False)
    delivery_authorized: bool = field(default=False, init=False)
    financial_action_authorized: bool = field(default=False, init=False)


_POLICY_KEYS = ('business', 'admins', 'employees', 'timezone', 'team_intake',
                'team_roster_verified', 'business_account_user_id',
                'allowed_live_recipients', 'live_send', 'recipients_verified',
                'review_mode', 'sheets_write', 'daily_summary',
                'allow_onboarding_replies', 'allow_registered_replies',
                'allowed_group_ids', 'group_destinations', 'meeting_recipient',
                'bot_user_id_verified', 'bot_user_id', 'intelligence')


class PromotionExplanationReview:
    def __init__(self, *, engine, current_request, explain, enabled=False):
        # Constructor captures references only; no clock/callback/SQL/init work.
        self._engine = engine
        self._current_request = current_request
        self._explain = explain
        self._enabled = enabled is True
        store = getattr(engine, 's', None)
        intelligence = getattr(engine, 'intelligence', None)
        self._host = (engine, store, getattr(store, 'db', None), getattr(engine, 'c', None),
                      getattr(engine, 'b', None), intelligence,
                      getattr(intelligence, 'index', None), getattr(engine, 'model', None),
                      _method(getattr(engine, 'now', None)),
                      current_request, _method(current_request), explain, _method(explain),
                      promotion.build_promotion_preview, promotion.preview_promotion,
                      finance.preview_promotion, promotion.PromotionDraft)

    def _bound(self):
        (engine, store, db, config, business, intelligence, index, model,
         clock, current_request, request_method, explain, explain_method,
         builder, core_alias, core, draft_type) = self._host
        if (not self._enabled or self._engine is not engine or type(engine) is not Engine
                or type(store) is not Store or engine.s is not store or store.db is not db
                or type(db) is not sqlite3.Connection
                or engine.c is not config or type(config) is not dict or engine.b != business
                or engine.intelligence is not intelligence or type(intelligence) is not Intelligence
                or intelligence.s is not store or intelligence.c is not config or intelligence.b != business
                or intelligence.index is not index or type(index) is not KnowledgeIndex or index.store is not store
                or engine.model is not model or self._current_request is not current_request
                or self._explain is not explain or not callable(current_request) or not callable(explain)
                or promotion.build_promotion_preview is not builder
                or promotion.preview_promotion is not core_alias or finance.preview_promotion is not core
                or promotion.PromotionDraft is not draft_type):
            _deny()
        # Connection liveness property only; no SQL or transaction ownership.
        db.in_transaction
        for saved, current in ((clock, _method(engine.now)),
                               (request_method, _method(current_request)),
                               (explain_method, _method(explain))):
            if any(left is not right for left, right in zip(saved, current)):
                _deny()

    def _policy(self, request):
        self._bound()
        engine, store, db, config, business = self._host[:5]
        validate_team(config)
        if (request.business != business or config.get('business') != business
                or config.get('admins') != [request.sender] or not intelligence_enabled(config)):
            _deny()
        payload = dict(sender=request.sender, channel=request.channel, source_id=request.source_id)
        scope, parts = _scope(config, payload)
        if parts[3] != str(config['employees'][request.sender]['telegram_user_id']):
            _deny()
        encoded = _json([scope, {key: config.get(key) for key in _POLICY_KEYS}])
        if len(encoded) > 65536:
            _deny()
        return hashlib.sha256(encoded).hexdigest()

    def review(self, request):
        if not self._enabled:
            _deny()
        try:
            data = _request_data(request)
            policy = self._policy(request)
            stamps = []

            def pure():
                if _request_data(request) != data or self._policy(request) != policy:
                    _deny()

            def current():
                # Clock first: a clock callback changing the current request is
                # observed by the subsequent host request callback, before data.
                pure()
                now = self._engine.now()
                pure()
                now = _time(now)
                if stamps and now < stamps[-1]:
                    _deny()
                stamps.append(now)
                observed = self._current_request()
                pure()
                if (_request_data(observed) != data
                        or not request.valid_from <= now < request.expires_at):
                    _deny()

            def build():
                pure()
                result = self._host[13](request.form)
                pure()
                if (type(result) is not self._host[16] or type(result.missing_fields) is not tuple
                        or result.missing_fields != ()):
                    _deny()
                _text(result.text, MAX_CODE_UNITS, MAX_CODE_BYTES)
                return result

            current()                         # No calculator before authority.
            initial = build()                 # Actual existing builder/core #1.
            original_text = initial.text
            receipt = _receipt(original_text)
            current()                         # Current consent before callback.
            answer = self._explain(receipt)    # Exactly one supplied callback.
            pure()
            if (type(answer) is not ExplanationDraft or type(answer.receipt_digest) is not str
                    or answer.receipt_digest != receipt.digest or receipt != _receipt(original_text)
                    or initial.text != original_text or initial.missing_fields != ()):
                _deny()
            prose = _text(answer.text, MAX_PROSE_UNITS, MAX_PROSE_BYTES)
            rendered = (original_text + '\n\nНепроверенное пояснение — требует вашей проверки.\n'
                        + prose + '\n\nЦифры выше рассчитаны кодом. Пояснение не проверено; '
                        'оно не разрешает финансовые действия или отправку.')
            _text(rendered, MAX_OUTPUT_UNITS, MAX_OUTPUT_BYTES)
            current()                         # LAST external request/clock calls.
            final = build()                   # Actual existing builder/core #2.
            # Pure comparisons only after the final data-producing operation.
            if (final.text != original_text or final != initial
                    or receipt != _receipt(original_text) or answer.text != prose
                    or answer.receipt_digest != receipt.digest):
                _deny()
            pure()
            return ExplanationReview(initial, receipt, answer, rendered)
        except ExplanationUnavailable:
            raise
        except Exception:
            raise ExplanationUnavailable() from None
