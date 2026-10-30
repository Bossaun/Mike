"""The assistant records employee problem descriptions; people need not edit tasks."""
import re


def setup(work):
    work.s.execute('''CREATE TABLE IF NOT EXISTS group_work_findings(
      business TEXT,chat_id INTEGER,source_key TEXT,task_id TEXT,actor TEXT,
      quote TEXT,scope TEXT,created REAL,
      PRIMARY KEY(business,chat_id,source_key,task_id))''')


def visible(work,actor,private):
    rows=work.s.rows('SELECT source_key,task_id,actor,quote,scope FROM group_work_findings WHERE business=? AND chat_id=? ORDER BY created DESC LIMIT 30',(work.e.b,work.c['chat_id']))
    return [dict(r) for r in rows if r['scope']=='group' or (private and (r['actor']==actor or actor in work.e.c['admins']))][:8]


def record(work,proposals,messages,allowed_ids):
    if not work.current() or not isinstance(proposals,list):return []
    notes=[];source={m['id']:m for m in messages}
    for p in proposals[:3]:
        if not isinstance(p,dict) or set(p)!={'task_id','message_id','quote'}:continue
        m=source.get(p['message_id']);quote=p['quote'];tid=p['task_id']
        if not m or m['actor'] not in work.c['participants'] or tid not in allowed_ids:continue
        if not isinstance(quote,str) or not 4<=len(quote)<=1000 or quote not in m['text']:continue
        key=('forwarded:' if m.get('forwarded') else 'telegram:')+str(m['id'])
        # Resolve an unclassified observation without duplicating its source.
        if work.s.execute("UPDATE group_work_findings SET task_id=? WHERE business=? AND chat_id=? AND source_key=? AND task_id=''",(tid,work.e.b,work.c['chat_id'],key)).rowcount:
            notes.append('Проблему записал. Учёт веду сам; добавлять её в задачи вам не нужно.')
            continue
        # This is an attributed observation, never completion, assignment or permission.
        if work.s.execute('INSERT OR IGNORE INTO group_work_findings VALUES(?,?,?,?,?,?,?,?)',
            (work.e.b,work.c['chat_id'],('forwarded:' if m.get('forwarded') else 'telegram:')+str(m['id']),tid,m['actor'],quote,
             'private' if m.get('private') else 'group',work.e.now())).rowcount:
            notes.append('Проблему записал: '+quote[:250]+'. Учёт веду сам; добавлять её в задачи вам не нужно.')
    return notes


def reply_text(text,private):
    # The runtime has no CRM/site execution tool; do not promise such execution.
    if re.search(r'\b(?:проверю|зайду|исправлю|обновлю|добавлю|загружу|сверю|проверим)\b[^.!?\n]{0,100}(?:CRM|CRM|сайт|таблиц|карточк)',text,re.I):
        return 'Сам проверить или изменить CRM и сайт сейчас не могу. Могу учесть описание проблемы и поступивший результат проверки.'
    # Last-mile guard against outsourcing the assistant's own bookkeeping.
    bookkeeping=r'\b(?:добав(?:ь(?:те)?|ить)|внес(?:и(?:те)?|ти)|запиш(?:и(?:те)?)|записать|созда(?:й(?:те)?|ть)|оформ(?:и(?:те)?|ить))\b[^.!?\n]{0,90}\b(?:задач\w*|поручени\w*|C\d{2})\b'
    if re.search(bookkeeping,text,re.I):
        return 'Учёт задач веду сам. Опишите, что не работает, или пришлите фото — этого достаточно.'
    # An addressed personal question or task is not a public-group response.
    if not private and re.search(r'(?:^|[.!?\n])\s*(?:Ал[эе]н|Аллен|Даша(?:\s+IT)?|Настя|Фрост|Майк)\s*[,!:]',text,re.I):
        return 'Сообщение учёл в контексте рабочего обсуждения. Индивидуальные поручения и уточнения — в личной переписке.'
    return text


def capture_problem(work,message,visible_ids):
    """Retain explicit complaints even if model classification fails or omits findings."""
    text=message.get('text','')
    if not work.current() or message.get('actor') not in work.c['participants']:return False
    if not re.search(r'не (?:видно|работает|отображается|появляется|отвечает|получается)|отсутству|неверн|неправильн|ошибк|сломал|ломается|нет (?:фото|картин|товар)|надо (?:добавить|исправить|заменить)',text,re.I):return False
    key=('forwarded:' if message.get('forwarded') else 'telegram:')+str(message['id'])
    if work.s.execute('SELECT 1 FROM group_work_findings WHERE business=? AND chat_id=? AND source_key=?',(work.e.b,work.c['chat_id'],key)).fetchone():return False
    focus=[i for i in message.get('focus',[]) if i in visible_ids]
    tid=focus[0] if len(focus)==1 else ''
    return bool(work.s.execute('INSERT OR IGNORE INTO group_work_findings VALUES(?,?,?,?,?,?,?,?)',
        (work.e.b,work.c['chat_id'],key,tid,message['actor'],text[:1000],'private' if message.get('private') else 'group',work.e.now())).rowcount)
