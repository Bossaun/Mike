"""Default-off local own-review question and uncertain self-report lifecycle.

No question is sent, and a self-report is never proof of customer contact. Only
already-admitted outgoing anchors can enter the existing touch register. All
question/register writes share the caller operation's captured Store transaction.
"""
from dataclasses import dataclass,field
from datetime import datetime,timezone
import math
import re

from . import customer_rules as core
from .customer_ingest import SourceRegistration,registration_fingerprint
from .customer_touch_register import (CustomerTouchRegister,TouchReviewSnapshot,
    _hash,_json,_id,_utc,_window,_from_window,_date,_closed,_pairs,_proof_data)
from .customer_product_draft_command import _gate
from .weekly_review_command import _host
from .store import Store
import json

MAX_STATE_BYTES=16384
MAX_BYTES=262144
MAX_QUERIES=32
MAX_ROWS=16


class TouchReviewUnavailable(ValueError):
    def __init__(self):super().__init__('customer_touch_review_unavailable')


def _deny():raise TouchReviewUnavailable()


def _method(obj,name):
    value=getattr(obj,name,None)
    return getattr(value,'__self__',None),getattr(value,'__func__',None)


def _output(text):
    if type(text) is not str or len(text.encode())>8000 or len(text.encode('utf-16-le'))//2>3500:_deny()
    return text


@dataclass(frozen=True)
class ReviewQuestion:
    question_id:str
    text:str
    item_count:int
    expires_at:datetime
    requires_review:bool=field(default=True,init=False)
    live_authorized:bool=field(default=False,init=False)


@dataclass(frozen=True)
class SelfReportAcknowledgement:
    question_id:str
    text:str
    reported_count:int
    summary:core.TouchSummary
    replayed:bool
    requires_review:bool=field(default=True,init=False)
    live_authorized:bool=field(default=False,init=False)


class _Reader:
    """One cumulative, point-only question reader for each public operation."""
    def __init__(self,store):self.store=store;self.queries=0;self.rows=0;self.bytes=0
    def read(self,key):
        self.queries+=1
        if self.queries>MAX_QUERIES:_deny()
        rows=self.store.execute("SELECT CASE WHEN typeof(value)='text' AND length(CAST(value AS BLOB))<=? THEN value END FROM runtime WHERE key=? LIMIT 2",(MAX_STATE_BYTES,key)).fetchall()
        self.rows+=len(rows)
        if len(rows)>1 or self.rows>MAX_ROWS:_deny()
        if not rows:return None
        value=rows[0][0]
        if type(value) is not str:_deny()
        self.bytes+=len(value.encode())
        if self.bytes>MAX_BYTES:_deny()
        return value


class TouchReviewLoop:
    def __init__(self,*,engine,register,source,conversation,enabled=False):
        self._engine=engine;self._register=register;self._source=source;self._conversation=conversation
        self._enabled=enabled is True;self._host=_host(engine);self._store=self._host[0]
        self._db=getattr(self._store,'db',None);self._clock=engine.now
        self._methods=tuple(_method(register,n) for n in ('review_snapshot','record_self_reported_receipts_in_transaction'))
        self._bound=(engine,register,source,conversation)

    def _check(self,payload):
        if not self._enabled:_deny()
        if (type(payload) is not dict or any(a is not b for a,b in zip(self._bound,(self._engine,self._register,self._source,self._conversation))) or
            type(self._store) is not Store or self._store.db is not self._db or self._engine.now is not self._clock or
            type(self._register) is not CustomerTouchRegister or type(self._source) is not SourceRegistration or
            type(self._conversation) is not core.Conversation):_deny()
        for name,expected in zip(('review_snapshot','record_self_reported_receipts_in_transaction'),self._methods):
            actual=_method(self._register,name)
            if any(a is not b for a,b in zip(actual,expected)):_deny()
        scope,policy=_gate(self._engine,payload,self._host)
        source=self._source;conv=self._conversation;registration_fingerprint(source)
        for value in (conv.customer_id,conv.channel,conv.thread_id,conv.source_id):_id(value)
        if source.business!=self._host[2] or conv.source_id!=source.source_id or conv.channel!=source.channel:_deny()
        token=_hash(['touch-review-scope:v1',self._host[2],source.identity,conv.customer_id,conv.channel,conv.thread_id,conv.source_id])
        return token,policy,payload['sender'],payload['channel'],scope

    def _clock_at(self,now):
        now=_utc(now);stamp=self._clock()
        if type(stamp) not in (int,float) or not math.isfinite(stamp) or datetime.fromtimestamp(stamp,timezone.utc)!=now:_deny()
        return now

    def _snapshot(self,now,window):
        snapshot=self._register.review_snapshot(now=now,reporting_window=window,expected_store=self._store)
        if (type(snapshot) is not TouchReviewSnapshot or snapshot.conversation!=self._conversation or
            snapshot.requires_review is not True or snapshot.live_authorized is not False or
            type(snapshot.local_revision) is not int or snapshot.local_revision<0 or
            any(type(v) is not str or re.fullmatch('[0-9a-f]{64}',v) is None for v in (snapshot.digest,snapshot.scope_fingerprint))):_deny()
        proof=snapshot.evaluation.source_proof
        if (proof.business!=self._host[2] or proof.source_id!=self._source.source_id or proof.channel!=self._source.channel or
            proof.registration_fingerprint!=registration_fingerprint(self._source) or proof.state_present is not True or
            type(snapshot.evaluation.summary) is not core.TouchSummary):_deny()
        return snapshot

    def _cohort(self,snapshot,plans,actor):
        candidates={c.target_id:c for c in snapshot.evaluation.preview.candidates}
        if len(candidates)!=len(snapshot.evaluation.preview.candidates):_deny()
        rows=[]
        for identifier in plans:
            candidate=candidates.get(identifier)
            if (type(candidate) is not core.Candidate or candidate.conversation!=self._conversation or
                candidate.kind!='touch' or candidate.owner_id!=actor or candidate.draft_only is not True or
                identifier not in snapshot.evaluation.summary.planned_ids):_deny()
            rows.append([candidate.id,candidate.target_id,candidate.owner_id,_utc(candidate.due_at).isoformat(),list(candidate.evidence_ids),candidate.draft])
        return _hash(rows)

    def _decode(self,raw,identity):
        if raw is None:return None
        q=json.loads(raw,object_pairs_hook=_pairs)
        _closed(q,{'version','scope','policy','actor','channel','audience','generation','question_id','prepared_at','expires_at','window','plans','cohort','snapshot','source','state','report'})
        if (type(q['version']) is not int or q['version']!=1 or
            q['scope']!=identity[0] or type(q['policy']) is not str or re.fullmatch('[0-9a-f]{64}',q['policy']) is None or
            type(q['channel']) is not str or q['channel'] not in ('bot','business') or type(q['audience']) is not str or len(q['audience'].encode())>2048 or
            type(q['generation']) is not int or not 1<=q['generation']<2**53 or
            type(q['plans']) is not list or not 1<=len(q['plans'])<=8 or len(set(q['plans']))!=len(q['plans']) or
            q['state'] not in ('pending','reported')):_deny()
        _id(q['actor'])
        for p in q['plans']:_id(p)
        for key in ('cohort','snapshot','source'):
            if type(q[key]) is not str or re.fullmatch('[0-9a-f]{64}',q[key]) is None:_deny()
        window=_from_window(q['window']);start=_date(q['prepared_at']);expiry=_date(q['expires_at'])
        if not window.contains(start) or not start<expiry<=_utc(window.end):_deny()
        if q['question_id']!=self._qid(q):_deny()
        if q['state']=='pending':
            if q['report'] is not None:_deny()
        else:
            r=_closed(q['report'],{'input_digest','snapshot','count','reported_at'})
            if any(type(r[k]) is not str or re.fullmatch('[0-9a-f]{64}',r[k]) is None for k in ('input_digest','snapshot')) or type(r['count']) is not int or not 0<=r['count']<=len(q['plans']) or not start<=_date(r['reported_at'])<expiry:_deny()
        return q

    @staticmethod
    def _qid(q):
        return 'touch-question:'+_hash(['touch-question:v1',{k:v for k,v in q.items() if k not in ('question_id','state','report')}])

    def _cas(self,reader,key,old,q):
        encoded=_json(q)
        if len(encoded.encode())>MAX_STATE_BYTES:_deny()
        if old is None:count=self._store.execute('INSERT INTO runtime(key,value) SELECT ?,? WHERE NOT EXISTS(SELECT 1 FROM runtime WHERE key=?)',(key,encoded,key)).rowcount
        else:count=self._store.execute('UPDATE runtime SET value=? WHERE key=? AND value=?',(encoded,key,old)).rowcount
        if count!=1 or reader.read(key)!=encoded:_deny()
        return encoded

    @staticmethod
    def _question(q):
        lines=['Вопрос для вашей ручной проверки: сколько из выбранных касаний вы отмечаете по своему сообщению?']
        lines+=['Касание '+str(i) for i in range(1,len(q['plans'])+1)]
        lines+=['Укажите число, номера и уже зарегистрированные исходящие события. Одного числа недостаточно.',
            'Фактическое выполнение этим не проверяется. Это локальный черновик вопроса; он не отправлен.']
        return ReviewQuestion(q['question_id'],_output('\n'.join(lines)),len(q['plans']),_date(q['expires_at']))

    def prepare(self,scope_payload,*,now,reporting_window,plan_ids,expires_at,replace_question_id=None):
        """Return one current local question, without creating a message or slot."""
        try:
            identity=self._check(scope_payload);now=_utc(now);_window(reporting_window);expiry=_utc(expires_at)
            if (type(plan_ids) is not tuple or not 1<=len(plan_ids)<=8 or len(set(plan_ids))!=len(plan_ids) or
                not reporting_window.contains(now) or not now<expiry<=_utc(reporting_window.end) or expiry>_utc(self._source.expires_at)):_deny()
            for value in plan_ids:_id(value)
            if replace_question_id is not None:_id(replace_question_id)
            if self._db.in_transaction:_deny()
            reader=_Reader(self._store);key='customer-touch-question:v1:'+identity[0]
            with self._store.transaction():
                raw=reader.read(key);old=self._decode(raw,identity)
                self._clock_at(now)
                first=self._snapshot(now,reporting_window);cohort=self._cohort(first,plan_ids,identity[2])
                if self._check(scope_payload)!=identity:_deny()
                retry=old is not None and (tuple(old[k] for k in ('scope','policy','actor','channel','audience'))==identity and
                    old['state']=='pending' and old['plans']==list(plan_ids) and old['window']==_window(reporting_window) and
                    old['expires_at']==expiry.isoformat() and old['snapshot']==first.digest and _date(old['prepared_at'])<=now<_date(old['expires_at']))
                if retry:
                    q=old;encoded=raw
                else:
                    if old is not None and replace_question_id!=old['question_id'] or old is None and replace_question_id is not None:_deny()
                    q=dict(version=1,scope=identity[0],policy=identity[1],actor=identity[2],channel=identity[3],audience=identity[4],
                        generation=1 if old is None else old['generation']+1,prepared_at=now.isoformat(),expires_at=expiry.isoformat(),
                        window=_window(reporting_window),plans=list(plan_ids),cohort=cohort,snapshot=first.digest,
                        source=_hash(_proof_data(first.evaluation.source_proof)),state='pending',report=None)
                    q['question_id']=self._qid(q)
                    encoded=self._cas(reader,key,raw,q)
                result=self._question(q)
                # Queue-metadata writes and the last clock callback precede the
                # final actual public source/register phase, then pure checks.
                self._clock_at(now);final=self._snapshot(now,reporting_window)
                if final!=first or self._cohort(final,plan_ids,identity[2])!=cohort or reader.read(key)!=encoded or self._check(scope_payload)!=identity:_deny()
                return result
        except TouchReviewUnavailable:raise
        except Exception:raise TouchReviewUnavailable() from None

    def submit(self,scope_payload,*,now,question_id,reported_count,selected_ordinals,outgoing_event_ids):
        """Self-report only. Never create an Event or infer a customer delivery."""
        try:
            identity=self._check(scope_payload);now=_utc(now);_id(question_id)
            if (type(reported_count) is not int or not 0<=reported_count<=8 or type(selected_ordinals) is not tuple or
                type(outgoing_event_ids) is not tuple or len(selected_ordinals)!=reported_count or len(outgoing_event_ids)!=reported_count or
                any(type(i) is not int or not 1<=i<=8 for i in selected_ordinals) or
                len(set(selected_ordinals))!=reported_count or len(set(outgoing_event_ids))!=reported_count):_deny()
            for value in outgoing_event_ids:_id(value)
            if self._db.in_transaction:_deny()
            reader=_Reader(self._store);key='customer-touch-question:v1:'+identity[0]
            report_hash=_hash(['touch-self-report:v1',question_id,reported_count,list(selected_ordinals),list(outgoing_event_ids)])
            with self._store.transaction():
                raw=reader.read(key);q=self._decode(raw,identity)
                if q is None or tuple(q[k] for k in ('scope','policy','actor','channel','audience'))!=identity or q['question_id']!=question_id or not _date(q['prepared_at'])<=now<_date(q['expires_at']) or any(i>len(q['plans']) for i in selected_ordinals):_deny()
                window=_from_window(q['window']);self._clock_at(now);current=self._snapshot(now,window)
                if _hash(_proof_data(current.evaluation.source_proof))!=q['source'] or self._check(scope_payload)!=identity:_deny()
                if q['state']=='reported':
                    if q['report']['input_digest']!=report_hash or q['report']['snapshot']!=current.digest or now<_date(q['report']['reported_at']):_deny()
                    replayed=True;expected=current
                else:
                    if current.digest!=q['snapshot'] or self._cohort(current,tuple(q['plans']),identity[2])!=q['cohort']:_deny()
                    receipts=tuple(core.TouchReceipt('self-report:'+_hash([question_id,q['plans'][i-1]]),self._conversation,q['plans'][i-1],event_id,False)
                        for i,event_id in zip(selected_ordinals,outgoing_event_ids))
                    expected=self._register.record_self_reported_receipts_in_transaction(receipts,now=now,reporting_window=window,
                        expected_store=self._store,expected_snapshot_digest=current.digest)
                    if type(expected) is not TouchReviewSnapshot:_deny()
                    q=dict(q,state='reported',report=dict(input_digest=report_hash,snapshot=expected.digest,count=reported_count,reported_at=now.isoformat()))
                    raw=self._cas(reader,key,raw,q);replayed=False
                # The last clock callback precedes the final public current-data
                # snapshot. Question and register are compared again afterwards.
                self._clock_at(now);final=self._snapshot(now,window)
                if final!=expected or _hash(_proof_data(final.evaluation.source_proof))!=q['source'] or reader.read(key)!=raw or self._check(scope_payload)!=identity:_deny()
                if any(q['plans'][i-1] not in final.evaluation.summary.uncertain_ids for i in selected_ordinals):_deny()
                text='Самоотчёт зарегистрирован: '+str(reported_count)+'. Фактический контакт и доставка этим не подтверждены.'
                if reported_count==0:text='Нулевой самоотчёт зарегистрирован. Квитанции касаний не созданы; выполнение не подтверждено.'
                return SelfReportAcknowledgement(question_id,_output(text),reported_count,final.evaluation.summary,replayed)
        except TouchReviewUnavailable:raise
        except Exception:raise TouchReviewUnavailable() from None
