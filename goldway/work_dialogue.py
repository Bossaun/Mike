"""Bounded recipient-specific dialogue and owner-authored task intake.

Model proposals never grant access. Private-origin tasks cannot use group fallback.
"""
import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo


def reference_tasks(work,text):
    found=set(re.findall(r'\bC\d{2}\b',text.upper()))
    return found | {t['id'] for t in work.tasks() if t['title'].casefold() in text.casefold()}


def visible(work, actor, private):
    tasks=work.tasks()
    if private:
        return tasks if actor in work.e.c['admins'] else [t for t in tasks if actor in (t['owner'],t['reviewer'])]
    return [t for t in tasks if t['origin_scope']=='group']


def conversation(work, message):
    private=message.get('private');actor=message['actor'];items=[]
    if private:
        for r in work.s.rows("SELECT dedupe,text,created FROM outbox WHERE business=? AND recipient=? AND state='sent' AND (dedupe LIKE 'work-%' OR dedupe LIKE 'delivery-notice:%') ORDER BY id DESC LIMIT 8",(work.e.b,actor)):
            items.append({'role':'assistant','text':r['text'][:800],'at':r['created'],'kind':'notification' if r['dedupe'].startswith('delivery-notice:') else 'reply'})
    else:
        for r in work.s.rows("SELECT text,created FROM group_work_sends WHERE business=? AND chat_id=? AND state='sent' ORDER BY created DESC LIMIT 6",(work.e.b,work.c['chat_id'])):
            items.append({'role':'assistant','text':r['text'][:800],'at':r['created'],'kind':'reply'})
    for r in work.s.rows("SELECT payload FROM group_work_events WHERE business=? AND chat_id=? AND message_id!=? AND (state='done' OR json_extract(payload,'$.photo_state')='done') ORDER BY rowid DESC LIMIT 40",(work.e.b,work.c['chat_id'],message['id'])):
        m=json.loads(r['payload'])
        if bool(m.get('private'))!=bool(private) or (private and m['actor']!=actor):continue
        if m.get('at',0)>message.get('at',work.e.now()):continue
        items.append(dict({'role':'user','actor':m['actor'],'text':m['text'][:1800],'at':m.get('at',0),'kind':'message'}, **({'photo_reading':{k:v[:2000] for k,v in m['photo_reading'].items()}} if m.get('photo_reading') else {})))
    return sorted([x for x in items if x['at']<=message.get('at',work.e.now())],key=lambda x:x['at'])[-10:]


def latest_focus(work, actor, private, before=None):
    before=work.e.now() if before is None else before
    candidates=[]
    for r in work.s.rows("SELECT message_id,payload,response FROM group_work_events WHERE business=? AND chat_id=? AND state='done' AND response IS NOT NULL ORDER BY rowid DESC LIMIT 30",(work.e.b,work.c['chat_id'])):
        m=json.loads(r['payload'])
        if bool(m.get('private'))!=bool(private) or m['actor']!=actor:continue
        if private:
            sent=work.s.execute("SELECT created FROM outbox WHERE business=? AND recipient=? AND dedupe=? AND state='sent'",(work.e.b,actor,'work-reply:reply:'+str(r['message_id']))).fetchone()
        else:
            sent=work.s.execute("SELECT created FROM group_work_sends WHERE business=? AND chat_id=? AND key=? AND state='sent'",(work.e.b,work.c['chat_id'],'reply:'+str(r['message_id']))).fetchone()
        if sent and sent['created']<=before:candidates.append((sent['created'],json.loads(r['response']).get('focus_task_ids',[])))
    if private:
        for r in work.s.rows("SELECT text,created,dedupe FROM outbox WHERE business=? AND recipient=? AND dedupe LIKE 'work-followup:%' AND state='sent' ORDER BY id DESC LIMIT 8",(work.e.b,actor)):
            ids=reference_tasks(work,r['text'])
            match=re.search(r':(C\d{2})$',r['dedupe'])
            if match:ids.add(match[1])
            if r['created']<=before:candidates.append((r['created'],sorted(ids)))
    return max(candidates,key=lambda v:v[0])[1] if candidates else []


def instruction_for(work, message, owner):
    text=message['text']
    # Intent still comes from the language model; these checks bind action and recipient
    # to the actual instruction so a notification/question cannot invent an assignment.
    plain=re.sub(r'«[^»]*»|"[^"\n]*"','',text)
    if re.search(r'\b(?:не\s+(?:нужно|надо|хочу|создавай|ставь|поручай|отправляй|поручи|попроси|напоминай|давай)|например|допустим|представь)\b',plain,re.I):return False
    imperative=r'\b(?:поручи(?:те)?|попроси(?:те)?|поставь(?:те)?|создай(?:те)?|заведи(?:те)?|добавь(?:те)?|напомни(?:те)?|пусть|сделай(?:те)?|проверь(?:те)?)\b'
    request=r'\b(?:можешь|можете|мог\s+бы|могли\s+бы|давай(?:те)?|прошу)\b.*\b(?:поручить|попросить|поставить|создать|добавить|напомнить|проверить)\b'
    # Questions about work and reports of someone else's requests are not orders.
    # Keep ordinary polite requests, but never turn a descriptive past-tense verb
    # ("Алэн попросил") into a new manager instruction.
    direct=bool(re.search(imperative,plain,re.I) or re.search(request,plain,re.I))
    necessity=bool(re.search(r'\b(?:надо|нужно|хочу)\b',plain,re.I))
    question=bool('?' in plain or re.search(r'^\s*(?:что|какие?|кому|когда|зачем|почему|как|сколько)\b',plain,re.I))
    narrative=bool(re.search(r'\b(?:сказал[аи]?|попросил[аи]?|написал[аи]?|говорил[аи]?|поручил[аи]?|спросил[аи]?)\b',plain,re.I))
    if narrative:return False
    if not direct and (not necessity or question or narrative):return False
    aliases={'alen':r'ал[эе]н(?:у|а|ом)?|аллен(?:у|а|ом)?','dasha_it':r'даш(?:а|е|у|ей)|даша\s*it','nastya':r'наст(?:я|е|ю|ей)|анастаси(?:я|и|ю|ей)','frost':r'фрост(?:у|а|ом)?','test_operator':r'мне|меня|майк(?:у|а)?'}
    named={key for key,pattern in aliases.items() if re.search(r'\b(?:'+pattern+r')\b',plain,re.I)}
    if named:return owner in named
    focus=message.get('focus',[])
    return len(focus)==1 and owner==next((t['owner'] for t in work.tasks() if t['id']==focus[0]),None)


def create_tasks(work, proposals, message):
    if not work.current() or message['actor'] not in work.e.c['admins']:return []
    if not isinstance(proposals,list):return []
    from .intake import TEAM_RECIPIENTS
    notes=[];contacted=set()
    for index,p in enumerate(proposals[:3]):
        if not isinstance(p,dict) or set(p)!={'owner','title','ask','criterion','quote'}:continue
        if p['owner'] not in TEAM_RECIPIENTS or p['owner'] not in work.c['participants']:continue
        if any(not isinstance(p[k],str) or not 1<=len(p[k].strip())<=limit for k,limit in [('title',100),('ask',700),('criterion',350),('quote',2000)]):continue
        if p['quote'] not in message['text'] or not instruction_for(work,message,p['owner']):continue
        # Full actual instruction, never a quoted receipt, forward, negation or hypothetical.
        if re.search(r'\b(?:не\s+(?:создавай|ставь|поручай|отправляй)|например|допустим|представь)\b',message['text'],re.I):continue
        scope='private' if message.get('private') else 'group'
        with work.s.transaction():
            old=work.s.execute('SELECT id FROM group_work_created WHERE business=? AND chat_id=? AND event_id=? AND proposal_index=?',(work.e.b,work.c['chat_id'],message['id'],index)).fetchone()
            if old:continue
            if any(t['owner']==p['owner'] and t['title'].strip().casefold()==p['title'].strip().casefold() and t['state']!='done' for t in work.tasks()):continue
            if len(work.tasks())>=50:
                notes.append('В списке уже 50 поручений. Сначала разберём текущие, чтобы не терять задачи.');break
            numbers=[int(t['id'][1:]) for t in work.tasks()]
            tid='C'+str(max(numbers,default=0)+1).zfill(2)
            if int(tid[1:])>99:continue
            definition=dict(id=tid,owner=p['owner'],title=p['title'],ask=p['ask'],criterion=p['criterion'],reviewer=message['actor'],source='Прямое поручение Майка в Telegram',active=True,daily=False)
            work.s.execute('INSERT INTO group_work_tasks(business,chat_id,id,definition,updated,origin_scope) VALUES(?,?,?,?,?,?)',(work.e.b,work.c['chat_id'],tid,json.dumps(definition,ensure_ascii=False),work.e.now(),scope))
            work.s.execute('INSERT INTO group_work_created VALUES(?,?,?,?,?,?,?,?)',(work.e.b,work.c['chat_id'],message['id'],index,tid,message['actor'],p['quote'],work.e.now()))
        who=work.c['participants'][p['owner']]['name']
        # Assignment is a new explicit instruction; no old followup replay.
        outcome=work.deliver_task(tid) if p['owner'] not in contacted else 'later'
        contacted.add(p['owner'])
        note='Записал поручение для '+who+': '+p['title']+'. '
        note+=('Передаю в личный рабочий канал; подтверждение отправки придёт отдельно.' if outcome=='queued' else 'Вернёмся к ней после текущей задачи.' if outcome=='later' else 'Сохранено. Личная отправка пока недоступна; сообщу, когда получится отправить.')
        notes.append(note)
    return notes


def waiting_for_reply(work, owner):
    """Do not stack an assignment on a pending message or unanswered question."""
    today=datetime.fromtimestamp(work.e.now(),ZoneInfo(work.d.c['timezone'])).date()
    replies=[json.loads(r['payload']) for r in work.s.rows("SELECT payload FROM group_work_events WHERE business=? AND chat_id=? AND state='done' ORDER BY rowid DESC LIMIT 80",(work.e.b,work.c['chat_id']))]
    last_inbound=max((m.get('at',0) for m in replies if m['actor']==owner and m.get('private')),default=0)
    for row in work.s.rows("SELECT o.state,COALESCE((SELECT MAX(d.at) FROM deliveries d WHERE d.outbox_id=o.id AND d.state='sent'),o.created) AS delivered_at FROM outbox o WHERE business=? AND recipient=? AND (dedupe LIKE 'work-followup:%' OR dedupe LIKE 'work-reply:%') ORDER BY id DESC LIMIT 30",(work.e.b,owner)):
        if row['state'] in ('pending','sending','retry','blocked','unknown'):return True
        if row['state']=='sent' and row['delivered_at']>=last_inbound and datetime.fromtimestamp(row['delivered_at'],ZoneInfo(work.d.c['timezone'])).date()==today:return True
    return False


def dispatch_task(work, tid):
    t=next((t for t in work.tasks() if t['id']==tid),None)
    if not t or not work.current():return 'blocked'
    key='assignment:'+tid;dedupe='work-followup:'+key
    old=work.s.execute('SELECT state FROM outbox WHERE business=? AND dedupe=?',(work.e.b,dedupe)).fetchone()
    if old:return 'queued' if old['state'] in ('pending','sent','retry') else 'blocked'
    old_group=work.s.execute('SELECT state FROM group_work_sends WHERE business=? AND chat_id=? AND key=?',(work.e.b,work.c['chat_id'],key)).fetchone()
    if old_group:return 'group_sent' if old_group['state']=='sent' else 'blocked'
    if work.s.execute("SELECT 1 FROM outbox WHERE business=? AND recipient=? AND dedupe LIKE 'work-%' AND state='unknown'",(work.e.b,t['owner'])).fetchone():return 'blocked'
    if waiting_for_reply(work,t['owner']):return 'later'
    text=work.c['participants'][t['owner']]['name']+', Майк просит: '+t['ask']
    if len(text)>350:text=work.c['participants'][t['owner']]['name']+', нужно '+t['title']+'. Сможете взять это после текущей задачи?'
    from .delivery_notices import seal_queue
    route='private_only' if t['owner'] in work.e.c['admins'] else 'business_only'
    work.s.queue(work.e.b,t['owner'],text,dedupe,work.e.now(),preferred_transport=route)
    seal_queue(work.s,work.e.b,dedupe,work.path,work.c)
    return 'queued'
