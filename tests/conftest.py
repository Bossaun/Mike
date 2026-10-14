import copy
import json
from pathlib import Path
import pytest
from goldway.store import Store
from goldway.engine import Engine
from goldway.model import ScriptedModel

@pytest.fixture
def env(tmp_path):
    c=json.loads((Path(__file__).parents[1]/'examples/config.json').read_text())
    c['employees']['boss']['telegram_user_id']=1
    c['employees']['alen']['telegram_user_id']=2
    c['employees']['nastya']['telegram_user_id']=3
    c['business_account_user_id']=99
    c['live_send']=True
    c['review_mode']=False
    clock=[1791979200.0]
    s=Store(str(tmp_path/'state.sqlite'))
    model=ScriptedModel({'actions':[],'summary':'Уточните результат.'})
    e=Engine(s,c,model,lambda:clock[0])
    return e,s,c,clock

@pytest.fixture
def action():
    return {'op':'create','title':'Подготовить список отправленных товаров','owner':'alen',
            'owner_evidence':'Ален: подготовить список отправленных товаров',
            'deadline':'2026-10-20','deadline_evidence':'до 2026-10-20',
            'next_step':'Сверить накладные','criterion':'Список с номерами накладных',
            'status':'confirmed','evidence':'Ален: подготовить список отправленных товаров до 2026-10-20'}

@pytest.fixture
def seed(env,action):
    e,*_=env
    e.model.result={'actions':[copy.deepcopy(action)],'summary':'Одна задача'}
    event=e.transcript(action['evidence'])
    e.process(event)
    return e.tasks()[0]
