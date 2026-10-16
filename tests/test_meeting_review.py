"""Caller-confirmed synthetic structures; no NLP/model/dispatch or real sources."""
from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime
import pytest
from goldway.meeting_review import (Source,Span,BoundValue,Agreement,Question,Claim,
    Meeting,TaskRecord,ResultRecord,ReviewError,review_meeting,compare_week)

DAY=date(2026,10,12)
END=date(2026,10,19)


def span(source,first,last=None):
    last=first if last is None else last
    return Span(source.id,first,last,'\n'.join(source.lines[first-1:last]))


@pytest.fixture
def case():
    source=Source('meeting-source','SYNTHETIC',DAY,(
        'Решили: Alice подготовит список.','Срок: 2026-10-20.',
        'Обсуждали вариант: Bob проверит каталог.','Открытый вопрос: какой формат?',
        'Факт: ответственный Alice.','Ответ: формат CSV.'))
    agreement=Agreement('A','list.decision','Подготовить список',(span(source,1,2),),True,
        BoundValue('list.owner','Alice',span(source,1),True),
        BoundValue('list.deadline','2026-10-20',span(source,2),True))
    tentative=Agreement('B','catalog.decision','Проверить каталог',(span(source,3),),False)
    question=Question('Q','Какой формат?',(span(source,4),))
    meeting=Meeting('meeting','SYNTHETIC',DAY,(source,),(agreement,tentative),(question,))
    return meeting,source


def weekly(case,status='confirmed',outcome='completed'):
    meeting,_=case
    task_source=Source('task-source','SYNTHETIC',date(2026,10,13),('Задача T1 связана с A.',))
    result_source=Source('result-source','SYNTHETIC',date(2026,10,15),('Результат T1: список создан.',))
    task=TaskRecord('T1','SYNTHETIC','A',status,(span(task_source,1),))
    result=ResultRecord('R1','SYNTHETIC','T1',result_source.on,outcome,(span(result_source,1),))
    params=dict(start=DAY,end=END,tasks=(task,),results=(result,),sources=(task_source,result_source))
    return meeting,params


def test_only_confirmed_decision_becomes_proposed_preview_with_exact_provenance(case):
    meeting,source=case;review=review_meeting(meeting)
    assert len(review.proposed_tasks)==1
    task=review.proposed_tasks[0]
    assert (task.agreement_id,task.title,task.owner,task.deadline,task.status)==('A','Подготовить список','Alice',date(2026,10,20),'proposed')
    assert task.spans==meeting.agreements[0].spans and task.missing_fields==task.conflicted_fields==()
    assert review.tentative_agreement_ids==('B',) and review.unresolved_questions==(meeting.questions[0],)
    assert not review.contradictions and review_meeting(meeting)==review
    with pytest.raises(FrozenInstanceError):meeting.agreements[0].value='changed'


def test_unconfirmed_fields_remain_unknown_without_defaults(case):
    meeting,_=case;a=meeting.agreements[0]
    a=replace(a,owner=replace(a.owner,confirmed=False),deadline=None)
    review=review_meeting(replace(meeting,agreements=(a,)))
    task=review.proposed_tasks[0]
    assert task.owner is None and task.deadline is None and task.missing_fields==('owner','deadline')


def test_contradictory_owner_values_preserve_all_values_and_do_not_select(case):
    meeting,source=case
    conflict_source=Source('other','SYNTHETIC',DAY,('Факт: ответственный Bob.',))
    claim=Claim('C','list.owner','Bob',(span(conflict_source,1),),True)
    review=review_meeting(replace(meeting,sources=meeting.sources+(conflict_source,),claims=(claim,)))
    conflict=review.contradictions[0]
    assert conflict.fact_key=='list.owner' and conflict.values==('Alice','Bob') and len(conflict.spans)==2
    assert review.proposed_tasks[0].owner is None and review.proposed_tasks[0].conflicted_fields==('owner',)
    assert review.proposed_tasks[0].deadline==date(2026,10,20)


def test_conflicting_decision_produces_no_selected_task_preview(case):
    meeting,source=case
    claim=Claim('C','list.decision','Отменить список',(span(source,3),),True)
    meeting=replace(meeting,claims=(claim,))
    review=review_meeting(meeting)
    assert not review.proposed_tasks and review.contradictions[0].values==('Отменить список','Подготовить список')
    meeting,params=weekly((meeting,source))
    assert compare_week(meeting,**params)[0].state=='contradiction'


def test_unconfirmed_or_same_value_claim_is_not_an_invented_conflict(case):
    meeting,source=case
    claims=(Claim('C1','list.owner','Alice',(span(source,5),),True),Claim('C2','list.owner','Bob',(span(source,3),),False))
    assert not review_meeting(replace(meeting,claims=claims)).contradictions


def test_question_resolution_requires_confirmed_cited_input(case):
    meeting,source=case
    q=replace(meeting.questions[0],resolution=BoundValue('format','CSV',span(source,6),False))
    assert review_meeting(replace(meeting,questions=(q,))).unresolved_questions==(q,)
    q=replace(q,resolution=replace(q.resolution,confirmed=True))
    assert not review_meeting(replace(meeting,questions=(q,))).unresolved_questions


@pytest.mark.parametrize('change',['quote','range','unknown-source','wrong-business','wrong-date'])
def test_source_span_bindings_fail_closed(case,change):
    meeting,source=case;a=meeting.agreements[0]
    if change=='quote':a=replace(a,spans=(replace(a.spans[0],quote='invented'),),owner=None,deadline=None)
    if change=='range':a=replace(a,spans=(Span(source.id,1,20,'invented'),),owner=None,deadline=None)
    if change=='unknown-source':a=replace(a,spans=(replace(a.spans[0],source_id='unknown'),),owner=None,deadline=None)
    if change=='wrong-business':source=replace(source,business='OTHER')
    if change=='wrong-date':source=replace(source,on=date(2026,10,13))
    with pytest.raises(ReviewError):replace(meeting,sources=(source,),agreements=(a,))


def test_owner_or_deadline_cannot_borrow_unrelated_source_lines(case):
    meeting,source=case;a=meeting.agreements[0]
    with pytest.raises(ReviewError,match='^field_outside_agreement_span$'):
        replace(a,owner=replace(a.owner,span=span(source,3)))


@pytest.mark.parametrize('deadline',['завтра','2026-2-1','2026-02-30'])
def test_confirmed_deadline_requires_explicit_valid_iso_date(case,deadline):
    a=case[0].agreements[0]
    with pytest.raises(ReviewError):replace(a,deadline=replace(a.deadline,value=deadline))


def test_week_completed_requires_explicit_result_and_preserves_recorded_status(case):
    meeting,params=weekly(case,status='completed')
    result=compare_week(meeting,**params)[0]
    assert result.state=='completed' and result.tasks[0].result_ids==('R1',)
    params['results']=()
    missing=compare_week(meeting,**params)[0]
    assert missing.state=='missing_results' and missing.tasks[0].recorded_status=='completed'
    assert missing.tasks[0].result_state=='missing' and missing.tasks[0].result_ids==()
    params['tasks']=()
    assert compare_week(meeting,**params)[0].state=='unlinked'


@pytest.mark.parametrize('outcome',['blocked','in_progress'])
def test_noncompleted_explicit_result_stays_noncompleted(case,outcome):
    meeting,params=weekly(case,outcome=outcome)
    assert compare_week(meeting,**params)[0].state==outcome


def test_missing_one_subtask_prevents_whole_agreement_completed(case):
    meeting,params=weekly(case)
    params['tasks']+=(replace(params['tasks'][0],id='T2'),)
    result=compare_week(meeting,**params)[0]
    assert result.state=='missing_results' and len(result.tasks)==2


def test_latest_explicit_result_date_wins_but_same_day_conflicts_are_not_selected(case):
    meeting,params=weekly(case)
    blocked=replace(params['results'][0],id='R2',outcome='blocked')
    params['results']+=(blocked,)
    assert compare_week(meeting,**params)[0].state=='conflict'
    older_source=Source('older','SYNTHETIC',date(2026,10,14),('Список ещё готовится.',))
    params['sources']+=(older_source,)
    params['results']=(replace(blocked,on=older_source.on,spans=(span(older_source,1),)),params['results'][0])
    assert compare_week(meeting,**params)[0].state=='completed'


@pytest.mark.parametrize('on,included',[(DAY,True),(date(2026,10,18),True),(END,False)])
def test_result_window_start_inclusive_end_exclusive(case,on,included):
    meeting,params=weekly(case)
    result_source=replace(params['sources'][1],on=on)
    params['sources']=(params['sources'][0],result_source)
    params['results']=(replace(params['results'][0],on=on),)
    assert (compare_week(meeting,**params)[0].state=='completed') is included


@pytest.mark.parametrize('change',['wrong-task-business','wrong-agreement','wrong-result-business','wrong-task-link',
                                  'duplicate-task','duplicate-result','bad-result-span','result-date','future-task-source'])
def test_week_exact_links_business_and_provenance_required(case,change):
    meeting,params=weekly(case)
    if change=='wrong-task-business':params['tasks']=(replace(params['tasks'][0],business='OTHER'),)
    if change=='wrong-agreement':params['tasks']=(replace(params['tasks'][0],agreement_id='B'),)
    if change=='wrong-result-business':params['results']=(replace(params['results'][0],business='OTHER'),)
    if change=='wrong-task-link':params['results']=(replace(params['results'][0],task_id='unknown'),)
    if change=='duplicate-task':params['tasks']*=2
    if change=='duplicate-result':params['results']*=2
    if change=='bad-result-span':params['results']=(replace(params['results'][0],spans=(replace(params['results'][0].spans[0],quote='invented'),)),)
    if change=='result-date':params['results']=(replace(params['results'][0],on=DAY),)
    if change=='future-task-source':params['sources']=(replace(params['sources'][0],on=END),params['sources'][1])
    with pytest.raises(ReviewError):compare_week(meeting,**params)


@pytest.mark.parametrize('first,last',[(0,1),(True,1),(2,1),(1,21),(1,1001)])
def test_span_bounds(first,last):
    with pytest.raises(ReviewError):Span('source',first,last,'quote')


def test_record_types_limits_duplicates_and_dates_fail_closed(case):
    meeting,source=case
    with pytest.raises(ReviewError):replace(meeting,agreements=meeting.agreements*2)
    with pytest.raises(ReviewError):replace(meeting,sources=meeting.sources*2)
    with pytest.raises(ReviewError):replace(source,lines=['mutable'])
    with pytest.raises(ReviewError):replace(source,lines=('x'*2001,))
    with pytest.raises(ReviewError):replace(source,lines=('🚀'*1000,)*17)
    with pytest.raises(ReviewError):replace(meeting,on=datetime(2026,10,12))
    with pytest.raises(ReviewError):replace(meeting,agreements=tuple(replace(meeting.agreements[0],id=str(i)) for i in range(101)))
    m,params=weekly(case);params['end']=date(2026,10,20)
    with pytest.raises(ReviewError):compare_week(m,**params)


def test_no_io_clock_network_sqlite_or_model_effects(case,monkeypatch):
    import builtins,socket,sqlite3,time
    def forbidden(*a,**k):pytest.fail('unexpected external effect')
    for obj,name in ((builtins,'open'),(socket,'socket'),(sqlite3,'connect'),(time,'time')):monkeypatch.setattr(obj,name,forbidden)
    assert review_meeting(case[0]).proposed_tasks[0].status=='proposed'
    meeting,params=weekly(case);assert compare_week(meeting,**params)[0].state=='completed'


@pytest.mark.parametrize('other_value,conflicted',[('CSV',False),('JSON',True)])
def test_confirmed_resolution_claim_conflicts_keep_question_open(case,other_value,conflicted):
    meeting,source=case
    other=Source('resolution-claim','SYNTHETIC',DAY,(f'Подтверждённый формат: {other_value}.',))
    question=replace(meeting.questions[0],resolution=BoundValue('format','CSV',span(source,6),True))
    claim=Claim('format-claim','format',other_value,(span(other,1),),True)
    report=review_meeting(replace(meeting,sources=meeting.sources+(other,),questions=(question,),claims=(claim,)))
    assert bool(report.unresolved_questions) is conflicted
    assert bool(report.contradictions) is conflicted
    if conflicted:
        assert report.unresolved_questions==(question,)
        conflict=next(item for item in report.contradictions if item.fact_key=='format')
        assert conflict.values==('CSV','JSON')
        assert set(conflict.spans)=={question.resolution.span,claim.spans[0]}


def test_conflicting_confirmed_question_resolutions_keep_both_questions_open(case):
    meeting,source=case
    other=Source('resolution-other','SYNTHETIC',DAY,('Ответ: формат JSON.',))
    first=replace(meeting.questions[0],resolution=BoundValue('format','CSV',span(source,6),True))
    second=replace(first,id='Q2',resolution=BoundValue('format','JSON',span(other,1),True))
    report=review_meeting(replace(meeting,sources=meeting.sources+(other,),questions=(first,second)))
    assert report.unresolved_questions==(first,second)
    assert report.contradictions[0].values==('CSV','JSON')
    assert set(report.contradictions[0].spans)=={first.resolution.span,second.resolution.span}
