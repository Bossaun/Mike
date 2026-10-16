"""New future-answer -> real synthetic delivery -> exact-citation boundary."""
import json
from types import SimpleNamespace
import pytest
from goldway.engine import Engine
from goldway.store import Store
from goldway.telegram import Dispatcher
from goldway.intelligence import reader_key,delivery_permitted
import goldway.answer_citations as citations

MEMBERS=['test_operator','frost','alen','nastya','dasha_it']
NOW=1792174600
PRIVATE='SYNTHETIC_PRIVATE_BODY_KEY_NEVER_IN_RECEIPT'

@pytest.fixture
def case(monkeypatch):
    s=Store(':memory:')
    c={'business':'GOLDWAY','timezone':'Europe/Moscow',
       'employees':{x:{'name':x,'telegram_user_id':i+1} for i,x in enumerate(MEMBERS)},
       'admins':['test_operator'],'business_account_user_id':99,'team_intake':{'recipients':MEMBERS[:],'started_at':100},
       'allowed_live_recipients':MEMBERS[:],'team_roster_verified':True,'recipients_verified':True,
       'live_send':True,'review_mode':True,'sheets_write':False,'daily_summary':False,
       'allow_onboarding_replies':False,'allow_registered_replies':False,
       'allowed_group_ids':[],'group_destinations':{},'meeting_recipient':None,
       'intelligence':{'enabled':True,'sources':[]}}
    e=Engine(s,c,None,lambda:NOW)
    e.intelligence.sources.refresh=lambda *a,**kw:{}
    for name in ('A','B'):
        e.intelligence.index.upsert(dict(source_id='source-'+name,document_id='doc',title='Source '+name,
          url='https://example.invalid/'+name,text='ALPHA '+PRIVATE+name,
          readers=[reader_key('GOLDWAY','test_operator')],observed_at=NOW-60,permission_expires_at=NOW+600,confirmed=True))
    s.execute('INSERT INTO chats(business,recipient,bot_chat,business_chat,connection,last_inbound) VALUES(?,?,?,?,?,?)',
              ('GOLDWAY','test_operator','1','500','conn',NOW))
    class API:
        def __init__(self):self.calls=[]
        def call(self,method,**params):
            self.calls.append(method)
            if method=='getBusinessConnection':return {'is_enabled':True,'rights':{'can_reply':True},'user':{'id':99}}
            assert method=='sendMessage'
            return {'message_id':len(self.calls)}
    api=API();d=Dispatcher(s,c,api,lambda:NOW)
    import builtins,socket,httpx
    def forbidden(*a,**kw):pytest.fail('external source/model/file/network access')
    monkeypatch.setattr(builtins,'open',forbidden);monkeypatch.setattr(socket,'socket',forbidden);monkeypatch.setattr(httpx,'Client',forbidden)
    yield SimpleNamespace(e=e,s=s,c=c,d=d,api=api)
    s.db.close()


def answer(case,event='answer',channel='bot',name='A',mode='grounded',send=True):
    class Synthetic:
        def propose(self,ctx):
            refs=[v['id'] for v in ctx['knowledge']['evidence'] if v['title']=='Source '+name] if mode=='grounded' else []
            assert refs or mode!='grounded'
            return dict(actions=[],intent='conversation',summary='Synthetic',reply_text='ALPHA response '+name,
              dialogue_state=dict(topic='ALPHA',entities=['ALPHA'],pending_offer=None,task_id=None),evidence_ids=refs,knowledge_mode=mode)
    case.e.model=Synthetic()
    source='telegram:1:1' if channel=='bot' else 'business:conn:500:1'
    assert case.e.reply(event,'test_operator','Что известно об ALPHA?',source_id=source,channel=channel)
    case.e.process(event)
    if send:case.d.flush()
    return case.s.rows('SELECT * FROM answer_citation_turns ORDER BY id')[-1]


def follow(case,event='cite',channel='bot',source=None):
    class Never:
        def propose(self,*a,**kw):pytest.fail('citation explanation called model')
    case.e.model=Never()
    case.e.intelligence.sources.refresh=lambda **kw:pytest.fail('citation explanation refreshed sources')
    case.e._context=lambda *a,**kw:pytest.fail('citation explanation built model context')
    source=source or ('telegram:1:2' if channel=='bot' else 'business:conn:500:2')
    case.e.reply(event,'test_operator','Откуда ты это знаешь?',source_id=source,channel=channel)
    return case.s.execute('SELECT * FROM outbox WHERE dedupe=?',('answer-citations:'+event,)).fetchone()


def test_exact_used_refs_distinct_from_protection_and_actual_dispatch_delivery(case):
    row=answer(case)
    assert row['phase']=='recorded'
    used=json.loads(row['citations']);protection=json.loads(row['protection_refs'])
    assert len(used)==1 and len(protection)==2
    assert PRIVATE not in json.dumps(row)
    result=follow(case)
    assert 'Source A — https://example.invalid/A' in result['text'] and 'Source B' not in result['text']
    assert 'наблюдалось:' in result['text'] and PRIVATE not in result['text']
    acl=case.s.execute('SELECT evidence_ids FROM intelligence_outbox_acl WHERE outbox_id=?',(result['id'],)).fetchone()[0]
    assert set(json.loads(acl))==set(protection)
    assert delivery_permitted(case.s,'GOLDWAY','test_operator',result['id'],NOW,case.c)
    assert not case.s.rows('SELECT id FROM usage') and not case.s.rows('SELECT id FROM tasks')


@pytest.mark.parametrize('state',['pending','retry','blocked','unknown','failed','sending'])
def test_unsent_latest_answer_never_falls_back(case,state):
    answer(case,event='old')
    answer(case,event='new',send=False)
    case.s.execute('UPDATE outbox SET state=? WHERE dedupe=?',(state,'clarify:new'))
    result=follow(case)
    assert 'сейчас недоступны' in result['text'] and 'Source A —' not in result['text']


def test_new_unanswered_turn_is_barrier(case):
    answer(case)
    case.e.reply('pending','test_operator','Новый вопрос',source_id='telegram:1:3',channel='bot')
    assert 'сейчас недоступны' in follow(case)['text']


def test_latest_unsupported_command_does_not_reveal_earlier_answer(case):
    answer(case)
    case.e.reply('trace','test_operator','/trace',source_id='telegram:1:3',channel='bot');case.d.flush()
    assert 'сейчас недоступны' in follow(case)['text']


def test_repeat_citation_explanations_keep_same_substantive_anchor_and_dedupe(case):
    answer(case);first=follow(case);case.d.flush()
    second=follow(case,'again')
    assert first['text']==second['text'] and 'Source A —' in second['text']
    assert len(case.s.rows('SELECT id FROM answer_citation_turns'))==1
    follow(case,'again')
    assert len(case.s.rows("SELECT id FROM outbox WHERE dedupe='answer-citations:again'"))==1


def test_latest_answer_with_no_citations_never_uses_earlier_refs(case):
    answer(case,event='old');answer(case,event='new',mode='not_needed')
    result=follow(case)
    assert 'нет сохранённых корпоративных ссылок' in result['text'] and 'Source A' not in result['text']


@pytest.mark.parametrize('mutation',['revoke','expire','content','title','url','updated','unconfirmed','outbox_text'])
def test_current_permission_and_exact_content_metadata_version(case,mutation):
    answer(case)
    if mutation=='revoke':case.e.intelligence.index.revoke_source('source-A')
    elif mutation=='expire':case.s.execute('UPDATE knowledge_documents SET permission_expires_at=?',(NOW,))
    elif mutation=='content':case.s.execute("UPDATE knowledge_chunks SET text='changed'")
    elif mutation=='outbox_text':case.s.execute("UPDATE outbox SET text='changed' WHERE dedupe='clarify:answer'")
    else:
        column={'title':'title','url':'url','updated':'updated_at','unconfirmed':'confirmed'}[mutation]
        case.s.execute('UPDATE knowledge_documents SET '+column+'=?',(0 if mutation=='unconfirmed' else 'changed',))
    assert 'сейчас недоступны' in follow(case)['text']


@pytest.mark.parametrize('channel,source',[('business','business:conn:500:2'),('bot','telegram:999:2'),('business','business:other:500:2')])
def test_other_private_scope_cannot_see_bot_answer(case,channel,source):
    answer(case)
    assert 'сейчас недоступны' in follow(case,channel=channel,source=source)['text']


def test_real_business_delivery_scope_is_required(case):
    answer(case,channel='business')
    assert 'Source A —' in follow(case,channel='business')['text']


def test_legacy_dialogue_without_new_receipt_is_not_backfilled(case):
    scope='dialogue-scope:v1:'+json.dumps(['GOLDWAY','test_operator','bot','1',None,None],separators=(',',':'))
    case.e.intelligence.memory.record(scope,'legacy','Question','Legacy '+PRIVATE,at=NOW)
    assert not case.s.rows('SELECT id FROM answer_citation_turns')
    assert 'сейчас недоступны' in follow(case)['text']


def test_capture_failure_keeps_successful_answer_with_unavailable_marker(case,monkeypatch):
    def fail(*a,**kw):raise ValueError(PRIVATE)
    monkeypatch.setattr(citations,'_cited',fail)
    row=answer(case)
    assert row['phase']=='unavailable'
    assert case.s.execute("SELECT state FROM events WHERE id='answer'").fetchone()[0]=='done'
    assert case.s.execute("SELECT state FROM outbox WHERE dedupe='clarify:answer'").fetchone()[0]=='sent'
    assert 'сейчас недоступны' in follow(case)['text']


def test_final_reread_rejects_mutation_before_response(case,monkeypatch):
    answer(case);real=citations._resolve;count=[0]
    def race(*a,**kw):
        count[0]+=1
        if count[0]==2:case.e.intelligence.index.revoke_source('source-A')
        return real(*a,**kw)
    monkeypatch.setattr(citations,'_resolve',race)
    assert 'сейчас недоступны' in follow(case)['text']


def test_receipt_bounds_and_indexed_latest_do_not_scan_answer_text_archive(case):
    answer(case)
    for i in range(200):
        case.s.execute("INSERT INTO answer_citation_turns(business,scope_key,event_key,at) VALUES('OTHER',?,?,?)",(str(i),str(i),NOW))
    statements=[];case.s.db.set_trace_callback(statements.append)
    result=follow(case);case.s.db.set_trace_callback(None)
    assert 'Source A —' in result['text'] and len(result['text'].encode('utf-16-le'))//2<=3500
    reads=[x for x in statements if x.startswith('SELECT') and 'answer_citation_turns' in x]
    assert reads and all('LIMIT 1' in x and 'scope_key=' in x and "business='GOLDWAY'" in x for x in reads)


def test_transaction_rollback_never_leaves_false_successful_receipt(case,monkeypatch):
    def fail(*a,**kw):raise ValueError('synthetic_memory_failure')
    monkeypatch.setattr(case.e.intelligence.memory,'record',fail)
    with pytest.raises(ValueError,match='synthetic_memory_failure'):answer(case,send=False)
    row=case.s.rows('SELECT phase FROM answer_citation_turns')[0]
    assert row['phase']=='pending'
    assert not case.s.rows("SELECT id FROM outbox WHERE dedupe='clarify:answer'")
    assert 'сейчас недоступны' in follow(case)['text']


def work_answer(case,completed=False):
    text='Concrete synthetic registered report output.'
    case.s.execute('INSERT INTO sources VALUES(?,?,?,?,0)',('GOLDWAY','work-source',text,'synthetic'))
    case.s.execute('''INSERT INTO tasks(business,id,title,owner,deadline,next_step,criterion,status,source_id,evidence,created)
      VALUES(?,?,?,?,?,?,?,?,?,?,?)''',('GOLDWAY','work','Synthetic work','frost','2026-10-20','step','criterion',
                                       'completed' if completed else 'blocked','work-source',text,NOW-1))
    case.s.execute('INSERT INTO task_sources VALUES(?,?,?)',('GOLDWAY','work','work-source'))
    class Synthetic:
        def propose(self,ctx):
            refs=[x['id'] for x in ctx['work_context']['evidence']]
            assert len(refs)==1
            return dict(actions=[],intent='conversation',summary='Synthetic',reply_text='По записи задачи передан результат.' if completed else 'По записи задачи есть препятствие.',
              dialogue_state=dict(topic='Работа',entities=[],pending_offer=None,task_id=None),evidence_ids=refs,knowledge_mode='grounded')
    case.e.model=Synthetic()
    case.e.reply('work-answer','test_operator','Какой результат задачи №1?' if completed else 'Какие у нас проблемы?',source_id='telegram:1:1',channel='bot')
    case.e.process('work-answer');case.d.flush()
    return case.s.rows('SELECT * FROM answer_citation_turns')[-1]


@pytest.mark.parametrize('completed',[False,True])
def test_work_citation_uses_task_revision_and_registered_result_snapshot(case,completed):
    row=work_answer(case,completed)
    assert row['phase']=='recorded'
    assert PRIVATE not in json.dumps(row)
    assert 'Реестр задач Mike: №1' in follow(case)['text']
    case.s.execute('UPDATE tasks SET revision=revision+1')
    assert 'сейчас недоступны' in follow(case,'again')['text']


def test_registered_result_source_change_invalidates_exact_receipt(case):
    work_answer(case,True)
    case.s.execute("UPDATE sources SET body=body||'changed' WHERE id='work-source'")
    assert 'сейчас недоступны' in follow(case)['text']


def test_changed_content_during_model_does_not_get_relabelled_as_old_provenance(case):
    class Mutating:
        def propose(self,ctx):
            item=next(v for v in ctx['knowledge']['evidence'] if v['title']=='Source A')
            case.s.execute("UPDATE knowledge_chunks SET text='AFTERMODEL' WHERE id=?",(item['id'],))
            return dict(actions=[],intent='conversation',summary='Synthetic',reply_text='ALPHA response',
              dialogue_state=dict(topic='',entities=[],pending_offer=None,task_id=None),evidence_ids=[item['id']],knowledge_mode='grounded')
    case.e.model=Mutating()
    case.e.reply('race','test_operator','Что известно об ALPHA?',source_id='telegram:1:1',channel='bot')
    case.e.process('race');case.d.flush()
    assert case.s.rows('SELECT phase FROM answer_citation_turns')[0]['phase']=='unavailable'
    assert 'сейчас недоступны' in follow(case)['text']


def test_business_drift_denied_before_receipt_read_or_queue(case):
    answer(case);case.c['business']='OTHER'
    statements=[];case.s.db.set_trace_callback(statements.append)
    row=follow(case);case.s.db.set_trace_callback(None)
    assert row is None
    assert not any(x.startswith('SELECT') and ('answer_citation_turns' in x or 'knowledge_' in x) for x in statements)
    case.e._context=Engine._context.__get__(case.e,Engine)
    case.e.reply('new-other','test_operator','привет',source_id='telegram:1:3',channel='bot')
    assert not case.s.rows("SELECT id FROM answer_citation_turns WHERE business='OTHER'")


def test_registered_result_body_budget_checked_before_helper(case,monkeypatch):
    work_answer(case,True)
    import goldway.task_result_context as result_module
    monkeypatch.setattr(citations,'MAX_BYTES',1000)
    def forbidden(*a,**kw):pytest.fail('over-budget source probe reached helper')
    monkeypatch.setattr(result_module,'registered_result',forbidden)
    assert 'сейчас недоступны' in follow(case)['text']


def test_both_resolves_share_one_cumulative_reader_budget(case,monkeypatch):
    answer(case);real=citations._resolve;readers=[]
    def inspect(*a,**kw):
        readers.append(kw['reader'])
        return real(*a,**kw)
    monkeypatch.setattr(citations,'_resolve',inspect)
    assert 'Source A —' in follow(case)['text']
    assert len(readers)==2 and readers[0] is readers[1]


@pytest.mark.parametrize('column,value',[
    ('permission_expires_at','BAD-TIMESTAMP'),('permission_expires_at',PRIVATE*10000),
    ('observed_at','BAD-TIMESTAMP'),('observed_at',PRIVATE*10000),
    ('confirmed','BAD-BOOLEAN'),('confirmed',PRIVATE*10000),
])
def test_malformed_numeric_cells_denied_before_transfer(case,column,value):
    answer(case)
    case.s.execute('UPDATE knowledge_documents SET '+column+'=?',(value,))
    assert 'сейчас недоступны' in follow(case)['text']


def update(case,id,text=None,document=None):
    from goldway.telegram import Updates
    m={'from':{'id':1},'chat':{'id':1,'type':'private'},'message_id':id,'date':NOW}
    if text is not None:m['text']=text
    if document is not None:m['document']=document
    value={'update_id':id,'message':m}
    # Actual deduped adapter path; API only synthetic delivery/download fixture.
    Updates(case.e,case.api,bot_id='999').handle(value)
    return value


@pytest.mark.parametrize('text',['/start','/summary','сводка','/meeting Synthetic note for review'])
def test_actual_telegram_early_responses_break_old_anchor_and_replay_is_noop(case,text):
    answer(case)
    original=update(case,101,text)
    latest=case.s.rows('SELECT id,event_key FROM answer_citation_turns ORDER BY id')[-1]
    assert len(case.s.rows('SELECT id FROM answer_citation_turns'))==2
    case.d.flush()
    assert 'сейчас недоступны' in follow(case)['text']
    # Later new content can become the target; replay of the OLD early command must not mask it.
    case.e._context=Engine._context.__get__(case.e,Engine)
    case.e.intelligence.sources.refresh=lambda *a,**kw:{}
    answer(case,event='after-early',name='B')
    from goldway.telegram import Updates
    Updates(case.e,case.api,bot_id='999').handle(original)
    assert 'Source B —' in follow(case,'again')['text']


def test_actual_telegram_attachment_limit_response_is_barrier(case):
    answer(case)
    case.api.document=lambda file_id:'A'*32001
    update(case,102,document={'file_id':'synthetic','file_name':'meeting.txt','file_size':32001})
    assert case.s.rows("SELECT id FROM outbox WHERE dedupe LIKE 'attachment-limit:%'")
    assert 'сейчас недоступны' in follow(case)['text']


@pytest.mark.parametrize('column',['public_number','revision'])
def test_task_numeric_requery_is_sql_bounded(case,monkeypatch,column):
    work_answer(case)
    real=citations._current;called=[False]
    def changing(*a,**kw):
        result=real(*a,**kw)
        if not called[0]:
            called[0]=True;case.s.execute('UPDATE tasks SET '+column+'=?',(PRIVATE*10000,))
        return result
    monkeypatch.setattr(citations,'_current',changing)
    assert 'сейчас недоступны' in follow(case)['text']


def test_outbox_thread_type_is_sql_bounded_before_receipt_use(case):
    answer(case)
    case.s.execute('UPDATE outbox SET destination_thread=?',(PRIVATE*10000,))
    assert 'сейчас недоступны' in follow(case)['text']


def test_actual_result_source_lengths_enforce_cumulative_probe_budget(case,monkeypatch):
    work_answer(case,True)
    case.s.execute('INSERT INTO sources VALUES(?,?,?,?,0)',('GOLDWAY','large-unmatched','A'*64000,'synthetic'))
    case.s.execute('INSERT INTO task_sources VALUES(?,?,?)',('GOLDWAY','work','large-unmatched'))
    import goldway.task_result_context as result_module
    monkeypatch.setattr(citations,'MAX_BYTES',60000)
    def forbidden(*a,**kw):pytest.fail('size preflight did not stop source body transfer')
    monkeypatch.setattr(result_module,'registered_result',forbidden)
    assert 'сейчас недоступны' in follow(case)['text']


@pytest.mark.parametrize('field',['source_id','deleted'])
def test_result_probe_metadata_guard_prevents_raw_giant_cell_transfer(case,monkeypatch,field):
    work_answer(case,True)
    if field=='source_id':
        # Normal body: only metadata is oversized, including an unmatched link.
        giant=PRIVATE*10000
        case.s.execute('INSERT INTO sources VALUES(?,?,?,?,0)',('GOLDWAY',giant,'small body','synthetic'))
        case.s.execute('INSERT INTO task_sources VALUES(?,?,?)',('GOLDWAY','work',giant))
    else:
        case.s.execute("UPDATE sources SET deleted=? WHERE id='work-source'",(PRIVATE*10000,))
    import goldway.task_result_context as result_module
    def forbidden(*a,**kw):pytest.fail('invalid source metadata reached raw result helper')
    monkeypatch.setattr(result_module,'registered_result',forbidden)
    assert 'сейчас недоступны' in follow(case)['text']


def test_result_probe_accounts_full_multibyte_source_id(case,monkeypatch):
    work_answer(case,True)
    import goldway.task_result_context as result_module
    real=result_module.registered_result
    readers=[];charged=[]
    resolve=citations._resolve
    def observed(*args,**kwargs):
        readers.append(kwargs['reader'])
        return resolve(*args,**kwargs)
    def helper(*args,**kwargs):
        charged.append(readers[-1].bytes)
        return real(*args,**kwargs)
    monkeypatch.setattr(citations,'_resolve',observed)
    monkeypatch.setattr(result_module,'registered_result',helper)
    assert 'Реестр задач Mike: №1' in follow(case)['text']
    baseline=charged[0];charged.clear();readers.clear()
    source='😀'*256
    case.s.execute('INSERT INTO sources VALUES(?,?,?,?,0)',('GOLDWAY',source,'small body','synthetic'))
    case.s.execute('INSERT INTO task_sources VALUES(?,?,?)',('GOLDWAY','work',source))
    assert 'Реестр задач Mike: №1' in follow(case,'again')['text']
    assert readers[0] is readers[1]
    assert charged[0]-baseline>=len(source.encode())+len('small body'.encode())
