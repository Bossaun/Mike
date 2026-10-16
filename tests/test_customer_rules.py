"""Synthetic customer rules: exact scopes, structured evidence and draft-only."""
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from goldway.customer_rules import (
    ContactPermission, Conversation, Disposition, Event, PaymentEvidence,
    ProductEvidence, Promise, PromiseResult, TouchPlan, TouchReceipt, Window,
    normalize_batch, payment_state, product_draft, promise_preview,
    reconcile_touches, touch_preview, unanswered,
)

UTC = timezone.utc
C = Conversation('customer-A', 'instagram', 'thread-A', 'synthetic-account')


def at(hour, minute=0):
    return datetime(2026, 10, 16, hour, minute, tzinfo=UTC)


WINDOW = Window(at(0), datetime(2026, 10, 17, tzinfo=UTC))


def event(id='in', *, scope=C, hour=9, direction='incoming', confirmed=True, reply_to=None):
    return Event(id, scope, at(hour), 'evidence-' + id, direction, confirmed, reply_to)


def pending(events, **changes):
    values = dict(now=at(12), window=WINDOW, stale_after=timedelta(hours=1), dispositions=())
    values.update(changes)
    return unanswered(events, **values)


def touch_inputs(**changes):
    permission = ContactPermission('consent', C, 'in', True, False, WINDOW)
    plan = TouchPlan('touch', C, 'in', 'manager-A', 'consent', True, Window(at(10), at(18)), 'Синтетический черновик уточнения.')
    values = dict(events=(event(),), plans=(plan,), permissions=(permission,), receipts=(), now=at(12), dispositions=())
    values.update(changes)
    return values


def test_normalized_incremental_and_archive_manifests_dedupe_without_import():
    original = event()
    for mode in ('incremental', 'archive'):
        result = normalize_batch([original, original], mode=mode, source_id=C.source_id,
            channel=C.channel, cursor_before=None, cursor_after='cursor-next')
        assert result.events == (original,) and result.mode == mode and result.cursor_after == 'cursor-next'
    orphan_reply = event('out', hour=10, direction='outgoing', reply_to='outside-this-incremental-batch')
    result = normalize_batch([orphan_reply], mode='incremental', source_id=C.source_id,
        channel=C.channel, cursor_before='previous', cursor_after=None)
    assert result.events == (orphan_reply,)


def test_duplicate_conflicts_and_source_crossing_rejected():
    with pytest.raises(ValueError): pending([event(), replace(event(), confirmed=False)])
    with pytest.raises(ValueError): pending([event(), replace(event(), conversation=replace(C, customer_id='other'))])
    with pytest.raises(ValueError): normalize_batch([event()], mode='archive', source_id='other',
        channel=C.channel, cursor_before=None, cursor_after=None)


def test_unanswered_threshold_reply_chronology_and_draft_vs_sent():
    incoming = event()
    assert not pending([incoming], now=at(9, 59)).candidates
    assert len(pending([incoming], now=at(10)).candidates) == 1
    assert not pending([incoming, event('out', hour=10, direction='outgoing')]).candidates
    assert len(pending([event('old-out', hour=8, direction='outgoing'), incoming]).candidates) == 1
    uncertain = pending([incoming, event('draft', hour=10, direction='outgoing', confirmed=False)])
    assert not uncertain.candidates and uncertain.blocked[0].reason == 'unconfirmed_message_state'
    same_instant = pending([incoming, event('out', hour=9, direction='outgoing')])
    assert len(same_instant.candidates) == 1


@pytest.mark.parametrize('scope', [replace(C, customer_id='other', thread_id='other'), replace(C, channel='telegram'),
                                  replace(C, thread_id='other'), replace(C, source_id='another-account')])
def test_outgoing_cannot_close_another_customer_channel_thread_or_source(scope):
    result = pending([event(), event('out', scope=scope, hour=10, direction='outgoing', reply_to='in')])
    assert len(result.candidates) == 1 and result.candidates[0].conversation == C


def test_explicit_reply_to_old_incoming_does_not_close_newer_incoming():
    result = pending([event(), event('new', hour=10), event('out', hour=11, direction='outgoing', reply_to='in')])
    assert len(result.candidates) == 1 and result.candidates[0].target_id == 'new'
    incoming_reply = event('reply', hour=11, reply_to='out')
    assert pending([event(), event('out', hour=10, direction='outgoing'), incoming_reply]).candidates[0].target_id == 'reply'


@pytest.mark.parametrize('state', ['acknowledged', 'resolved'])
def test_acknowledgement_stably_suppresses_only_exact_candidate_scope(state):
    candidate = pending([event()]).candidates[0]
    ack = Disposition('ack', C, candidate.id, at(11), state, 'ack-source')
    assert not pending([event()], dispositions=(ack,)).candidates
    wrong = replace(ack, conversation=replace(C, source_id='other-account'))
    assert pending([event()], dispositions=(wrong,)).candidates[0].id == candidate.id
    future = replace(ack, at=at(13))
    assert pending([event()], dispositions=(future,)).candidates
    new = pending([event(), event('new', hour=10)], dispositions=(ack,))
    assert new.candidates[0].target_id == 'new' and new.candidates[0].id != candidate.id


def promise_inputs(**changes):
    declaration = event('promise-message', hour=9, direction='outgoing')
    promise = Promise('promise', C, declaration.id, 'manager-A', at(11), True)
    values = dict(events=(declaration,), promises=(promise,), results=(), now=at(12), dispositions=())
    values.update(changes)
    return values


def test_promises_are_explicit_and_only_matched_result_evidence_resolves_them():
    values = promise_inputs()
    candidate = promise_preview(**values).candidates[0]
    assert candidate.kind == 'promise' and candidate.owner_id == 'manager-A' and candidate.draft_only is True
    response = event('result', hour=10, direction='outgoing')
    assert promise_preview(**{**values, 'events': (*values['events'], response)}).candidates
    result = PromiseResult('result-record', C, 'promise', response.id, True)
    assert not promise_preview(**{**values, 'events': (*values['events'], response), 'results': (result,)}).candidates
    assert promise_preview(**{**values, 'events': (*values['events'], response), 'results': (replace(result, confirmed=False),)}).candidates
    ack = Disposition('ack', C, candidate.id, at(11), 'resolved', 'ack-source')
    assert not promise_preview(**{**values, 'dispositions': (ack,)}).candidates


@pytest.mark.parametrize('change', ['unknown_due', 'unconfirmed', 'cross_thread', 'earlier_result'])
def test_promise_unknown_and_wrong_source_cannot_be_fabricated_as_completed(change):
    values = promise_inputs(); promise = values['promises'][0]
    if change == 'unknown_due':
        result = promise_preview(**{**values, 'promises': (replace(promise, due_at=None),)})
        assert not result.candidates and result.blocked
    elif change == 'unconfirmed':
        result = promise_preview(**{**values, 'promises': (replace(promise, confirmed=False),)})
        assert not result.candidates and result.blocked
    else:
        result_event = event('result', scope=replace(C, thread_id='other') if change == 'cross_thread' else C,
                             hour=10 if change == 'cross_thread' else 8, direction='outgoing')
        result = PromiseResult('r', C, promise.id, 'result', True)
        with pytest.raises(ValueError): promise_preview(**{**values, 'events': (*values['events'], result_event), 'results': (result,)})


def test_touch_preview_is_candidate_only_with_explicit_consent_readiness_window():
    values = touch_inputs()
    candidate, = touch_preview(**values).candidates
    assert candidate.draft_only is True and candidate.draft == values['plans'][0].draft
    assert candidate.evidence_ids == ('evidence-in', 'evidence-in')
    with pytest.raises(FrozenInstanceError): candidate.draft_only = False
    ack = Disposition('ack', C, candidate.id, at(11), 'acknowledged', 'ack-source')
    assert not touch_preview(**{**values, 'dispositions': (ack,)}).candidates


@pytest.mark.parametrize('change', ['ready_unknown', 'not_ready', 'consent_unknown', 'no_consent', 'declined',
                                   'unknown_window', 'expired', 'plan_expired', 'stale_id', 'unconfirmed_source'])
def test_readiness_never_substitutes_for_consent_or_valid_window(change):
    values = touch_inputs(); plan = values['plans'][0]; permission = values['permissions'][0]
    if change == 'ready_unknown': plan = replace(plan, readiness=None)
    if change == 'not_ready': plan = replace(plan, readiness=False)
    if change == 'consent_unknown': permission = replace(permission, granted=None)
    if change == 'no_consent': values['permissions'] = ()
    if change == 'declined': permission = replace(permission, granted=False)
    if change == 'unknown_window': permission = replace(permission, window=None)
    if change == 'expired': permission = replace(permission, window=Window(at(8), at(11)))
    if change == 'plan_expired': plan = replace(plan, window=Window(at(10), at(11)))
    if change == 'stale_id': plan = replace(plan, permission_id='old-id')
    if change == 'unconfirmed_source': values['events'] = (replace(values['events'][0], confirmed=False),)
    values['plans'] = (plan,)
    if change != 'no_consent': values['permissions'] = (permission,)
    result = touch_preview(**values)
    assert not result.candidates and len(result.blocked) == 1


def test_new_optout_or_expired_latest_consent_never_falls_back_to_older_grant():
    values = touch_inputs()
    message = event('optout', hour=11)
    stop = ContactPermission('stop', C, message.id, False, True, None)
    result = touch_preview(**{**values, 'events': (*values['events'], message), 'permissions': (*values['permissions'], stop)})
    assert not result.candidates and result.blocked[0].reason == 'opt_out'
    expired = replace(stop, opt_out=False, granted=True, window=Window(at(10), at(11)))
    result = touch_preview(**{**values, 'events': (*values['events'], message), 'permissions': (*values['permissions'], expired)})
    assert not result.candidates


def test_actual_receipt_resolves_touch_unknown_receipt_never_authorizes_repeat():
    values = touch_inputs()
    sent = event('sent', hour=11, direction='outgoing')
    receipt = TouchReceipt('receipt', C, 'touch', sent.id, True)
    completed = {**values, 'events': (*values['events'], sent), 'receipts': (receipt,)}
    assert not touch_preview(**completed).candidates
    summary = reconcile_touches(completed['events'], completed['plans'], completed['receipts'], conversation=C, window=WINDOW, now=at(12))
    assert summary.planned_ids == summary.confirmed_ids == ('touch',) and summary.pending_ids == ()
    unknown = {**completed, 'receipts': (replace(receipt, confirmed=False),)}
    result = touch_preview(**unknown)
    assert not result.candidates and result.blocked[0].reason == 'unknown_delivery'
    summary = reconcile_touches(unknown['events'], unknown['plans'], unknown['receipts'], conversation=C, window=WINDOW, now=at(12))
    assert summary.confirmed_ids == () and summary.uncertain_ids == ('touch',)
    draft_summary = reconcile_touches(values['events'], values['plans'], (), conversation=C, window=WINDOW, now=at(12))
    assert draft_summary.confirmed_ids == () and draft_summary.pending_ids == ('touch',)


def test_cross_customer_receipt_and_reused_send_cannot_count_as_completed_touch():
    values = touch_inputs(); sent = event('sent', hour=11, direction='outgoing')
    other = replace(C, customer_id='other', thread_id='other')
    with pytest.raises(ValueError): touch_preview(**{**values, 'events': (*values['events'], sent),
        'receipts': (TouchReceipt('r', other, 'touch', sent.id, True),)})
    second = replace(values['plans'][0], id='second')
    receipts = (TouchReceipt('one', C, 'touch', sent.id, True), TouchReceipt('two', C, 'second', sent.id, True))
    with pytest.raises(ValueError): touch_preview(**{**values, 'events': (*values['events'], sent),
        'plans': (*values['plans'], second), 'receipts': receipts})


@pytest.mark.parametrize('confirmed', [True, False])
def test_reconciliation_window_end_is_exclusive_for_confirmed_and_unknown_receipts(confirmed):
    values = touch_inputs()
    sent = event('boundary-send', hour=12, direction='outgoing')
    receipt = TouchReceipt('receipt', C, 'touch', sent.id, confirmed)
    summary = reconcile_touches((*values['events'], sent), values['plans'], (receipt,), conversation=C,
        window=Window(at(10), at(12)), now=at(13))
    assert summary.planned_ids == summary.pending_ids == ('touch',)
    assert summary.confirmed_ids == summary.uncertain_ids == ()


def test_product_draft_uses_only_exact_current_confirmed_facts_and_keeps_zero_price():
    evidence = ProductEvidence('facts', C, 'SKU-blue', False, Decimal('0'), 'RUB', 'Уточнённая доставка 2 дня', WINDOW, True, 'catalog-source')
    result = product_draft(conversation=C, product_id='SKU-blue', evidence=evidence, now=at(12))
    assert result.draft_only is True and 'нет в наличии' in result.text and '0 RUB' in result.text and result.missing == ()
    unknown = product_draft(conversation=C, product_id='SKU-blue', evidence=replace(evidence, confirmed=False), now=at(12))
    assert unknown.missing == ('availability', 'price', 'delivery') and unknown.source_ids == ()
    expired = product_draft(conversation=C, product_id='SKU-blue', evidence=replace(evidence, window=Window(at(1), at(2))), now=at(12))
    assert expired.missing == unknown.missing
    for scope, product in [(replace(C, customer_id='other'), 'SKU-blue'), (C, 'SKU-red')]:
        with pytest.raises(ValueError): product_draft(conversation=scope, product_id=product, evidence=evidence, now=at(12))


@pytest.mark.parametrize('status,reliable,payment_at,expected', [
    (None, True, None, 'unknown'), (None, True, at(9), 'unknown'),
    ('paid', False, None, 'unknown'), ('unconfirmed', False, None, 'unknown'),
    ('unconfirmed', True, None, 'unconfirmed_recorded'), ('paid', True, None, 'paid_recorded'),
    ('paid', True, at(13), 'unknown'),
])
def test_empty_date_never_declares_debt_or_guesses_payment(status, reliable, payment_at, expected):
    evidence = PaymentEvidence('p', C, 'invoice-A', at(10), status, payment_at, reliable, 'accounting-source')
    assert payment_state(evidence, now=at(12)) == expected


def test_input_bounds_aware_time_and_exact_evidence_guards():
    with pytest.raises(ValueError): replace(event(), at=datetime(2026, 10, 16))
    with pytest.raises(ValueError): pending([event()], stale_after=timedelta(0))
    with pytest.raises(ValueError): pending([event()], stale_after=timedelta(days=36601))
    with pytest.raises(ValueError): ProductEvidence('facts', C, 'SKU', True, Decimal('1E+1000000000'), 'RUB', None, WINDOW, True, 'source')
    consumed = []
    def many():
        for number in range(1002):
            consumed.append(number); yield event()
    with pytest.raises(ValueError): pending(many())
    assert len(consumed) == 1001
    with pytest.raises(ValueError): Promise('p', C, 'message', 'owner', datetime(2026, 10, 16), True)


def test_pure_rules_do_not_read_files_network_clock_or_mutate_inputs(monkeypatch):
    import builtins
    import socket
    import time
    values = touch_inputs()
    def forbidden(*args, **kwargs): pytest.fail('Pure rules attempted an external operation')
    monkeypatch.setattr(builtins, 'open', forbidden); monkeypatch.setattr(socket, 'socket', forbidden); monkeypatch.setattr(time, 'time', forbidden)
    assert touch_preview(**values).candidates[0].draft_only is True
    assert pending([event()]).candidates[0].draft_only is True
    assert promise_preview(**promise_inputs()).candidates[0].draft_only is True
    with pytest.raises(FrozenInstanceError): values['plans'][0].readiness = False
