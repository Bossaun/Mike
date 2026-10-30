import httpx
import pytest
from goldway.message_layout import entities,rich_html,split_sections,units,send,cut
from goldway.telegram import Telegram,DeliveryError,Dispatcher
from goldway.delivery_notices import seal_queue
from test_telegram import API,chat


def test_utf16_entities_quote_and_heading():
    text='Отправлено: Алэн 😊\n\nПолучил фото 📷.\n\nКопия для вас.'
    result=entities(text)
    assert result[0]==dict(type='bold',offset=0,length=units('Отправлено: Алэн 😊'))
    quote=next(x for x in result if x['type']=='blockquote')
    assert quote['offset']==units('Отправлено: Алэн 😊\n\n')
    assert quote['length']==units('Получил фото 📷.')


def test_long_copy_collapses_but_ordinary_message_not_quoted():
    assert any(x['type']=='expandable_blockquote' for x in entities('Отправлено: Алэн\n\n'+'т'*400+'\n\nКопия для вас.'))
    assert entities('Алэн, фото получил. Что на нём нужно проверить?')==[]


def test_rich_html_escapes_untrusted_markup_and_preserves_content():
    output=rich_html('Отчёт <test>\n\nВ работе:\n<img src="https://evil">\n<tg-button>Кнопка</tg-button>')
    assert '<h2>Отчёт &lt;test&gt;</h2>' in output and '<h3>В работе</h3>' in output
    assert '<img' not in output and '<tg-button' not in output
    assert '&lt;img' in output and '&lt;tg-button' in output


def test_split_preserves_all_items_and_unicode():
    paragraphs=['Список '+str(i)+':\n'+'😊 задача '*30 for i in range(10)]
    result=split_sections('\n\n'.join(paragraphs),500)
    assert len(result)>2 and all(units(x)<=500 for x in result)
    assert ''.join(''.join(result).split())==''.join(''.join(paragraphs).split())
    assert cut('а😊б',2)=='а'


@pytest.mark.parametrize('state,detail,n',[
 ('failed','Telegram rejected request (400)',2),
 ('failed','Telegram rejected request (404)',2),
 ('unknown','network',1),('retry','Rate limited',1),
 ('failed','Telegram rejected request (403)',1)])
def test_rich_rejection_fallback_only_after_certain_safe_rejection(state,detail,n):
    class Fake:
        def __init__(self):self.calls=[]
        def call(self,method,**params):
            self.calls.append((method,params))
            if method=='sendRichMessage':raise DeliveryError(state,detail)
            return {'message_id':8}
    api=Fake();params=dict(chat_id=1,business_connection_id='unchanged',text='Отчёт\n\nДетали',disable_notification=True)
    if n==2:assert send(api,params,True)['message_id']==8
    else:
        with pytest.raises(DeliveryError):send(api,params,True)
    assert len(api.calls)==n
    assert all(x[1]['chat_id']==1 and x[1]['business_connection_id']=='unchanged' for x in api.calls)
    assert 'allow_paid_broadcast' not in api.calls[0][1]


def test_wire_entities_auto_but_existing_format_untouched():
    captured=[]
    def handler(request):
        import json
        captured.append(json.loads(request.content));return httpx.Response(200,json={'ok':True,'result':{'message_id':1}})
    api=Telegram('synthetic',httpx.Client(transport=httpx.MockTransport(handler)))
    api.call('sendMessage',chat_id=1,text='Главное:\n\nДетали')
    api.call('sendMessage',chat_id=1,text='<b>Главное</b>',parse_mode='HTML')
    assert captured[0]['entities'][0]['type']=='bold' and 'entities' not in captured[1]


def test_digest_details_wait_for_parent_and_previous_part(env,tmp_path):
    import json
    e,s,c,clock=env;api=API();chat(s,c,'boss',clock[0]);p=tmp_path/'guard.json';cfg={'enabled':True};p.write_text(json.dumps(cfg))
    for key in ['work-digest:today','work-digest:today:details:1','work-digest:today:details:2']:
        s.queue(e.b,'boss','Главное:\n\nДетали',key,clock[0],preferred_transport='bot');seal_queue(s,e.b,key,p,cfg)
    s.execute("UPDATE outbox SET state='unknown' WHERE dedupe='work-digest:today'")
    d=Dispatcher(s,c,api,lambda:clock[0]);d.flush()
    assert not api.calls
    s.execute("UPDATE outbox SET state='sent' WHERE dedupe='work-digest:today'")
    s.execute("UPDATE outbox SET state='unknown' WHERE dedupe='work-digest:today:details:1'")
    d.flush();assert not api.calls


def test_rich_send_uses_native_article_without_paid_flags():
    class Fake:
        def __init__(self):self.calls=[]
        def call(self,method,**params):self.calls.append((method,params));return {'message_id':9,'rich_message':{'blocks':[]}}
    api=Fake();assert send(api,dict(chat_id=1,text='Отчёт\n\nВ работе:\nПроверка карточки'),True)['message_id']==9
    method,params=api.calls[0]
    assert method=='sendRichMessage' and '<h2>' in params['rich_message']['html']
    assert 'text' not in params and 'allow_paid_broadcast' not in params
