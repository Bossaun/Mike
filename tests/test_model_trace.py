"""Payload-free synthetic categories and explicitly supplied offline prices."""
from dataclasses import fields, replace
from datetime import date
from decimal import Decimal, localcontext

import pytest

from goldway.model_trace import (Budget, BudgetScope, DeliveryState, ExecutionSource,
    Intent, ModelProfile, Operation, Processing, Provider, RateCard, RequestKind,
    SafeReason, TokenUsage, TraceRecord, Validation, Workload, compare_profiles,
    trace_views)


D=Decimal
ASOF=date(2026,10,16)


def trace(**changes):
    values=dict(request_kind=RequestKind.TASK,intent=Intent.TASK_ACTION,
                execution_source=ExecutionSource.MODEL,proposed_ops=(Operation.CREATE_TASK,),
                validation=Validation.PASSED,applied_count=1,queued_count=2,
                delivery_states=(DeliveryState.SENT,DeliveryState.UNKNOWN),
                processing=Processing.DONE,usage=TokenUsage(100,20),reason=SafeReason.NONE)
    values.update(changes)
    return TraceRecord(**values)


def profile(**changes):
    values=dict(label='synthetic-A',provider=Provider.OPENAI,context_limit_tokens=10000,
                output_limit_tokens=1000,rates=RateCard(D('2'),D('4'),'USD',ASOF,'synthetic:rates'))
    values.update(changes)
    return ModelProfile(**values)


def estimate(profiles=None,**changes):
    values=dict(profiles=(profile(),) if profiles is None else profiles,
                workload=Workload(2000,500,10,BudgetScope.DAY),
                budget=Budget(D('1'),'USD',BudgetScope.DAY),comparison_as_of=ASOF)
    values.update(changes)
    return compare_profiles(**values)


def test_stages_distinct_unknown_delivery_never_called_success():
    view,=trace_views((trace(),))
    assert view.proposed_count==1 and view.applied_count==1 and view.queued_count==2
    assert dict(view.delivery_counts)[DeliveryState.SENT]==1
    assert dict(view.delivery_counts)[DeliveryState.UNKNOWN]==1
    assert view.processing is Processing.DONE
    text='\n'.join(view.lines)
    assert 'Предложено: 1' in text and 'Применено: 1' in text and 'очередь: 2' in text
    assert 'unknown=1' in text and 'success' not in text


def test_validation_rejection_and_error_notice_can_be_sent_without_applied_action():
    view,=trace_views((trace(validation=Validation.REJECTED,applied_count=0,queued_count=1,
                           delivery_states=(DeliveryState.SENT,),processing=Processing.FAILED,
                           reason=SafeReason.TASK_VALIDATION),))
    assert view.applied_count==0 and dict(view.delivery_counts)[DeliveryState.SENT]==1
    assert 'rejected' in view.lines[1] and 'task_validation' in view.lines[-1]


def test_unknown_tokens_and_unobserved_deliveries_not_zero():
    view,=trace_views((trace(applied_count=None,queued_count=3,
                           delivery_states=(DeliveryState.SENT,),usage=TokenUsage(None,None)),))
    assert view.applied_count is None and view.unobserved_delivery_count==2
    assert 'нет данных' in view.lines[2] and 'нет данных' in view.lines[-1]
    view,=trace_views((trace(queued_count=None,delivery_states=()),))
    assert view.unobserved_delivery_count is None


def test_deterministic_action_not_invented_as_model_proposal():
    row=trace(execution_source=ExecutionSource.DETERMINISTIC,proposed_ops=(),
              applied_count=1,validation=Validation.NOT_RUN)
    view,=trace_views((row,))
    assert view.proposed_count==0 and view.applied_count==1
    assert 'deterministic' in view.lines[0]


@pytest.mark.parametrize('field,value',[
    ('request_kind','SECRET_PROMPT'),('intent','task_action'),('execution_source','model'),
    ('validation','passed'),('processing','done'),('reason','ValueError: SECRET_TOKEN'),
    ('proposed_ops',('create_task',)),('delivery_states',('sent',)),
    ('usage',{'input_tokens':100}),
])
def test_raw_text_enums_and_dict_payloads_rejected(field,value):
    with pytest.raises(ValueError):trace(**{field:value})


def test_trace_schema_cannot_carry_prompts_ids_config_or_provider_responses():
    names={field.name for field in fields(TraceRecord)}
    assert not names&{'prompt','text','task_id','customer_id','config','exception','response','key'}
    with pytest.raises(TypeError):trace(prompt='synthetic-secret')
    with pytest.raises(ValueError):trace_views(({'prompt':'synthetic-secret'},))
    with pytest.raises(ValueError):trace_views([trace()])
    with pytest.raises(ValueError):trace_views(tuple(trace() for _ in range(1001)))


@pytest.mark.parametrize('changes',[
    {'applied_count':True},{'queued_count':1.0},{'applied_count':-1},
    {'applied_count':2},{'applied_count':1,'validation':Validation.REJECTED},
    {'queued_count':0},{'proposed_ops':tuple(Operation.CREATE_TASK for _ in range(21))},
    {'delivery_states':tuple(DeliveryState.PENDING for _ in range(1001))},
])
def test_trace_counter_consistency_and_bounds(changes):
    with pytest.raises(ValueError):trace(**changes)


@pytest.mark.parametrize('value',[True,1.0,-1,10_000_001])
def test_usage_counter_guard(value):
    with pytest.raises(ValueError):TokenUsage(value,0)


def test_offline_cost_and_context_use_input_plus_reserved_output():
    row,=estimate()
    assert row.context_required_tokens==2500 and row.context_fits and row.output_fits
    assert row.estimated_per_call==D('0.006') and row.estimated_all_calls==D('0.06')
    assert row.budget_state=='within_supplied_budget'
    assert row.price_source=='synthetic:rates' and row.price_as_of==ASOF and row.price_age_days==0


def test_context_and_output_limits_are_separate_from_affordable_price():
    row,=estimate((profile(context_limit_tokens=2200,output_limit_tokens=400),))
    assert row.context_required_tokens==2500 and not row.context_fits and not row.output_fits
    assert row.budget_state=='within_supplied_budget'  # affordability is not usability


@pytest.mark.parametrize('field', ['input_per_million','output_per_million','as_of','source'])
def test_unknown_rate_or_price_provenance_is_not_zero(field):
    rates=replace(profile().rates,**{field:None})
    row,=estimate((profile(rates=rates),))
    assert row.estimated_per_call is None and row.estimated_all_calls is None
    assert row.budget_state=='unknown' and row.missing


def test_explicit_zero_price_and_unknown_budget_are_distinct():
    rates=RateCard(D('0'),D('0'),'USD',ASOF,'synthetic:free')
    row,=estimate((profile(rates=rates),))
    assert row.estimated_all_calls==0 and row.budget_state=='within_supplied_budget'
    row,=estimate(budget=Budget(None,'USD',BudgetScope.DAY))
    assert row.estimated_all_calls==D('0.06') and row.budget_state=='unknown'
    assert row.missing==('budget',)


def test_explicit_budget_boundary_and_dated_source_not_current_price_claim():
    row,=estimate(budget=Budget(D('0.06'),'USD',BudgetScope.DAY))
    assert row.budget_state=='within_supplied_budget'
    row,=estimate(budget=Budget(D('0.059'),'USD',BudgetScope.DAY))
    assert row.budget_state=='exceeds_supplied_budget'
    old=replace(profile().rates,as_of=date(2026,10,1))
    assert estimate((profile(rates=old),))[0].price_age_days==15


def test_profiles_are_not_ranked_or_used_to_switch_provider():
    profiles=(profile(label='synthetic-slower',provider=Provider.ANTHROPIC),
              profile(label='synthetic-local',provider=Provider.LOCAL))
    rows=estimate(profiles)
    assert [row.label for row in rows]==[p.label for p in profiles]
    assert [row.provider for row in rows]==[p.provider for p in profiles]


@pytest.mark.parametrize('change', ['scope','currency','future','duplicate','raw'])
def test_comparison_scope_currency_date_and_raw_input_guards(change):
    values={}
    if change=='scope':values['budget']=Budget(D('1'),'USD',BudgetScope.SCENARIO)
    if change=='currency':values['budget']=Budget(D('1'),'EUR',BudgetScope.DAY)
    if change=='future':values['comparison_as_of']=date(2026,10,15)
    if change=='duplicate':values['profiles']=(profile(),profile())
    if change=='raw':values['profiles']=({'label':'synthetic','api_key':'not-accepted'},)
    with pytest.raises(ValueError):estimate(**values)


@pytest.mark.parametrize('value',[True,0.1,1,-1,D('-1'),D('NaN'),D('Infinity'),D('1E1000000000')])
def test_decimal_rates_and_budget_strict_and_bounded(value):
    with pytest.raises(ValueError):RateCard(value,D('1'),'USD',ASOF,'synthetic:source')
    with pytest.raises(ValueError):Budget(value,'USD',BudgetScope.DAY)


@pytest.mark.parametrize('changes',[
    {'input_tokens':True},{'input_tokens':-1},{'reserved_output_tokens':1.0},
    {'reserved_output_tokens':10_000_001},{'calls':0},{'calls':10001},{'calls':False},
])
def test_workload_bounds(changes):
    values=dict(input_tokens=2000,reserved_output_tokens=500,calls=1,scope=BudgetScope.DAY)
    values.update(changes)
    with pytest.raises(ValueError):Workload(**values)


def test_profile_schema_rejects_config_secrets_and_implicit_provider_or_limits():
    with pytest.raises(TypeError):profile(api_key='not-accepted')
    with pytest.raises(ValueError):profile(provider='openai')
    with pytest.raises(ValueError):profile(context_limit_tokens=0,output_limit_tokens=0)
    with pytest.raises(ValueError):profile(label='prompt\nsecret')
    with pytest.raises(ValueError):estimate(tuple(profile(label=str(i)) for i in range(21)))


def test_estimate_independent_of_global_decimal_context():
    expected=estimate()
    with localcontext() as context:
        context.prec=2
        assert estimate()==expected


def test_no_files_network_clock_or_runtime_adapter_calls(monkeypatch):
    import builtins,socket,time
    def forbidden(*args,**kwargs):pytest.fail('external operation')
    monkeypatch.setattr(builtins,'open',forbidden)
    monkeypatch.setattr(socket,'socket',forbidden)
    monkeypatch.setattr(time,'time',forbidden)
    assert trace_views((trace(),))[0].applied_count==1
    assert estimate()[0].estimated_all_calls==D('0.06')
