"""Explicit, transactional import of reviewed task-only exports; no remote writes."""
import json
import re
from datetime import datetime
from urllib.parse import quote
from .engine import fingerprint

LEGACY_HEADERS=['ID','Задача','Ответственный','Подтвержденное состояние','Следующее действие',
                'Критерий готовности','Статус','Срок / период','Доказательство / ссылка','Комментарий']
STATUS={'К проверке':'confirmed','Готов материал':'in_progress','В работе':'in_progress',
        'Выполнено':'completed','Ждём данные':'blocked'}

def prepare(rows,config,source):
    if not rows or rows[0]!=LEGACY_HEADERS: raise ValueError('Unexpected legacy task headers')
    if not source.strip(): raise ValueError('Source identifier required')
    people={v['name'].strip().casefold():k for k,v in config['employees'].items()}
    result=[];seen=set()
    for index,raw in enumerate(rows[1:],2):
        row=[str(x).strip() if x is not None else '' for x in (raw+['']*10)[:10]]
        if not any(row): continue
        if not re.fullmatch(r'[1-9][0-9]*',row[0]) or row[0] in seen: raise ValueError('Invalid/duplicate legacy ID')
        seen.add(row[0])
        if not row[1] or row[6] not in STATUS: raise ValueError('Missing title or invalid legacy status')
        deadline=None
        for fmt in ('%Y-%m-%d','%d.%m.%Y'):
            try:
                parsed=datetime.strptime(row[7],fmt)
                if parsed.strftime(fmt)==row[7]: deadline=parsed.date().isoformat()
            except ValueError: pass
        owner=people.get(row[2].casefold());status=STATUS[row[6]];issues=[]
        if not owner: issues.append('Ответственный требует подтверждения: '+(row[2] or 'не указан'))
        if not deadline: issues.append('Срок требует уточнения: '+(row[7] or 'не указан'))
        if status=='completed' and (len(row[8])<12 or 'почти' in row[8].casefold()):
            status='in_progress';issues.append('Прежний статус Выполнено требует подтверждённого результата')
        if not row[5]: issues.append('Критерий завершения не указан')
        result.append({'legacy_id':row[0],'row':index,'title':row[1],'owner':owner,'deadline':deadline,
            'next_step':row[4],'criterion':row[5],'status':status,'evidence':row[8],
            'raw':row,'issues':issues,'source':source})
    return result

def import_tasks(engine,rows,source,apply=False):
    prepared=prepare(rows,engine.c,source)
    if not apply:
        return {'mode':'preview','tasks':len(prepared),'needs_review':sum(bool(t['issues']) for t in prepared),
                'unknown_owner':sum(t['owner'] is None for t in prepared),'unknown_deadline':sum(t['deadline'] is None for t in prepared)}
    added=0;skipped=0
    with engine.s.transaction():
        for t in prepared:
            # Namespace stable per export source; repeat import preserves subsequent task edits.
            source_id='legacy:'+fingerprint(source+':'+t['legacy_id'])
            exists=engine.s.execute('SELECT task_id FROM task_sources WHERE business=? AND source_id=?',(engine.b,source_id)).fetchone()
            if exists: skipped+=1;continue
            id='l'+fingerprint(source_id)[:11]
            if engine.s.execute('SELECT 1 FROM tasks WHERE business=? AND id=?',(engine.b,id)).fetchone():
                raise ValueError('Import ID collision')
            engine.s.execute('INSERT INTO sources(business,id,body,link) VALUES(?,?,?,?)',
                (engine.b,source_id,json.dumps(t['raw'],ensure_ascii=False),source+f' (строка {t["row"]})'))
            engine.s.execute('INSERT INTO tasks(business,id,title,owner,deadline,next_step,criterion,status,source_id,evidence,created) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (engine.b,id,t['title'],t['owner'],t['deadline'],t['next_step'],t['criterion'],t['status'],source_id,t['evidence'],engine.now()))
            engine.s.execute('INSERT INTO task_sources VALUES(?,?,?)',(engine.b,id,source_id))
            engine.s.audit(engine.b,'legacy_import',{'task':id,'legacy_id':t['legacy_id'],'issues':t['issues']},engine.now())
            added+=1
    # No messages or remote writes during import. Start reminders only after reviewed setup.
    return {'mode':'applied','added':added,'skipped':skipped}
