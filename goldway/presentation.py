"""Telegram HTML rendering; data is escaped and task numbers are persistent."""
from html import escape

STATUS={'proposed':'Черновик','confirmed':'Поручение','in_progress':'В работе','blocked':'Есть препятствие','completed':'Выполнено'}

def clean(value,limit=350):
    text=' '.join(str(value or '').split())
    if len(text)>limit:text=text[:limit-1].rstrip()+'…'
    return escape(text)

def task_card(task,config):
    number=task.get('public_number')
    reference=f'Задача №{number}' if number else 'Задача '+task['id']
    person=config['employees'].get(task.get('owner'),{}).get('name')
    parts=[f"<b>{clean(reference)} · {clean(STATUS[task['status']])}</b>",f"<b>{clean(task['title'],220)}</b>",
           f"👤 {clean(person) if person else 'Ответственного нужно уточнить'}"]
    if task.get('deadline'):parts.append('📅 '+clean(task['deadline']))
    if task.get('next_step'):parts.append('➡️ Следующий шаг: '+clean(task['next_step'],220))
    if task.get('criterion'):parts.append('✅ Готово, когда: '+clean(task['criterion'],180))
    return '\n'.join(parts)

def meeting_messages(tasks,config,*,review=None):
    if review is not None:
        if list(tasks):raise ValueError("structured_review_requires_no_legacy_tasks")
        from .meeting_review_render import render_meeting_review
        return render_meeting_review(review,config)
    tasks=list(tasks)
    if not tasks:return ['<b>📋 GOLDWAY · итог планёрки</b>\nНовых поручений не выделено.']
    unknown=sum(not t.get('owner') for t in tasks);undated=sum(not t.get('deadline') for t in tasks)
    introduction=(f"<b>📋 GOLDWAY · итог планёрки</b>\nПунктов: {len(tasks)} · Без ответственного: {unknown}\n"
                  + ('Точные сроки не названы.' if undated==len(tasks) else (f'Срок нужно уточнить для {undated} задач.' if undated else 'Сроки указаны в карточках.'))
                  + ('\n\nЧерновики — требуется подтверждение руководителя.\n\n' if all(t['status']=='proposed' for t in tasks)
                     else '\n\nСтатус каждого пункта указан в карточке. Черновики требуют подтверждения руководителя.\n\n'))
    footer='\n\n<b>Как уточнить</b>\nНапишите обычным текстом, кто отвечает и когда будет результат. Назовите задачу или ответьте на её карточку; номер можно использовать по желанию.'
    pages=[];page=introduction;count=0
    for task in tasks:
        card=task_card(task,config)
        if count>=3 or len((page+'\n\n'+card+footer).encode('utf-16-le'))//2>3500:
            pages.append(page);page='<b>📋 GOLDWAY · продолжение</b>\n\n';count=0
        page+=('\n\n' if count else '')+card;count+=1
    pages.append(page+footer)
    return pages
