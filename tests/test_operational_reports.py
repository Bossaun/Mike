"""Pure explicit-period/source inputs; no runtime fixtures or business data."""
from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime
from decimal import Decimal, localcontext
from zoneinfo import ZoneInfo

import pytest

from goldway.operational_reports import (AgendaFact, AgendaPreview, Coverage,
    FormValue, MetricDefinition, MetricObservation, Period, TaskOutcome,
    agenda_preview, digest_preview, kpi_snapshot, monday_form_cutoff,
    monthly_snapshot, reconcile_forms)


D=Decimal
ZONE=ZoneInfo('Europe/Moscow')
DAY=Period(date(2026,10,16),date(2026,10,17))
MONTH=Period(date(2026,10,1),date(2026,11,1))
DEFINITION=MetricDefinition('touches','Касания','count',None,D('10'),'at_least')


def observation(value='12', **changes):
    values=dict(owner='synthetic-owner',metric_id='touches',period=DAY,
                value=None if value is None else D(value),unit='count',currency=None,
                observed_on=date(2026,10,19),source_id='synthetic:metric')
    values.update(changes)
    return MetricObservation(**values)


def coverage(complete=True,period=DAY,observed_on=date(2026,10,19)):
    return Coverage(period,complete,observed_on,'synthetic:coverage')


def kpis(observations=None,**changes):
    values=dict(definitions=(DEFINITION,),owners=('synthetic-owner',),
                observations=(observation(),) if observations is None else observations,
                coverage=coverage(),require_daily=True)
    values.update(changes)
    return kpi_snapshot(**values)


def forms(**changes):
    due=monday_form_cutoff(monday=date(2026,10,19),timezone_name='Europe/Moscow')
    values=dict(definitions=(DEFINITION,),owners=('synthetic-owner',),
                forms=(FormValue(observation('12'),datetime(2026,10,19,14,tzinfo=ZONE)),),
                account=(observation('10',source_id='synthetic:account'),),
                coverage=coverage(),due_at=due,as_of=due)
    values.update(changes)
    return reconcile_forms(**values)


def outcome(id='task1',**changes):
    values=dict(id=id,owner='synthetic-owner',created_on=date(2026,9,25),
                due_on=date(2026,10,10),completion_state='completed',
                completed_on=date(2026,10,11),observed_on=date(2026,11,5),source_id='synthetic:'+id)
    values.update(changes)
    return TaskOutcome(**values)


def month(records=(),**changes):
    values=dict(owners=('synthetic-owner',),records=records,
                coverage=coverage(period=MONTH,observed_on=date(2026,11,5)))
    values.update(changes)
    return monthly_snapshot(**values)


def test_daily_kpi_uses_explicit_target_unit_and_source():
    row,=kpis()
    assert row.value==D('12') and row.target==D('10') and row.target_state=='met'
    assert row.status=='known' and row.unit=='count' and row.currency is None
    assert row.sources==('synthetic:coverage','synthetic:metric')
    with pytest.raises(FrozenInstanceError):row.value=D('0')


def test_kpi_missing_is_not_zero_explicit_zero_can_miss_target():
    assert kpis(())[0].value is None and kpis(())[0].target_state=='unknown'
    assert kpis((observation(None),))[0].status=='unknown'
    row,=kpis((observation('0'),))
    assert row.value==0 and row.target_state=='missed'
    rows=kpis(owners=('synthetic-owner','second'))
    assert rows[1].owner=='second' and rows[1].value is None


@pytest.mark.parametrize('definition',[
    replace(DEFINITION,target=None),replace(DEFINITION,direction='informational',target=None),
    replace(DEFINITION,direction='at_most',target=D('15')),
])
def test_no_invented_target_or_direction(definition):
    row,=kpis(definitions=(definition,))
    assert row.target==definition.target
    assert row.target_state==('informational' if definition.direction=='informational' else 'unknown' if definition.target is None else 'met')


@pytest.mark.parametrize('which',['coverage','row'])
def test_incomplete_period_or_early_row_is_never_final_kpi(which):
    options={'coverage':coverage(False)} if which=='coverage' else {}
    rows=(observation(observed_on=DAY.start),) if which=='row' else (observation(),)
    row,=kpis(rows,**options)
    assert row.status=='provisional' and row.target_state=='unknown'
    assert row.value==D('12')


@pytest.mark.parametrize('changes',[
    {'unit':'boxes'}, {'owner':'outside'}, {'metric_id':'undefined'},
    {'period':Period(date(2026,10,15),date(2026,10,16))},
    {'observed_on':date(2026,10,20)},
])
def test_exact_kpi_scope_units_period_and_date(changes):
    with pytest.raises(ValueError):kpis((observation(**changes),))


def test_money_metric_currency_must_match_no_fx():
    definition=MetricDefinition('sales','Продажи','money','EUR',D('100'),'at_least')
    row=observation(metric_id='sales',unit='money',currency='USD')
    with pytest.raises(ValueError,match='currency conflict'):kpis((row,),definitions=(definition,))
    with pytest.raises(ValueError):MetricDefinition('x','X','money',None,None,'informational')
    with pytest.raises(ValueError):MetricDefinition('x','X','count','EUR',None,'informational')


def test_duplicate_metrics_definitions_or_owner_scope_rejected():
    for values in ({'observations':(observation(),observation())},
                   {'definitions':(DEFINITION,DEFINITION)},
                   {'owners':('synthetic-owner','synthetic-owner')}):
        with pytest.raises(ValueError):kpis(**values)


def test_monday_15_and_discrepancy_questions_are_only_projection():
    row,=forms()
    assert row.comparison=='discrepancy' and row.difference==D('2')
    assert row.submission_state=='on_time' and 'расхождение' in row.question
    assert row.sources==('synthetic:coverage','synthetic:metric','synthetic:account')
    cutoff=monday_form_cutoff(monday=date(2026,10,19),timezone_name='Europe/Moscow')
    assert cutoff.hour==15 and cutoff.weekday()==0 and cutoff.tzinfo.key=='Europe/Moscow'
    with pytest.raises(ValueError):monday_form_cutoff(monday=date(2026,10,20),timezone_name='Europe/Moscow')


def test_form_missing_before_and_at_cutoff_not_zero():
    due=datetime(2026,10,19,15,tzinfo=ZONE)
    row,=forms(forms=(),as_of=due.replace(hour=14))
    assert row.reported is None and row.difference is None and row.question is None
    assert row.submission_state=='pending'
    row,=forms(forms=())
    assert row.submission_state=='missing_after_cutoff' and row.reported is None
    assert 'нет подтверждённого бланка' in row.question


def test_form_unknown_account_unknown_value_and_unknown_submission_time():
    row,=forms(account=())
    assert row.comparison=='missing_account' and row.accounted is None and row.difference is None
    row,=forms(account=(observation(None),))
    assert row.comparison=='unknown' and row.difference is None
    row,=forms(forms=(FormValue(observation(),None),))
    assert row.submission_state=='unknown_time' and row.comparison=='unknown'


@pytest.mark.parametrize('which',['coverage','account','form','early_submission'])
def test_no_final_form_comparison_from_incomplete_period(which):
    options={}
    if which=='coverage':options['coverage']=coverage(False)
    if which=='account':options['account']=(observation('10',observed_on=DAY.start),)
    if which=='form':options['forms']=(FormValue(observation(observed_on=DAY.start),datetime(2026,10,19,14,tzinfo=ZONE)),)
    if which=='early_submission':options['forms']=(FormValue(observation(),datetime(2026,10,16,14,tzinfo=ZONE)),)
    row,=forms(**options)
    assert row.comparison=='provisional' and row.difference is None


def test_late_forms_future_submissions_and_ambiguous_matches():
    row,=forms(forms=(FormValue(observation(),datetime(2026,10,19,16,tzinfo=ZONE)),),
               as_of=datetime(2026,10,19,17,tzinfo=ZONE))
    assert row.submission_state=='late'
    with pytest.raises(ValueError,match='Future form'):
        forms(forms=(FormValue(observation(),datetime(2026,10,19,16,tzinfo=ZONE)),))
    one=FormValue(observation(),datetime(2026,10,19,14,tzinfo=ZONE))
    with pytest.raises(ValueError):forms(forms=(one,one))
    with pytest.raises(ValueError):forms(account=(observation(unit='boxes'),))


def test_form_decimal_difference_independent_of_callers_precision():
    with localcontext() as ctx:
        ctx.prec=2
        row,=forms(forms=(FormValue(observation('1.23456789'),datetime(2026,10,19,14,tzinfo=ZONE)),),
                   account=(observation('1.23456788'),))
    assert row.difference==D('0.00000001')


def test_monthly_cohorts_and_last_day_are_explicit_and_historical():
    records=(outcome(),
        outcome('lastday',due_on=date(2026,10,31),completion_state='not_completed',completed_on=None),
        outcome('later',due_on=date(2026,10,20),completed_on=date(2026,11,3)),
        outcome('old-due',due_on=date(2026,9,28),completed_on=date(2026,10,3)))
    row,=month(records)
    assert row.status=='final' and row.due_in_period==3 and row.completed_in_period==2
    assert row.incomplete_at_period_end==2 and row.overdue_at_period_end==2
    assert row.late_completions==1 and row.observed_tasks==4
    assert len(row.sources)==5


def test_full_explicit_month_coverage_zero_differs_from_no_coverage():
    row,=month()
    assert row.status=='final' and row.due_in_period==row.completed_in_period==0
    row,=month(coverage=coverage(False,MONTH,date(2026,10,20)))
    assert row.status=='provisional' and row.due_in_period is None and row.completed_in_period is None


@pytest.mark.parametrize('changes',[
    {'due_on':None}, {'completion_state':'unknown','completed_on':None},
    {'observed_on':date(2026,10,20)},
])
def test_missing_or_stale_month_evidence_not_final(changes):
    row,=month((outcome(**changes),))
    assert row.status=='provisional' and row.completed_in_period is None and row.overdue_at_period_end is None


def test_month_rejects_duplicates_other_period_future_and_unknown_owner():
    for changes in ({'records':(outcome(),outcome())},
                    {'records':(outcome(owner='other'),)},
                    {'records':(outcome(observed_on=date(2026,11,6)),)},
                    {'coverage':coverage(period=DAY)}):
        with pytest.raises(ValueError):month(**changes)


def fact(id='fact1',**changes):
    values=dict(id=id,text='Проверить материалы',kind='question',status='confirmed',
                priority=10,sources=('synthetic:source',))
    values.update(changes)
    return AgendaFact(**values)


def test_agenda_priority_and_six_line_digest_with_sources_and_unknown_label():
    facts=tuple(fact(str(i),priority=i,status='provisional' if i==0 else 'confirmed') for i in range(8))
    agenda=agenda_preview(facts=facts,as_of=datetime(2026,10,19,9,tzinfo=ZONE),max_items=7)
    digest=digest_preview(agenda=agenda,day=date(2026,10,19),timezone_name='Europe/Moscow')
    assert len(digest.lines)==6 and len('\n'.join(digest.lines).splitlines())==6
    assert 'неполные данные' in digest.lines[1] and 'synthetic:source' in digest.lines[1]
    assert digest.lines[-1].endswith('4') and digest.sources==('synthetic:source',)
    assert digest.scheduled_for.hour==9 and digest.due_today
    assert digest.requires_review and not digest.live_send_authorized


@pytest.mark.parametrize(('hour','target','due'),[(8,date(2026,10,19),False),(10,date(2026,10,19),True),(10,date(2026,10,18),False)])
def test_digest_supplied_clock_is_metadata_not_dispatch(hour,target,due):
    agenda=agenda_preview(facts=(),as_of=datetime(2026,10,19,hour,tzinfo=ZONE),max_items=6)
    digest=digest_preview(agenda=agenda,day=target,timezone_name='Europe/Moscow')
    assert digest.due_today is due and len(digest.lines)==2
    assert 'не подтверждение отсутствия проблем' in digest.lines[1]


@pytest.mark.parametrize('value',['x\ny','x\ry','x\u2028y','x\x85y','', 'x'*301])
def test_agenda_cannot_inject_extra_digest_lines(value):
    with pytest.raises(ValueError):fact(text=value)


@pytest.mark.parametrize('value',[True,1.0,1,D('NaN'),D('Infinity'),D('1E1000000000')])
def test_bad_metric_and_target_numbers_rejected(value):
    with pytest.raises(ValueError):replace(observation(),value=value)
    with pytest.raises(ValueError):replace(DEFINITION,target=value)


def test_period_cardinality_and_unknown_source_guards():
    with pytest.raises(ValueError):Period(date(2026,1,1),date(2028,1,1))
    with pytest.raises(ValueError):Coverage(DAY,True,DAY.start,'source')
    with pytest.raises(ValueError):fact(sources=())
    with pytest.raises(ValueError):agenda_preview(facts=(fact(),fact()),as_of=datetime(2026,10,19,9,tzinfo=ZONE),max_items=6)
    with pytest.raises(ValueError):kpis(tuple(observation() for _ in range(1001)))
    with pytest.raises(ValueError):AgendaPreview(datetime(2026,10,19),(),0)
    with pytest.raises(ValueError):monday_form_cutoff(monday=date(2026,10,19),timezone_name=None)


def test_reporting_never_reads_files_network_or_wallclock(monkeypatch):
    import builtins,socket,time
    def forbidden(*args,**kwargs):pytest.fail('external operation')
    monkeypatch.setattr(builtins,'open',forbidden)
    monkeypatch.setattr(socket,'socket',forbidden)
    monkeypatch.setattr(time,'time',forbidden)
    assert kpis()[0].status=='known'
    assert forms()[0].difference==2
    assert month()[0].completed_in_period==0
    agenda=agenda_preview(facts=(fact(),),as_of=datetime(2026,10,19,9,tzinfo=ZONE),max_items=6)
    assert len(digest_preview(agenda=agenda,day=date(2026,10,19),timezone_name='Europe/Moscow').lines)==2
