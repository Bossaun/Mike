import json
from test_group_work import r,inbound
from goldway.model import response_schema
from goldway.work_evidence import allowed_evidence_ids,failure_code


def test_context_citation_answers_without_granting_task_mutation(r):
    r.d.context=lambda:{'messages':[{'id':'77','body':'Карточка проверяется.'}],'seed_context':'test'}
    r.e.model.result={'reply':'По переписке карточка требует проверки.','evidence_ids':['77'],'updates':[{'task_id':'C01','message_id':77,'kind':'accept','quote':'Принимаю'}]}
    inbound(r,'Валли, как дела?');r.w.process(lambda:False)
    assert r.s.rows('SELECT state FROM group_work_events')[0]['state']=='done'
    assert r.s.rows('SELECT state FROM group_work_tasks')[0]['state']=='open'
    assert r.api.sent


def test_unprovided_citation_is_rejected_and_reason_saved(r):
    r.e.model.result={'reply':'Всё сделано','evidence_ids':['never-provided'],'updates':[]}
    inbound(r,'Валли, как дела?');r.w.process(lambda:False)
    assert r.s.rows('SELECT state FROM group_work_events')[0]['state']=='failed'
    detail=json.loads(r.s.rows("SELECT detail FROM audit WHERE kind='group_work_reply_failure'")[0]['detail'])
    assert detail=={'code':'invalid_evidence','scope':'group'}


def test_schema_is_per_context_not_shared():
    a=response_schema({'kind':'group_work','current_messages':[{'id':-45}],'messages':[{'id':'20'}]});b=response_schema({'kind':'group_work'})
    assert set(a['properties']['evidence_ids']['items']['enum'])=={'seed','-45','20'}
    assert b['properties']['evidence_ids']['items']['enum']==['seed']
    assert allowed_evidence_ids({'allowed_evidence_ids':['invented']})==['seed']


def test_error_redaction():
    assert failure_code(ValueError('private text or token'))=='processing_error'
