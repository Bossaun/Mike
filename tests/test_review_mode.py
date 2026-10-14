from goldway.telegram import Dispatcher, DeliveryError
from test_telegram import API, chat
import pytest

def test_review_mode_creates_drafts_and_one_summary(env,action):
    e,s,c,*_=env;c['review_mode']=True
    e.model.result={'actions':[action],'summary':'Разобрано'}
    id=e.transcript(action['evidence']);e.process(id);e.process(id)
    assert e.tasks()[0]['status']=='proposed'
    out=s.rows('SELECT * FROM outbox')
    assert len(out)==1 and out[0]['recipient']=='boss'
    assert 'Черновики' in out[0]['text'] and 'требуется' in out[0]['text']

def test_pilot_allowlist_blocks_other_recipients_even_with_valid_chat(env):
    e,s,c,clock=env;c['allowed_live_recipients']=['boss'];chat(s,c,'alen',clock[0])
    with pytest.raises(DeliveryError,match='allowlist'):
        Dispatcher(s,c,API(),lambda:clock[0]).route('alen')

def test_speaker_alias_grounding(env,action):
    e,s,c,*_=env;c['speaker_aliases']={'Speaker 2':'alen'}
    action['owner_evidence']='Speaker 2: подготовить список';action['evidence']='Speaker 2: подготовить список до 2026-10-20'
    e.model.result={'actions':[action]};e.process(e.transcript(action['evidence']))
    assert e.tasks()[0]['owner']=='alen'

def test_source_lines_preserve_literal_original(env):
    e,s,c,*_=env
    a={'op':'create','title':'Сверить накладные','owner':None,'deadline':None,'next_step':'Сверить','criterion':'Список','status':'confirmed','evidence_lines':[2]}
    e.model.result={'actions':[a]};e.process(e.transcript('Планёрка\nНужно сверить накладные.'))
    assert e.tasks()[0]['evidence']=='Нужно сверить накладные.'

@pytest.mark.parametrize('refs',[[0],[100],['2'],[True],[],[1,1,1,1]])
def test_invalid_source_line_reference_rejected(env,refs):
    e,*_=env;e.model.result={'actions':[{'op':'create','title':'Задача','next_step':'Шаг','criterion':'Результат','status':'confirmed','evidence_lines':refs}]}
    with pytest.raises(ValueError,match='reference'):e.process(e.transcript('Строка'))
    assert not e.tasks()

def test_review_duplicate_does_not_downgrade_confirmed_task(env,seed,action):
    e,s,c,*_=env;c['review_mode']=True;e.model.result={'actions':[action]}
    e.process(e.transcript(action['evidence']+' Дополнительная реплика'))
    assert e.tasks()[0]['status']=='confirmed'

def test_structured_update_null_placeholders_do_not_clear_fields(env,seed):
    e,*_=env
    text='Проверены накладные, следующий шаг сверить номера.'
    a={'op':'update','task_id':seed['id'],'title':None,'owner':None,'deadline':None,'next_step':'Сверить номера','criterion':None,'status':None,'evidence_lines':[1],'owner_lines':[],'deadline_lines':[],'set_fields':['next_step']}
    e.model.result={'actions':[a]};e.reply('update','alen',text,seed['id']);e.process('update')
    t=e.tasks()[0]
    assert t['owner']=='alen' and t['deadline']=='2026-10-20' and t['next_step']=='Сверить номера'

def test_nonadjacent_source_lines_are_exact_excerpts(env):
    e,*_=env
    e.model.result={'actions':[{'op':'create','title':'Сверить','next_step':'Шаг','criterion':'Результат','status':'confirmed','owner':None,'deadline':None,'owner_lines':[],'deadline_lines':[],'evidence_lines':[1,3]}]}
    e.process(e.transcript('Нужно сверить накладные\nПосторонняя реплика\nИ составить список номеров'))
    assert 'Нужно сверить накладные' in e.tasks()[0]['evidence']
    assert 'И составить список номеров' in e.tasks()[0]['evidence']
    assert 'Посторонняя реплика' not in e.tasks()[0]['evidence']

def test_unproven_owner_stays_unknown_in_meeting_review(env,action):
    e,s,c,*_=env;c['review_mode']=True
    action['owner_evidence']='Подготовить список';action['evidence']='Подготовить список до 2026-10-20'
    e.model.result={'actions':[action]};e.process(e.transcript(action['evidence']))
    assert e.tasks()[0]['owner'] is None
    assert s.rows("SELECT * FROM audit WHERE kind='owner_requires_review'")

def test_prelaunch_history_not_interpreted_as_new_meeting(env):
    from goldway.telegram import Updates
    from test_telegram import API
    e,s,c,clock=env;c['ignore_messages_before']=clock[0]
    u=Updates(e,API(),999)
    u.handle({'update_id':4,'message':{'from':{'id':1},'chat':{'id':11,'type':'private'},'message_id':1,'date':int(clock[0]-100),'text':'Старая планёрка'}})
    assert not s.rows('SELECT * FROM events')
