"""Root-only finite current-provider evidence probe; preparation is not execution.

No Telegram client, production Store/config, retries or model/profile changes.
Only root may execute in an already authorized server environment. Default is
description only. Production access is restricted to the existing usage ledger.
"""
import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
import time
from contextlib import contextmanager

HERE = Path(__file__).resolve().parent
BUSINESS = 'GOLDWAY'
MEMBERS = ['test_operator', 'frost', 'alen', 'nastya', 'dasha_it']
SYNTHETIC_CHAT = 1
QUESTIONS = (
    'Что такое GOLDWAY и какие три подтверждённых бренда представлены в публичном каталоге? '
    'Кратко перечисли их с явными номерами 1, 2, 3 и предложи следующим ответом пояснить '
    'каждый из этих же трёх брендов в том же порядке, также списком 1, 2, 3. '
    'Не создавай задач.',
    'Давай',
    'Расскажи о втором бренде',
    'Сделай покороче',
    'Откуда ты это знаешь?',
)
MAX_CALLS = 5
SUM = 'SELECT COALESCE(SUM(reserved_cost),0) FROM usage WHERE business=? AND at>=?'
INSERT = 'INSERT INTO usage(business,at,provider,input_tokens,output_tokens,reserved_cost) VALUES(?,?,?,?,?,?)'
UPDATE = 'UPDATE usage SET input_tokens=?,output_tokens=? WHERE id=?'


class Stop(ValueError):
    pass


def require(value, reason):
    if not value:
        raise Stop(reason)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def check_sources(root):
    manifest = json.loads((HERE / 'SOURCE_MANIFEST.json').read_text())
    actual = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted((root / 'goldway').glob('*.py'))}
    actual['examples/knowledge.public.json'] = hashlib.sha256(
        (root / 'examples/knowledge.public.json').read_bytes()).hexdigest()
    require(actual == manifest['files'], 'source_manifest_changed')
    return manifest


def profile_from_environment():
    # A root-supplied ALLOWLISTED model-only projection, never /data/config.json.
    raw = os.environ.get('GOLDWAY_PUBLIC_MODEL_PROFILE', '')
    require(0 < len(raw.encode()) <= 4096, 'missing_model_profile_projection')
    profile = json.loads(raw)
    required = {'provider', 'model', 'daily_budget', 'input_per_million',
                'output_per_million', 'max_output_tokens', 'max_context_chars'}
    optional = {'api', 'base_url', 'timeout_seconds', 'json_mode',
                'long_context_threshold', 'long_input_multiplier', 'long_output_multiplier'}
    require(type(profile) is dict and required <= set(profile) <= required | optional,
            'profile_not_allowlisted_model_only')
    require(all(type(profile[name]) in (int, float) and math.isfinite(profile[name])
                for name in ('daily_budget', 'input_per_million', 'output_per_million')),
            'profile_prices_not_finite_numbers')
    require(profile['provider'] == 'openai' and profile['model'] == 'gpt-4.1-mini'
            and profile['daily_budget'] == 1.0
            and profile['input_per_million'] == 0.4 and profile['output_per_million'] == 1.6,
            'existing_mini_one_dollar_profile_required')
    require(profile.get('base_url', 'https://api.openai.com') == 'https://api.openai.com',
            'unexpected_provider_endpoint')
    require(profile.get('api', 'chat_completions') in ('chat_completions', 'responses'),
            'unsupported_profile_api')
    require(type(profile['max_context_chars']) is int and 1 <= profile['max_context_chars'] <= 44000
            and type(profile['max_output_tokens']) is int and 1 <= profile['max_output_tokens'] <= 6000,
            'profile_limits_exceed_existing_public_ceiling')
    require(type(profile.get('timeout_seconds', 60)) in (int, float)
            and 0 < profile.get('timeout_seconds', 60) <= 180, 'invalid_profile_timeout')
    # Caller must copy these exact current values, not choose a larger/smaller
    # profile to make this probe pass. This harness never changes them.
    return profile


class UsageOnlyLedger:
    """No Store initialization/migration or production event/task/outbox read."""
    def __init__(self):
        self.db = sqlite3.connect('file:/data/team.sqlite?mode=rw', uri=True,
                                  isolation_level=None, timeout=5)
        self.owned = set()

    def execute(self, sql, args=()):
        if sql == SUM:
            require(len(args) == 2 and args[0] == BUSINESS, 'ledger_sum_scope')
        elif sql == INSERT:
            require(len(self.owned) < MAX_CALLS and len(args) == 6
                    and args[0] == BUSINESS and args[2] == 'openai'
                    and args[3:5] == (0, 0)
                    and type(args[5]) in (int, float) and math.isfinite(args[5])
                    and 0 <= args[5] <= 1, 'ledger_insert_scope')
        elif sql == UPDATE:
            require(len(args) == 3 and args[2] in self.owned
                    and all(type(v) is int and v >= 0 for v in args[:2]), 'ledger_update_scope')
        else:
            raise Stop('ledger_sql_outside_allowlist')
        cursor = self.db.execute(sql, args)
        if sql == INSERT:
            self.owned.add(cursor.lastrowid)
        return cursor

    @contextmanager
    def transaction(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            raise


class CaptureAPI:
    """Real Dispatcher metadata, synthetic delivery ONLY, no network API."""
    def __init__(self):
        self.sent = []

    def call(self, method, **params):
        require(method == 'sendMessage' and str(params.get('chat_id')) == str(SYNTHETIC_CHAT)
                and set(params) <= {'chat_id', 'text', 'parse_mode'}, 'unexpected_capture_route')
        require(type(params.get('text')) is str and 0 < len(params['text']) <= 3500,
                'invalid_capture_body')
        require(len(params['text'].encode('utf-16-le')) // 2 <= 3500, 'capture_utf16_limit')
        self.sent.append(copy.deepcopy(params))
        return {'message_id': 1000 + len(self.sent)}


class BoundedModel:
    def __init__(self, actual, profile):
        self.actual, self.profile = actual, digest(profile)
        self.calls = []

    def propose(self, context):
        require(len(self.calls) < MAX_CALLS, 'provider_call_cap')
        require(digest(self.actual.config) == self.profile, 'profile_changed')
        call = {'context': copy.deepcopy(context), 'result': None}
        self.calls.append(call)  # failed attempts count, no retry.
        result = self.actual.propose(context)
        require(type(result) is dict and result.get('actions') == []
                and result.get('intent', 'conversation') == 'conversation', 'nonconversation_provider_result')
        state = result.get('dialogue_state')
        require(type(state) is dict and state.get('task_id') is None
                and not (state.get('pending_offer') or {}).get('kind') == 'confirm_task',
                'task_authority_in_public_chain')
        call['result'] = copy.deepcopy(result)
        return result


def synthetic_config(root, profile):
    public = json.loads((root / 'examples/knowledge.public.json').read_text())['intelligence']
    require(len(public['sources']) == 2 and
            [(s['id'], s['kind'], s['url']) for s in public['sources']] == [
                ('goldway-public-site', 'website', 'https://goldwaybaltic.com'),
                ('goldway-public-catalog', 'public_catalog',
                 'https://gw-api.abdulwaheedaziz8.workers.dev?api=catalog')], 'public_registration_changed')
    for source in public['sources']:
        source['readers'] = ['test_operator']
    return dict(business=BUSINESS, timezone='Europe/Moscow',
        employees={key: dict(name=key, telegram_user_id=i + 1) for i, key in enumerate(MEMBERS)},
        admins=['test_operator'], business_account_user_id=99,
        team_intake=dict(recipients=MEMBERS[:], started_at=1), team_roster_verified=True,
        allowed_live_recipients=MEMBERS[:], recipients_verified=True,
        live_send=True, review_mode=True, sheets_write=False, daily_summary=False,
        allow_onboarding_replies=False, allow_registered_replies=False,
        allowed_group_ids=[], group_destinations={}, meeting_recipient=None,
        speaker_aliases={}, rules=[], availability='test_window', model=copy.deepcopy(profile),
        intelligence=public)


def run(root):
    check_sources(root)
    profile = profile_from_environment()
    key = os.environ.get('MODEL_API_KEY')
    require(type(key) is str and bool(key), 'existing_provider_environment_missing')
    sys.path.insert(0, str(root))
    from goldway.store import Store
    from goldway.engine import Engine
    from goldway.model import JsonModel
    from goldway.telegram import Dispatcher
    from goldway.answer_citations import _resolve
    from goldway.dialogue import scope_from_payload
    from goldway.ordinal_followup import select_numbered_item
    from goldway.intelligence import delivery_permitted

    store = Store(':memory:')
    ledger = UsageOnlyLedger()
    model = BoundedModel(JsonModel(ledger, BUSINESS, profile, key), profile)
    config = synthetic_config(root, profile)
    engine = Engine(store, config, model)
    api = CaptureAPI()
    dispatcher = Dispatcher(store, config, api)
    store.execute('INSERT INTO chats(business,recipient,bot_chat) VALUES(?,?,?)',
                  (BUSINESS, 'test_operator', str(SYNTHETIC_CHAT)))
    previous = None
    original_entities = None
    turn = 0
    emit(dict(status='started_not_accepted', events=5, call_limit=MAX_CALLS,
              synthetic_delivery=True, telegram_calls=0, profile_sha256=digest(profile)))
    try:
        for turn, question in enumerate(QUESTIONS, 1):
            before = len(model.calls)
            payload = dict(sender='test_operator', text=question, channel='bot',
                           source_id=f'telegram:{SYNTHETIC_CHAT}:{turn}')
            event = f'provider-chain:{turn}'
            require(engine.reply(event, 'test_operator', question, channel='bot',
                                 source_id=payload['source_id'], message_at=time.time()), 'event_rejected')
            engine.process(event)
            require(store.execute('SELECT state FROM events WHERE business=? AND id=?',
                                  (BUSINESS, event)).fetchone()[0] == 'done', 'event_not_done')
            rows = store.rows("SELECT * FROM outbox WHERE business=? AND state='pending'", (BUSINESS,))
            require(len(rows) == 1 and rows[0]['recipient'] == 'test_operator'
                    and rows[0]['task_id'] is None, 'unexpected_output_projection')
            if turn in (1, 2):
                require(len(model.calls) == before + 1, 'ordinary_turn_not_one_provider_call')
            if turn in (3, 4):
                require(len(model.calls) == before + 1, 'readonly_turn_not_one_provider_call')
                ctx = model.calls[-1]['context']
                require(ctx.get('rewrite_only') is True and ctx.get('tasks') == []
                        and ctx['capabilities'].get('task_mutations') is False,
                        'readonly_context_contract')
                wanted = selected.text if turn == 3 else previous['answer']
                require(ctx['followup']['target_answer'] == wanted, 'wrong_latest_answer_target')
            if turn == 5:
                require(len(model.calls) == before and previous['labels']
                        and all(label in rows[0]['text'] for label in previous['labels']),
                        'source_followup_not_saved_latest_authority')
            require(delivery_permitted(store, BUSINESS, 'test_operator', rows[0]['id'], time.time(), config),
                    'outbox_protection_denied')
            dispatcher.flush()
            actual = store.execute('SELECT * FROM outbox WHERE id=?', (rows[0]['id'],)).fetchone()
            receipts = store.rows('SELECT state,at FROM deliveries WHERE outbox_id=?', (actual['id'],))
            require(actual['state'] == 'sent' and actual['transport'] == 'bot'
                    and actual['destination_chat'] == str(SYNTHETIC_CHAT)
                    and len(receipts) == 1 and receipts[0]['state'] == 'sent'
                    and api.sent[-1]['text'] == actual['text'], 'capture_delivery_not_proven')
            require(not store.rows('SELECT id FROM tasks') and not store.rows('SELECT id FROM usage'),
                    'synthetic_domain_mutation')
            # This is permitted public/synthetic evidence, not a raw provider
            # payload. Emit delivery before semantic checks so a failed offer
            # remains inspectable without repeating the paid request.
            evidence = []
            if len(model.calls) > before:
                found = model.calls[-1]['context'].get('knowledge', {}).get('evidence', [])
                for item in found[:5]:
                    evidence.append({name: item.get(name) for name in ('id', 'title', 'url', 'text')})
            emit(dict(status='synthetic_delivery_observed', turn=turn, input=question,
                      provider_calls=len(model.calls) - before, actual_captured_delivery=actual['text'],
                      public_retrieved_evidence=evidence, telegram_delivery=False))
            scope = scope_from_payload(payload, BUSINESS)
            if turn < 5:
                resolved = _resolve(store, config, payload, time.time(), include_answer=True)
                require(resolved['answer'] == actual['text'] and resolved['citations']
                        and all(item['kind'] == 'document' for item in resolved['citations']),
                        'current_delivered_grounding_receipt_missing')
                memory = engine.intelligence.memory.get(scope,
                    readable_ids=engine.intelligence.readable_ids('test_operator', time.time()), now=time.time())
                require(memory['turns'][-1]['content'] == actual['text'], 'delivered_memory_mismatch')
                if turn == 1:
                    original_entities = memory['entities']
                    offer = memory['pending_offer']
                    require(type(offer) is dict and offer.get('kind') == 'explain'
                            and type(offer.get('text')) is str and offer['text'].strip()
                            and len(original_entities) == 3
                            and all(name in actual['text'] for name in original_entities),
                            'offer_or_three_delivered_entities_absent')
                if turn == 2:
                    hint = model.calls[-1]['context'].get('followup') or {}
                    require(hint.get('kind') == 'continue_last_turn'
                            and (hint.get('pending_offer') or {}).get('kind') == 'explain',
                            'bare_acceptance_did_not_use_offered_explanation')
                    chosen = [select_numbered_item(actual['text'], number) for number in (1, 2, 3)]
                    require(all(chosen) and all(item.count == 3 for item in chosen)
                            and [item.name for item in chosen] == original_entities,
                            'accepted_offer_not_same_strict_numbered_objects')
                    selected = chosen[1]
                if turn in (3, 4):
                    require(resolved['citations'] == previous['citations'], 'derivative_citation_authority_changed')
                if turn == 4:
                    def body(text):
                        return text.split('\n\nИсточники', 1)[0].strip()
                    require(0 < len(body(actual['text'])) < len(body(previous['answer'])),
                            'latest_answer_not_shortened')
                previous = resolved
            emit(dict(status='turn_structural_checks_complete', turn=turn,
                      grounding_refs=len(previous['citations']) if previous else 0))
        emit(dict(status='structural_chain_complete_semantic_review_required', events=turn,
                  provider_calls=len(model.calls), synthetic_deliveries=len(api.sent),
                  telegram_delivery=False, business_acceptance=False))
    except Exception as exc:
        # No raw HTTP/provider payload, environment/config, SQL data or traceback.
        emit(dict(status='stopped_not_accepted', turn=turn, provider_calls=len(model.calls),
                  safe_reason=str(exc) if type(exc) is Stop else 'engine_or_provider_failure',
                  error_type=type(exc).__name__, reservations_retained=True))
        return 1
    finally:
        ledger.db.close()
        store.db.close()
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    if not args.execute:
        emit(dict(status='prepared_not_executed', events=5, provider_calls=0,
                  required='root authorization + existing authenticated server execution path'))
        return 0
    require(os.environ.get('GOLDWAY_PROVIDER_CHAIN_AUTHORIZED') == 'root-approved-once',
            'root_execution_not_authorized')
    require(args.source_root is not None and args.source_root.is_dir(), 'source_root_required')
    return run(args.source_root.resolve())


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:
        emit(dict(status='preflight_stopped_not_accepted', provider_calls=0))
        sys.exit(2)
