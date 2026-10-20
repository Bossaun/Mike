"""Current delivered offer -> checked catalogue -> real capture delivery/memory."""
import pytest
from test_catalog_question_isolation import c
from goldway.intelligence import reader_key
from goldway.telegram import Dispatcher
from goldway.answer_citations import _resolve

NAMES=['PUSY','VOIS','The Act']
FIRST='В публичной выборке есть бренды:\n1. PUSY\n2. VOIS\n3. The Act\nМогу пояснить каждый по порядку.'


@pytest.mark.parametrize('channel',['bot','business'])
@pytest.mark.parametrize('expire',[False,True])
def test_actual_engine_delivers_explanation_instead_of_second_promise(c,channel,expire):
    for i,name in enumerate(NAMES):
        c.e.intelligence.index.upsert(dict(source_id='synthetic-public',document_id='brand-'+str(i),
            title=name+': публичные позиции каталога',url='https://gw-api.abdulwaheedaziz8.workers.dev?api=catalog',
            text='Публичный каталог GOLDWAY. В поле brand указано: '+name+'.\nПозиции каталога:\n'+name+' "TEST & PRODUCT"\nЭто публичные названия товаров, не данные об остатках, оплатах или ответственном.',
            readers=[reader_key('GOLDWAY','test_operator')],observed_at=c.clock[0]-1,
            permission_expires_at=c.clock[0]+600,confirmed=True))
    contexts=[]
    class Model:
        def propose(self,context):
            contexts.append(context)
            if expire and len(contexts)==2:c.clock[0]+=1000
            return dict(actions=[],summary='Synthetic accepted offer',intent='conversation',
                reply_text=FIRST if len(contexts)==1 else 'Хорошо, расскажу подробнее: 1. PUSY, 2. VOIS, 3. The Act.',
                dialogue_state=dict(topic='GOLDWAY',entities=NAMES,pending_offer=dict(kind='explain',text='Пояснить три бренда по порядку') if len(contexts)==1 else None,task_id=None),
                knowledge_mode='grounded',evidence_ids=[x['id'] for x in context['knowledge']['evidence']])
    c.e.model=Model()
    for i,text in enumerate(['Что есть в публичном каталоге GOLDWAY: PUSY, VOIS, The Act?','Давай'],1):
        c.clock[0]+=1
        source='telegram:1:'+str(i) if channel=='bot' else 'business:conn:1:'+str(i)
        assert c.e.reply('catalog-explain-'+str(i),'test_operator',text,channel=channel,source_id=source,message_at=c.clock[0])
        if expire and i==2:
            with pytest.raises(ValueError):c.e.process('catalog-explain-'+str(i))
            Dispatcher(c.s,c.c,c.api,now=lambda:c.clock[0]).flush()
            assert all('TEST' not in x['text'] for x in c.api.sent)
            assert c.s.execute('SELECT state FROM events WHERE id=?',('catalog-explain-2',)).fetchone()[0]=='failed'
            assert not c.e.tasks()
            return
        c.e.process('catalog-explain-'+str(i))
        Dispatcher(c.s,c.c,c.api,now=lambda:c.clock[0]).flush()
        assert c.s.execute('SELECT state FROM events WHERE id=?',('catalog-explain-'+str(i),)).fetchone()[0]=='done'
    assert len(contexts)==2 and contexts[1]['followup']['kind']=='continue_last_turn'
    actual=c.api.sent[-1]['text']
    assert 'расскажу подробнее' not in actual.casefold()
    for i,name in enumerate(NAMES,1):
        assert str(i)+'. '+name in actual and name+' "TEST & PRODUCT"' in actual
    assert '&quot;' not in actual and '&amp;' not in actual
    assert actual.count('публичные позиции каталога —')==3
    out=c.s.rows('SELECT * FROM outbox ORDER BY id')
    assert len(out)==2 and all(x['state']=='sent' and x['transport']==channel and x['task_id'] is None for x in out)
    assert all(x['parse_mode'] is None for x in out)
    payload=dict(sender='test_operator',channel=channel,source_id=source)
    resolved=_resolve(c.s,c.c,payload,c.clock[0],include_answer=True)
    assert resolved['answer']==actual and len(resolved['citations'])==3
    assert not c.e.tasks() and not c.s.rows('SELECT 1 FROM usage')
