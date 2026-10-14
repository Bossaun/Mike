import pytest
from goldway.model import response_schema


@pytest.mark.parametrize('question',['А с тобой можно общаться ?','Как тобой пользоваться?','Как такой пользоваться ?','Можно','Что ты умеешь?'])
def test_basic_question_gets_direct_reply_without_model(env,seed,question):
    e,s,c,clock=env
    class NeverCall:
        def propose(self,context):raise AssertionError('Basic question must not call model')
    e.model=NeverCall();e.reply('faq','alen',question,channel='bot');e.process('faq')
    out=s.rows("SELECT * FROM outbox WHERE dedupe='clarify:faq'")[0]
    assert len(out['text'])>20 and 'оператор спрашивает' not in out['text']
    assert out['preferred_transport']=='bot'
    assert e.tasks()[0]['status']=='confirmed' and e.tasks()[0]['last_reply'] is None


def test_model_answer_is_sent_instead_of_internal_summary(env):
    e,s,c,*_=env
    e.model.result={'actions':[],'summary':'Оператор просит помочь с текстом.',
                    'reply_text':'Конечно. Для кого нужно написать текст и о каком товаре?'}
    e.reply('chat','boss','Помоги написать текст о товаре',channel='bot');e.process('chat')
    out=s.rows("SELECT * FROM outbox WHERE dedupe='clarify:chat'")[0]
    assert out['text']==e.model.result['reply_text']
    assert out['text']!=e.model.result['summary']
    assert not e.tasks()


@pytest.mark.parametrize('reply_text',[None,'',' '*3,7,'x'*3001])
def test_missing_or_invalid_direct_answer_does_not_send_summary(env,reply_text):
    e,s,c,*_=env;e.model.result={'actions':[],'summary':'Оператор спрашивает.', 'reply_text':reply_text}
    e.reply('chat','boss','Помоги написать текст о товаре',channel='bot')
    with pytest.raises(ValueError,match='direct reply_text'):e.process('chat')
    out=s.rows('select * from outbox')
    assert len(out)==1 and 'не могу его обработать' in out[0]['text']
    assert e.model.result['summary'] not in out[0]['text']


def test_conversation_schema_requires_answer_and_disallows_actions():
    schema=response_schema({'kind':'conversation','employees':{'boss':'Synthetic'}})
    assert 'reply_text' in schema['required']
    assert schema['properties']['actions']['maxItems']==0
    assert 'reply_text' not in response_schema({'kind':'transcript'})['properties']


def test_model_failure_is_acknowledged_once_without_false_success(env):
    e,s,c,*_=env
    class Unavailable:
        def propose(self,context):raise ValueError('Daily model budget reached')
    e.model=Unavailable();e.reply('chat','boss','Помоги написать текст о товаре',channel='bot')
    for _ in range(2):
        with pytest.raises(ValueError):e.process('chat')
    out=s.rows('select * from outbox')
    assert len(out)==1 and out[0]['preferred_transport']=='bot'
    assert 'не могу его обработать' in out[0]['text']
    assert not e.tasks()
