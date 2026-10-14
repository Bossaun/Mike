import hashlib
import json
import re
import time
import uuid
from contextlib import nullcontext
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

STATUSES = {'confirmed','proposed','in_progress','blocked','completed'}
FIELDS = {'title','owner','deadline','next_step','criterion','status','evidence'}
REFERENCE_PATTERN = r'(?:№|#|задач[ауеи]?\s*)(?:\s*№)?\s*(\d+)'
from .conversation import basic_reply, is_general

class ClarificationRequired(ValueError):
    def __init__(self,reason,notice=None):
        super().__init__(reason)
        self.notice=notice or reason

def fingerprint(text):
    return hashlib.sha256(' '.join(text.casefold().split()).encode()).hexdigest()

class Engine:
    def __init__(self, store, config, model, now=time.time):
        self.s, self.c, self.model, self.now = store, config, model, now
        self.b = config['business']
        self.tz = ZoneInfo(config.get('timezone','Europe/Moscow'))

    def tasks(self, owner=None):
        sql='SELECT * FROM tasks WHERE business=?'; args=[self.b]
        if owner is not None:
            sql+=' AND owner=?'; args.append(owner)
        return self.s.rows(sql+' ORDER BY created', args)

    def transcript(self, text, link='uploaded transcript'):
        if not text.strip() or len(text)>self.c.get('max_transcript_chars',32000):
            raise ValueError('Empty or oversized transcript; split into explicit parts')
        id='transcript:'+fingerprint(text)
        self.s.event(self.b,id,'transcript',{'text':text,'link':link,'source_id':link if link.startswith(('business:','telegram:')) else id,'sender':self.c['admins'][0]})
        return id

    def reply(self, event_id, sender, text, task_id=None, source_id=None, channel=None):
        if sender not in self.c['employees'] and sender not in self.c['admins']:
            return False
        payload={'sender':sender,'text':text,'task_id':task_id,
                 'source_id':source_id or event_id,'link':event_id,'channel':channel,
                 'received_at':self.now()}
        with nullcontext() if self.s.db.in_transaction else self.s.transaction():
            added=self.s.event(self.b,event_id,'reply',payload)
            operator=self._operator_turn(payload,'reply')
            general=basic_reply(text) is not None if operator else is_general(text)
            if added and not general and (not operator or task_id or self.reference_ids(text) or re.search(REFERENCE_PATTERN,text.casefold())):
                context=self._context(payload,'reply')
                if len(context['tasks'])==1:
                    self._record_response(context['tasks'][0]['id'],sender,payload['received_at'])
            return added

    def _operator_turn(self,p,kind):
        gate=self.c.get('personal_intake')
        if not (kind=='reply' and p.get('channel')=='bot' and isinstance(gate,dict)
                and gate.get('recipient')==p['sender'] and p['sender'] in self.c['admins']):return False
        from .intake import validate_personal
        try:validate_personal(self.c)
        except (ValueError,TypeError,KeyError):return False
        return True

    def _operator_candidates(self,p):
        candidates=self.tasks()
        references=self.reference_ids(p['text'])
        if p.get('task_id'):
            candidates=[t for t in candidates if t['id']==p['task_id']]
        elif references or re.search(REFERENCE_PATTERN,p['text'].casefold()):
            candidates=[t for t in candidates if t['id'] in references]
        else:
            words=set(re.findall(r'\w+',p['text'].casefold()))
            candidates=sorted([t for t in candidates if t['status']!='completed'],
                key=lambda t:len(words & set(re.findall(r'\w+',t['title'].casefold()))),reverse=True)
        return [{k:t[k] for k in ('id','public_number','title','owner','deadline','next_step','criterion','status','revision')}
                for t in candidates[:12]]

    def _record_response(self, task_id, sender, received_at):
        changed=self.s.execute('UPDATE tasks SET last_reply=MAX(COALESCE(last_reply,0),?) WHERE business=? AND id=? AND owner=?',
                               (received_at,self.b,task_id,sender)).rowcount
        if changed:
            self.s.execute("UPDATE outbox SET state='cancelled' WHERE business=? AND task_id=? AND recipient=? AND state IN ('pending','retry','blocked') AND dedupe LIKE 'reminder:%' AND created<=?",
                           (self.b,task_id,sender,received_at))

    def reference_ids(self,text):
        numbers={int(n) for n in re.findall(REFERENCE_PATTERN,text.casefold())}
        return {t['id'] for t in self.tasks() if t['id'] in text or t.get('public_number') in numbers}

    def queue_meeting_review(self,tasks,key,recipient):
        from .presentation import meeting_messages
        pages=meeting_messages(tasks,self.c)
        for index,page in enumerate(pages):
            self.s.queue(self.b,recipient,page,f'review:{key}:{recipient}:page:{index}',self.now(),parse_mode='HTML')

    def _context(self,p,kind):
        allowed=self.tasks() if kind=='transcript' or p['sender'] in self.c['admins'] else self.tasks(p['sender'])
        if p.get('task_id'):
            allowed=[t for t in allowed if t['id']==p['task_id']]
        else:
            # Transcript gets active candidates ranked by overlap, never all history.
            words=set(re.findall(r'\w+',p['text'].casefold()))
            allowed=sorted([t for t in allowed if t['status']!='completed'],
                key=lambda t:len(words & set(re.findall(r'\w+',t['title'].casefold()))),reverse=True)
        if kind=='reply' and not p.get('task_id') and (len(allowed)>1 or self.reference_ids(p['text']) or re.search(REFERENCE_PATTERN,p['text'].casefold())):
            # Free text cannot silently choose one of several tasks without a reference.
            mentioned=[t for t in allowed if t['id'] in self.reference_ids(p['text']) or t['title'].casefold() in p['text'].casefold()]
            allowed=mentioned
        summaries=[]
        for row in self.s.rows("SELECT detail FROM audit WHERE business=? AND kind='processed' ORDER BY id DESC LIMIT 20",(self.b,)):
            detail=json.loads(row['detail'])
            if detail.get('sender')==p['sender']: summaries.append(detail.get('summary','')[:500])
            if len(summaries)==3: break
        return {'recent_summaries':summaries,'rules':self.c.get('rules',[]),'date':datetime.fromtimestamp(p.get('received_at',self.now()),self.tz).isoformat(),
                'business':self.b,'kind':kind,'sender':p['sender'],'text':'\n'.join(f'{i} | {line}' for i,line in enumerate(p['text'].splitlines(),1)),
                'employees':{k:v['name'] for k,v in self.c['employees'].items()},
                'speaker_aliases':self.c.get('speaker_aliases',{}),
                'tasks':[{k:t[k] for k in ('id','public_number','title','owner','deadline','next_step','criterion','status','revision')}
                         for t in allowed[:12]]}

    def process(self,event_id):
        event=self.s.execute('SELECT * FROM events WHERE business=? AND id=?',(self.b,event_id)).fetchone()
        if not event or event['state'] in ('done','superseded'): return
        p=json.loads(event['payload']); kind=event['kind']
        try:
            context=self._context(p,kind)
            personal_operator=self._operator_turn(p,kind)
            general=kind=='reply' and (basic_reply(p['text']) is not None if personal_operator else is_general(p['text']))
            operator=personal_operator and not general
            if operator:
                context['kind']='operator_chat'
                context['tasks']=self._operator_candidates(p)
                context['availability']=self.c.get('availability','development')
            explicit_reference=p.get('task_id') or self.reference_ids(p['text']) or re.search(REFERENCE_PATTERN,p['text'].casefold())
            explicit_creation=operator and re.search(r'\bсоздай(?:те)?\s+(?:(?:новую|тестовую)\s+)*задачу\b',p['text'].casefold())
            completion_phrase=re.search(r'\b(?:готов(?:о|а|ы)?|заверш(?:ил|ила|ено)|сделал(?:а)?|сделано|закончил(?:а)?)\b',p['text'].casefold())
            ambiguous=kind=='reply' and not context['tasks'] and (explicit_reference or (completion_phrase and not explicit_creation))
            if ambiguous and not general:
                message='Уточните задачу: ответьте на сообщение о ней или укажите её номер.'
                result={'actions':[],'summary':message,'reply_text':message,'intent':'conversation'}
            else:
                if not operator and (general or (kind=='reply' and not context['tasks'])):
                    context['kind']='conversation';context['tasks']=[]
                    visible=self.tasks() if p['sender'] in self.c['admins'] else self.tasks(p['sender'])
                    context['task_overview']={status:sum(t['status']==status for t in visible) for status in STATUSES}
                    context['availability']=self.c.get('availability','development')
                direct=basic_reply(p['text']) if context['kind']=='conversation' else None
                result=({'actions':[],'summary':'Объяснены возможности помощника.','reply_text':direct} if direct else self.model.propose(context))
                if context['kind']=='conversation' and result.get('actions'):
                    raise ValueError('General conversation cannot modify tasks')
                self.s.audit(self.b,'deterministic_reply' if direct else 'model_proposal',{'event':event_id,'result':result},self.now())
            actions=result.get('actions')
            if not isinstance(actions,list) or len(actions)>20: raise ValueError('Invalid action list')
            if not isinstance(result.get('summary',''),str): raise ValueError('Invalid summary')
            reply_text=result.get('summary','')
            if operator:
                if result.get('intent') not in ('conversation','task_action'):
                    raise ValueError('Operator chat requires an explicit intent')
                if result['intent']=='conversation' and actions:
                    raise ValueError('Conversation intent cannot modify tasks')
                context['task_intent']=result['intent']
            if context['kind'] in ('conversation','operator_chat'):
                reply_text=result.get('reply_text')
                if not isinstance(reply_text,str) or not reply_text.strip() or len(reply_text)>3000:
                    raise ValueError('Conversation requires a direct reply_text')
            with self.s.transaction():
                # Re-check event after model call to guard replay by another process.
                state=self.s.execute('SELECT state FROM events WHERE business=? AND id=?',(self.b,event_id)).fetchone()[0]
                if state=='done': return
                source=p.get('source_id') or event_id
                self.s.execute('INSERT INTO sources(business,id,body,link) VALUES(?,?,?,?) ON CONFLICT(business,id) DO UPDATE SET body=excluded.body,link=excluded.link',
                               (self.b,source,p['text'],p.get('link',event_id)))
                changed=[]
                for a in actions:
                    if (kind=='transcript' or operator) and self.c.get('review_mode') and a.get('op')=='create':
                        a=dict(a,status='proposed')
                    changed.append(self._apply(a,context,source,kind,p))
                if kind=='reply' and context['kind']!='conversation':
                    for t in context['tasks']:
                        if operator and not any(a.get('op')=='update' and a.get('task_id')==t['id'] for a in actions):continue
                        self._record_response(t['id'],p['sender'],p.get('received_at',self.now()))
                if kind=='transcript' and self.c.get('review_mode'):
                    from .presentation import meeting_messages
                    reviewed=[dict(self.s.execute('SELECT * FROM tasks WHERE business=? AND id=?',(self.b,t)).fetchone()) for t in dict.fromkeys(changed)]
                    recipients=[self.c['meeting_recipient']] if self.c.get('meeting_recipient') else self.c['admins']
                    for recipient in recipients:
                        self.queue_meeting_review(reviewed,event_id,recipient)
                action_queued=False
                for id in ([] if kind=='transcript' and self.c.get('review_mode') else dict.fromkeys(changed)):
                    t=dict(self.s.execute('SELECT * FROM tasks WHERE business=? AND id=?',(self.b,id)).fetchone())
                    if operator:
                        from .presentation import task_card
                        recipients=[p['sender']]
                        display=dict(self.c,employees=dict(self.c['employees']))
                        if t['owner']==p['sender']:display['employees'][p['sender']]=dict(self.c['employees'][p['sender']],name='Вы')
                        body=task_card(t,display)
                        missing=[]
                        if not t['owner']:missing.append('ответственного')
                        if not t['deadline']:missing.append('срок')
                        if not t['criterion']:missing.append('критерий завершения')
                        if missing:body+='\n\nНужно уточнить: '+', '.join(missing)+'.'
                        if t['status']=='proposed':body+='\nПодтвердите поручение или поправьте детали.'
                        for question in context.get('deadline_questions',[])+context.get('owner_questions',[]):body+='\n'+question
                    elif kind=='reply' and p.get('channel')=='group':
                        from .presentation import task_card
                        recipients=[p['sender']];body=task_card(t,self.c)
                    elif t['status']=='proposed':
                        recipients=self.c['admins']; body=f"Предложение, требуется подтверждение: Задача №{t['public_number']} {t['title']}"
                    elif t['status']=='completed':
                        recipients=[self.c['meeting_recipient']] if self.c.get('meeting_recipient') else self.c['admins']; body=f"Выполнено: Задача №{t['public_number']} {t['title']}. Результат: {t['evidence'][:700]}"
                    else:
                        recipients=[t['owner']] if t['owner'] else self.c['admins']
                        missing=[]
                        if not t['owner']: missing.append('Кто отвечает?')
                        if not t['deadline']: missing.append('Какой срок?')
                        if not t['criterion']: missing.append('Какой результат подтвердит завершение?')
                        body=f"Задача №{t['public_number']} · {t['title']}\nСледующий шаг: {t['next_step']}\nСрок: {t['deadline'] or 'не указан'}\n"+' '.join(missing or ['Сообщите результат или препятствие.'])
                    for recipient in recipients:
                        queued=self.s.queue(self.b,recipient,body,f"action:{id}:{t['revision']}:{recipient}",self.now(),id,parse_mode='HTML' if operator or (kind=='reply' and p.get('channel')=='group') else None,preferred_transport=p.get('channel') if kind=='reply' and recipient==p['sender'] else None)
                        action_queued=action_queued or queued
                questions=context.get('deadline_questions',[])+context.get('owner_questions',[])
                if operator and questions and not action_queued:
                    self.s.queue(self.b,p['sender'],'\n'.join(questions),f'field-clarify:{event_id}',self.now(),preferred_transport=p.get('channel'))
                if not actions and kind=='reply':
                    self.s.queue(self.b,p['sender'],reply_text or 'Уточните результат и следующий шаг.',f'clarify:{event_id}',self.now(),preferred_transport=p.get('channel'))
                self.s.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(self.b,event_id))
                self.s.audit(self.b,'processed',{'event':event_id,'sender':p['sender'],'tasks':changed,'summary':result.get('summary','')[:2000]},self.now())
        except Exception as exc:
            self.s.execute("UPDATE events SET state='failed',error=? WHERE business=? AND id=?",(type(exc).__name__+': '+str(exc)[:300],self.b,event_id))
            if kind=='reply':
                notice=exc.notice if isinstance(exc,ClarificationRequired) else 'Я получил сообщение, но сейчас не могу его обработать. Попробуйте ещё раз чуть позже.'
                self.s.queue(self.b,p['sender'],notice,f'error:{event_id}',self.now(),preferred_transport=p.get('channel'))
            raise

    def _apply(self,a,context,source,kind,p):
        if not isinstance(a,dict): raise ValueError('Invalid action')
        a=dict(a)
        mask=a.pop('set_fields',None)
        if mask is not None:
            if not isinstance(mask,list) or any(k not in FIELDS-{'evidence'} for k in mask): raise ValueError('Invalid update mask')
            if a.get('op')=='update':
                for field in FIELDS-{'evidence'}:
                    if field not in mask: a.pop(field,None)
                if 'owner' not in mask: a.pop('owner_lines',None)
                if 'deadline' not in mask: a.pop('deadline_lines',None)
        lines=p['text'].splitlines()
        cited=set()
        for field,reference in (('evidence','evidence_lines'),('owner_evidence','owner_lines'),('deadline_evidence','deadline_lines')):
            if reference in a:
                indexes=a.pop(reference)
                if reference!='evidence_lines' and indexes==[] and not a.get('owner' if reference=='owner_lines' else 'deadline'):
                    a[field]='';continue
                if not isinstance(indexes,list) or not 1<=len(indexes)<=3 or any(type(i) is not int or not 1<=i<=len(lines) for i in indexes): raise ValueError('Invalid source line reference')
                a[field]='\n[… ]\n'.join(lines[i-1] for i in indexes)
                cited.add(field)
        if a.get('op') not in ('create','update'): raise ValueError('Invalid operation')
        if set(a)-FIELDS-{'op','task_id','owner_evidence','deadline_evidence'}: raise ValueError('Unknown action fields')
        operator=(context.get('kind')=='operator_chat' and self._operator_turn(p,kind)
                  and context.get('task_intent')=='task_action')
        if a['op']=='create' and kind!='transcript' and not operator: raise ValueError('Replies cannot create tasks')
        evidence=a.get('evidence','')
        if not isinstance(evidence,str) or not evidence or not evidence.strip() or ('evidence' not in cited and evidence not in p['text']): raise ValueError('Missing literal evidence')
        for field in ('title','next_step','criterion'):
            if field in a and (not isinstance(a[field],str) or len(a[field])>1000): raise ValueError('Invalid text')
        if 'status' in a and a['status'] not in STATUSES: raise ValueError('Invalid status')
        if a.get('owner') is not None:
            if a['owner'] not in self.c['employees']: raise ValueError('Unknown owner')
            quote=a.get('owner_evidence','')
            name=self.c['employees'][a['owner']]['name']
            aliases=[name]+[label for label,owner in self.c.get('speaker_aliases',{}).items() if owner==a['owner']]
            self_commit=kind=='reply' and a['owner']==p['sender'] and isinstance(quote,str) and bool(re.search(r'\b(я|меня|мне|беру|сделаю|возьму)\b',quote.casefold()))
            if operator and self_commit:
                commitment=re.search(r'\b(?:я\s+(?:сделаю|возьму|беру|отвечаю|выполню)|(?:ответственн[а-яё]*|отвечаю|исполнитель)\s*[—:=-]?\s*я|(?:назначь|назначайте|поставь)\s+меня|беру\s+(?:это|задачу|на\s+себя))\b',quote.casefold())
                denied=re.search(r'\b(?:не\s+я|я\s+не\s+(?:буду|беру|берусь|сделаю|возьму|отвечаю)|не\s+(?:назначай|назначайте)\s+меня)\b',quote.casefold())
                self_commit=bool(commitment) and not denied
            if not quote or ('owner_evidence' not in cited and quote not in p['text']) or not (self_commit or any(label.casefold() in quote.casefold() for label in aliases)):
                if (kind=='transcript' or operator) and self.c.get('review_mode'):
                    self.s.audit(self.b,'owner_requires_review',{'source':source,'title':a.get('title'),'unverified_owner':a['owner']},self.now())
                    if operator and a['op']=='update':
                        a.pop('owner',None)
                        context.setdefault('owner_questions',[]).append('Ответственный не изменён: уточните, кто берёт задачу.')
                    else:a['owner']=None
                else: raise ValueError('Owner not grounded in text')
        if 'deadline' in a and a['deadline'] is not None and not isinstance(a['deadline'],str): raise ValueError('Invalid deadline')
        if operator and a.get('deadline_evidence'):
            quote=a['deadline_evidence']
            if 'deadline_evidence' not in cited and quote not in p['text']:raise ValueError('Deadline not grounded in text')
            from .deadlines import resolve_deadline
            resolved=resolve_deadline(quote,datetime.fromtimestamp(p.get('received_at',self.now()),self.tz))
            if resolved is None and a['op']=='update':
                a.pop('deadline',None)
                context.setdefault('deadline_questions',[]).append('Срок не изменён: уточните дату нового срока.')
            else:a['deadline']=resolved
        if a.get('deadline'):
            date.fromisoformat(a['deadline'])
            quote=a.get('deadline_evidence','')
            if not quote or ('deadline_evidence' not in cited and quote not in p['text']): raise ValueError('Deadline not grounded in text')
            # Pilot accepts explicit ISO dates; relative dates need an explicit clarification.
            if not operator and a['deadline'] not in quote: raise ValueError('Use explicit ISO deadline, clarify relative dates')
        if a['op']=='create':
            for f in ('title','next_step','criterion','status'):
                if f not in a: raise ValueError('Incomplete task')
            if not a['title'].strip(): raise ValueError('Empty title')
            if a['status'] not in ('confirmed','proposed'): raise ValueError('New task must be confirmed or proposed')
            # Deterministic exact semantic-title guard. Fuzzy matches remain model suggestions.
            existing=next((t for t in self.tasks() if fingerprint(t['title'])==fingerprint(a['title']) and t['status']!='completed'),None)
            if existing:
                id=existing['id']
                self.s.execute('INSERT OR IGNORE INTO task_sources VALUES(?,?,?)',(self.b,id,source))
                return id
            id=uuid.uuid4().hex[:12]
            self.s.execute('INSERT INTO tasks(business,id,title,owner,deadline,next_step,criterion,status,source_id,evidence,created) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (self.b,id,a['title'],a.get('owner'),a.get('deadline'),a['next_step'],a['criterion'],a['status'],source,evidence,self.now()))
        else:
            id=a.get('task_id')
            old=next((t for t in context['tasks'] if t['id']==id),None)
            if not old: raise ValueError('Task outside permitted context')
            if operator and not p.get('task_id'):
                referenced=id in self.reference_ids(p['text']) or old['title'].casefold() in p['text'].casefold()
                if not referenced and len(context['tasks'])!=1:
                    raise ClarificationRequired('Уточните номер задачи, которую нужно изменить: сейчас подходят несколько задач.')
            if p['sender'] not in self.c['admins']:
                if a.get('owner',old['owner'])!=old['owner']: raise ValueError('Employee cannot reassign')
                if old['status']=='proposed': raise ValueError('Only admin may confirm proposal')
            if operator and old['status']=='proposed' and a.get('status') in ('confirmed','in_progress','blocked'):
                explicit=bool(re.search(r'\b(подтверждаю|утверждаю|подтверждаем|беру)\b',evidence.casefold()))
                simple_yes=evidence.strip().casefold().strip(' .!')=='да' and len(context['tasks'])==1
                if not (explicit or simple_yes):raise ClarificationRequired('Draft requires explicit confirmation','Подтвердите черновик задачи или поправьте детали.')
            if a.get('status')=='completed':
                low=evidence.casefold()
                if operator:
                    detail=re.sub(REFERENCE_PATTERN,'',low)
                    detail=re.sub(r'\b(?:готов[а-яё]*|выполн[а-яё]*|сделан[а-яё]*|заверш[а-яё]*|задач[а-яё]*|вся|всю|всё|весь|все|работ[а-яё]*|полностью)\b','',detail)
                    if len(detail.strip(' :.,!'))<12 or re.search(r'\b(?:почти|не\s+(?:готов[а-яё]*|сделал[а-яё]*|выполн[а-яё]*|закончил[а-яё]*|заверш[а-яё]*))\b',low):
                        raise ClarificationRequired('Completion requires a concrete result','Что именно сделано? Пришлите готовый результат или ссылку.')
                if len(evidence.strip())<12 or any(w in low for w in ('почти','almost','не готов','не заверш','пока не','not done')):
                    raise ValueError('Completion requires concrete result')
            values={k:v for k,v in a.items() if k in FIELDS}
            if operator and old['status']=='completed' and a.get('status')!='completed':values.pop('evidence',None)
            if not values:
                self.s.execute('INSERT OR IGNORE INTO task_sources VALUES(?,?,?)',(self.b,id,source))
                return id
            sql=','.join(f'{k}=?' for k in values)
            self.s.execute(f'UPDATE tasks SET {sql},revision=revision+1 WHERE business=? AND id=?',(*values.values(),self.b,id))
        self.s.execute('INSERT OR IGNORE INTO task_sources VALUES(?,?,?)',(self.b,id,source))
        return id

    def tick(self):
        now=self.now(); interval=self.c.get('reminder_hours',24)*3600
        today=datetime.fromtimestamp(now,self.tz).date().isoformat()
        with self.s.transaction():
            for t in self.tasks():
                if t['status'] in ('proposed','completed'): continue
                last=max(t['created'],t['last_reply'] or 0,t['last_reminder'] or 0)
                if now-last<interval: continue
                overdue=t['deadline'] and t['deadline']<today
                recipient=t['owner'] or self.c['admins'][0]
                text=f"Задача №{t['public_number']} · {t['title']}: "+('срок прошёл. ' if overdue else '')+'Какой результат? Что мешает и какой следующий шаг?'
                bucket=int(now//interval)
                if self.s.queue(self.b,recipient,text,f"reminder:{t['id']}:{bucket}",now,t['id']):
                    self.s.execute('UPDATE tasks SET last_reminder=? WHERE business=? AND id=?',(now,self.b,t['id']))

    def summary(self):
        today=datetime.fromtimestamp(self.now(),self.tz).date().isoformat()
        tasks=self.tasks()
        def lines(items): return ('; '.join(f"№{t['public_number']} {t['title'][:100]}" for t in items[:8]) + (f' (+{len(items)-8})' if len(items)>8 else '')) or 'нет'
        return ('Выполнено: '+lines([t for t in tasks if t['status']=='completed'])+'\nЗадерживается: '+
                lines([t for t in tasks if t['status']!='completed' and t['deadline'] and t['deadline']<today])+
                '\nНужно решение: '+lines([t for t in tasks if t['status'] in ('blocked','proposed') or not t['owner'] or not t['deadline']]))

    def queue_summary(self, key):
        with self.s.transaction():
            for recipient in ([self.c['meeting_recipient']] if self.c.get('meeting_recipient') else self.c['admins']):
                self.s.queue(self.b,recipient,self.summary(),f'summary:{key}:{recipient}',self.now())
