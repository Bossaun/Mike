"""Native Telegram presentation; source text and delivery permissions stay separate."""
from html import escape


def units(text):
    return len(text.encode('utf-16-le')) // 2


def cut(text, limit):
    return text.encode('utf-16-le')[:limit*2].decode('utf-16-le', errors='ignore')


def entities(text):
    result=[]
    start=0
    for line in text.splitlines(keepends=True):
        title=line.rstrip('\r\n')
        if title and units(title)<=110 and (title.endswith(':') or
                (start==0 and title.startswith(('Отправлено:', 'Я отправил сообщение:', 'Задачи по косметике', 'Подробности по задачам')))):
            result.append(dict(type='bold',offset=units(text[:start]),length=units(title)))
        elif 0<title.find(':')<=60 and not title.lstrip().startswith(('http:','https:','tg:')):
            label=title[:title.index(':')+1]
            result.append(dict(type='bold',offset=units(text[:start]),length=units(label)))
        start+=len(line)
    if text.startswith(('Отправлено:', 'Я отправил сообщение:')) and '\n\n' in text:
        begin=text.index('\n\n')+2
        footer='\n\nКопия для вас.'
        end=len(text)-len(footer) if text.endswith(footer) else len(text)
        if end>begin:
            result.append(dict(type='expandable_blockquote' if units(text[begin:end])>300 else 'blockquote',
                               offset=units(text[:begin]),length=units(text[begin:end])))
    return sorted(result,key=lambda e:(e['offset'],-e['length']))


def rich_html(text):
    # Never interpret model/user HTML, URLs or media as tags, buttons or uploads.
    blocks=[]
    for i, paragraph in enumerate(text.split('\n\n')):
        lines=paragraph.splitlines()
        if not lines:continue
        if i==0:
            blocks.append('<h2>'+escape(lines.pop(0))+'</h2>')
        elif lines[0].endswith(':') and units(lines[0])<=110:
            blocks.append('<h3>'+escape(lines.pop(0)[:-1])+'</h3>')
        if lines:
            if len(lines)>1:
                blocks.append('<ul>'+''.join('<li>'+escape(line)+'</li>' for line in lines)+'</ul>')
            else:blocks.append('<p>'+escape(lines[0])+'</p>')
    return ''.join(blocks)


def split_sections(text, limit=3000):
    """Retain all text; prefer complete paragraphs, then lines, then bounded cuts."""
    chunks=[];current=''
    for paragraph in text.split('\n\n'):
        candidate=current+'\n\n'+paragraph if current else paragraph
        if units(candidate)<=limit:current=candidate;continue
        if current:chunks.append(current);current=''
        for line in paragraph.split('\n'):
            candidate=current+'\n'+line if current else line
            if units(candidate)<=limit:current=candidate;continue
            if current:chunks.append(current);current=''
            while units(line)>limit:
                head=cut(line,limit);chunks.append(head);line=line[len(head):]
            current=line
    if current:chunks.append(current)
    return chunks


def send(api, params, article=False):
    if article:
        request={k:v for k,v in params.items() if k not in ('text','entities','parse_mode','link_preview_options')}
        request['rich_message']={'html':rich_html(params['text'])}
        try:return api.call('sendRichMessage',**request)
        except Exception as exc:
            from .telegram import DeliveryError
            # An explicit rejection proves no message was created. Unknown/timeouts
            # and rate limits never trigger a second attempt or a different route.
            if not (isinstance(exc,DeliveryError) and exc.state=='failed' and
                    exc.detail in ('Telegram rejected request (400)','Telegram rejected request (404)')):raise
    return api.call('sendMessage',**params)
