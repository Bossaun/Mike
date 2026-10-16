"""Read-only transformation of the exact prior delivered substantive answer.

Uses prospective I14 metadata and existing outbox text; no historical fallback,
source retrieval, copied raw text store, mutation authority, or additional call.
"""
from contextlib import nullcontext
import json

from .answer_citations import _scope, _hash, _resolve, capture_answer
from .dialogue_focus import _request, _SHORTEN, _CONTINUE, build_followup
from .monthly_task_history import _Reader

_UNAVAILABLE=('Не могу однозначно выбрать предыдущий доставленный ответ для переработки. '
              'Пришлите нужный текст ещё раз или дождитесь ответа на предыдущий запрос.')


def is_rewrite_request(text):
    request=_request(text)
    # Bare yes/task confirmation and ordinal entity selection stay on their
    # existing routes; no unproven entity order is imported by this package.
    return bool(request and (_SHORTEN.fullmatch(request) or _CONTINUE.fullmatch(request)))


def _deny():raise ValueError('answer_rewrite_unavailable')


def _gate(engine,payload):
    if (engine.c.get('business')!=engine.b or engine.intelligence is None
            or engine.intelligence.b!=engine.b or engine.intelligence.s is not engine.s
            or engine.intelligence.c is not engine.c):_deny()
    return _scope(engine.c,payload)


def _target(engine,event_id,payload,reader):
    scope,_=_gate(engine,payload)
    args=(engine.b,_hash(scope))
    # Only the current fresh request's barrier can select its immediate prior
    # row. Never query an older recorded/delivered row after a missing barrier.
    current=reader.query('''SELECT id FROM answer_citation_turns
        WHERE business=? AND scope_key=? AND event_key=? AND phase=?''',(*args,_hash(event_id),'pending'))
    if len(current)!=1:_deny()
    number=current[0]['id']
    latest=reader.query('''SELECT id FROM answer_citation_turns WHERE business=? AND scope_key=?
        AND id=(SELECT id FROM answer_citation_turns WHERE business=? AND scope_key=? ORDER BY id DESC LIMIT 1)''',(*args,*args))
    if not latest or latest[0]['id']!=number:_deny()
    prior=reader.query('''SELECT id FROM answer_citation_turns WHERE business=? AND scope_key=?
        AND id=(SELECT id FROM answer_citation_turns WHERE business=? AND scope_key=? AND id<? ORDER BY id DESC LIMIT 1)''',
        (*args,*args,number))
    if not prior:_deny()
    target=_resolve(engine.s,engine.c,payload,engine.now(),reader=reader,anchor_id=prior[0]['id'],include_answer=True)
    if type(target['answer']) is not str or not 0<len(target['answer'])<=3000:_deny()
    return target


def _clarify(engine,event_id,payload,notice=_UNAVAILABLE,prefix="answer-rewrite"):
    try:scope,_=_gate(engine,payload)
    except (ValueError,TypeError,KeyError):
        engine.s.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(engine.b,event_id))
        return
    with nullcontext() if engine.s.db.in_transaction else engine.s.transaction():
        engine.s.queue(engine.b,payload['sender'],notice,prefix+':'+event_id,engine.now(),
                       preferred_transport=payload['channel'])
        engine.s.execute("UPDATE answer_citation_turns SET phase='unavailable' WHERE business=? AND scope_key=? AND event_key=?",
                         (engine.b,_hash(scope),_hash(event_id)))
        engine.s.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(engine.b,event_id))


def _checked_result(engine,result,limit):
    if (type(result) is not dict or type(result.get('actions')) is not list
            or result['actions'] or result.get('intent','conversation')!='conversation'):
        raise ValueError('Conversation intent cannot modify tasks')
    body=result.get('reply_text')
    if (type(body) is not str or not body.strip() or len(body)>limit
            or '\nИсточники:' in body or body.startswith('Источники:')
            or 'Источники исходного ответа:' in body):
        raise ValueError('Conversation requires a direct reply_text')
    if (result.get('knowledge_mode')!='not_needed' or result.get('evidence_ids')!=[]
            or type(result.get('evidence_ids')) is not list):
        raise ValueError('Invalid knowledge citations')
    if type(result.get('summary')) is not str:raise ValueError('Invalid summary')
    state=engine.intelligence.memory._state(result.get('dialogue_state'))
    if state is None or state['task_id'] is not None:raise ValueError('Unpermitted dialogue task focus')
    # A transformation cannot authorize a new pending task confirmation offer.
    if state.get('pending_offer') and state['pending_offer']['kind']=='confirm_task':
        raise ValueError('Conversation intent cannot modify tasks')
    return body,state


def handle_answer_rewrite(engine,event_id,payload):
    if not is_rewrite_request(payload.get('text')):_deny()
    def prepare(target):
        hint=build_followup(payload['text'],{'turns':[{'role':'assistant','content':target['answer']}],
                                           'pending_offer':None,'entities':[]})
        return dict(hint,target_answer=target['answer'],pending_offer=None) if hint else None
    return _handle_readonly_answer(engine,event_id,payload,prepare)


def _handle_readonly_answer(engine,event_id,payload,prepare,*,ordinal=False,notice=_UNAVAILABLE,prefix='answer-rewrite'):
    def clarify():return _clarify(engine,event_id,payload,notice,prefix)
    try:_gate(engine,payload)
    except (ValueError,TypeError,KeyError):
        engine.s.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(engine.b,event_id))
        return
    intelligence=engine.intelligence
    reader=_Reader(engine.s)
    try:target=_target(engine,event_id,payload,reader)
    except Exception:
        clarify();return
    # The caller selects a bounded text projection only; saved receipt refs
    # remain the sole citation authority for every read-only derivative.
    hint=prepare(target)
    if not hint:clarify();return
    footer=('\n\nИсточники исходного ответа:\n'+'\n'.join(target['labels'])) if target['labels'] else ''
    limit=3000-len(footer)
    if limit<128:clarify();return
    context=dict(kind='conversation',business=engine.b,sender=payload['sender'],text=payload['text'],
        intelligence=True,rewrite_only=True,rewrite_reply_chars=limit,followup=hint,ordinal_only=ordinal,
        tasks=[],employees={},knowledge={'evidence':[],'facts':[],'conflicts':[],'status':'no_evidence'},
        dialogue={'turns':[],'topic':'','entities':[],'pending_offer':None,'task_id':None},
        capabilities={'answer_rewrite':'existing_answer_text_only','task_mutations':False},recent_summaries=[])
    # Exactly the existing request's one model call. JsonModel keeps its own
    # input/output cost reservation, context bound, provider and budget unchanged.
    result=engine.model.propose(context)
    body,state=_checked_result(engine,result,limit)
    text=body+footer
    if len(text.encode('utf-16-le'))//2>3500:raise ValueError('Conversation requires a direct reply_text')
    # Validate before every successful output/task/dialogue write; keep the same
    # reader so the initial and final metadata/result probes share one budget.
    with nullcontext() if engine.s.db.in_transaction else engine.s.transaction():
        try:fresh=_target(engine,event_id,payload,reader)
        except Exception:
            clarify();return
        if fresh!=target:clarify();return
        key=prefix+':'+event_id
        queued=engine.s.queue(engine.b,payload['sender'],text,key,engine.now(),preferred_transport=payload['channel'])
        if not queued:return
        row=engine.s.execute('SELECT id,text FROM outbox WHERE business=? AND dedupe=?',(engine.b,key)).fetchone()
        # Host mutation inside queue must abort the surrounding transaction.
        if engine.intelligence is not intelligence:_deny()
        _gate(engine,payload)
        intelligence.protect_outbox(row['id'],target['protection'])
        if engine.intelligence is not intelligence:_deny()
        scope,_=_gate(engine,payload)
        # Receipt derivation preserves ONLY the exact saved original citation
        # authority. No refs are taken from generated prose or its footer.
        seals={item['ref']:{key:item[key] for key in ('kind','digest','observed_at')} for item in target['citations']}
        engine.s.execute('UPDATE answer_citation_turns SET seals=? WHERE business=? AND scope_key=? AND event_key=?',
                         (json.dumps(seals),engine.b,_hash(scope),_hash(event_id)))
        receipt_result=dict(actions=[],reply_text=text,evidence_ids=[item['ref'] for item in target['citations']])
        capture_answer(engine.s,engine.c,event_id,payload,receipt_result,list(target['protection']),[dict(row)],engine.now())
        if engine.intelligence is not intelligence:_deny()
        _gate(engine,payload)
        intelligence.memory.record(scope,event_id,payload['text'],text,state=state,
            evidence_ids=target['protection'],at=engine.now(),outbox_ids=[row['id']],
            context_kind='work' if any(item['ref'].startswith('work-task:') for item in target['citations']) else None)
        engine.s.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(engine.b,event_id))
