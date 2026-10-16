"""Synthetic supplied observations; no real posts, services or authorization."""
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo
import pytest
from goldway.marketing_drafts import (Publication,PublicationObservation,publication_gap,
    Asset,DesignBrief,render_design_brief,MetricDefinition,Campaign,MetricObservation,
    campaign_report,render_campaign_report,DraftError)

NOW=datetime(2026,10,16,12,tzinfo=timezone.utc)
START=NOW-timedelta(days=7)


def observation(*,coverage='complete',publications=(),start=START,end=NOW,observed=NOW):
    return PublicationObservation('channel',observed,start,end,coverage,'scan',publications)


def gap(value,**changes):
    args=dict(channel_id='channel',now=NOW,threshold=timedelta(days=7),max_observation_age=timedelta(hours=1))
    args.update(changes);return publication_gap(value,**args)


def test_gap_requires_complete_explicit_window_and_always_requires_review():
    result=gap(observation())
    assert result.state=='gap' and result.as_of==NOW and result.window_start==START
    assert result.window_publication_count==0 and result.last_observed_publication is None
    assert result.source_ids==('scan',) and result.review_required is True and result.live_authorized is False
    with pytest.raises(FrozenInstanceError):result.live_authorized=True


@pytest.mark.parametrize('coverage',['partial','unknown'])
def test_missing_and_partial_observations_do_not_prove_no_publications(coverage):
    result=gap(observation(coverage=coverage))
    assert result.state=='unknown' and result.window_publication_count is None
    missing=gap(None);assert missing.state=='unknown' and missing.coverage=='missing' and missing.as_of is None


def test_short_and_stale_coverage_cannot_prove_gap():
    assert gap(observation(start=START+timedelta(seconds=1))).state=='unknown'
    stale=observation(start=START-timedelta(hours=2),end=NOW-timedelta(hours=2),observed=NOW)
    assert gap(stale).state=='unknown'
    old_observation=replace(stale,observed_at=stale.coverage_end)
    assert gap(old_observation).window_publication_count is None


def test_asof_is_coverage_end_not_silently_current_time():
    end=NOW-timedelta(minutes=30)
    result=gap(observation(start=end-timedelta(days=7),end=end))
    assert result.state=='gap' and result.as_of==end and result.window_start==end-timedelta(days=7)


@pytest.mark.parametrize('offset',[timedelta(0),timedelta(days=6,hours=23)])
def test_explicit_publication_in_window_including_start_prevents_gap(offset):
    post=Publication('post','channel',START+offset,'post-source')
    result=gap(observation(publications=(post,)))
    assert result.state=='recent_publication' and result.window_publication_count==1
    assert result.last_observed_publication==post.published_at and set(result.source_ids)=={'scan','post-source'}


def test_known_recent_post_can_disprove_gap_without_complete_count():
    post=Publication('post','channel',NOW-timedelta(hours=2),'post-source')
    result=gap(observation(coverage='partial',publications=(post,)))
    assert result.state=='recent_publication' and result.window_publication_count is None


def test_old_publication_is_not_inferred_to_be_last_in_full_history():
    post=Publication('old','channel',START-timedelta(seconds=1),'post-source')
    result=gap(observation(start=START-timedelta(days=1),publications=(post,)))
    assert result.state=='gap' and result.last_observed_publication==post.published_at and result.window_publication_count==0


@pytest.mark.parametrize('change',['wrong-channel','duplicate','at-end','future','naive'])
def test_publication_provenance_and_time_boundaries(change):
    post=Publication('post','channel',START,'source')
    if change=='wrong-channel':post=replace(post,channel_id='other')
    if change=='at-end':post=replace(post,published_at=NOW)
    if change=='naive':
        with pytest.raises(DraftError):replace(post,published_at=NOW.replace(tzinfo=None))
        return
    if change=='future':
        with pytest.raises(DraftError):gap(observation(observed=NOW+timedelta(seconds=1)))
        return
    with pytest.raises(DraftError):observation(publications=(post,post) if change=='duplicate' else (post,))


def test_gap_expected_channel_aware_clock_and_threshold_are_explicit():
    for changes in (dict(channel_id='other'),dict(now=NOW.replace(tzinfo=None)),dict(threshold=timedelta(0)),dict(max_observation_age=1)):
        with pytest.raises(DraftError):gap(observation(),**changes)


def brief():
    return DesignBrief('brief','Показать новую упаковку','Указанная аудитория','Квадратный макет',NOW,
        (Asset('asset','Предоставленное фото','asset-source'),),True,frozenset())


def test_editable_brief_preserves_explicit_fields_assets_and_review_requirement():
    original=brief();draft=render_design_brief(original)
    assert all(value in draft.text for value in (original.goal,original.audience,original.format,NOW.isoformat(),'asset-source'))
    assert draft.source_ids==('asset-source',) and draft.review_required and not draft.live_authorized
    edited=replace(original,goal='Другая цель')
    assert 'Другая цель' in render_design_brief(edited).text and original.goal!='Другая цель'
    with pytest.raises(FrozenInstanceError):original.goal='mutated'


def test_unknown_brief_fields_are_explicit_not_invented_defaults():
    unknown=DesignBrief('brief',None,None,None,None,(),False,frozenset({'goal','audience','format','deadline','assets'}))
    text=render_design_brief(unknown).text
    assert text.count('[уточнить]')==4 and '[не предоставлены]' in text
    assert 'нет по переданным данным' not in text
    known_empty=replace(unknown,assets_confirmed=True,unknown_fields=unknown.unknown_fields-{'assets'})
    assert '[подтверждено: материалов нет]' in render_design_brief(known_empty).text


@pytest.mark.parametrize('change',[{'goal':None},{'unknown_fields':frozenset({'goal'})},
    {'assets_confirmed':1},{'deadline':NOW.replace(tzinfo=None)},
    {'assets':(Asset('asset','Photo','source'),Asset('asset','Photo2','source2'))},
    {'assets':[]},{'goal':'x'*1001}])
def test_brief_requires_consistent_unknowns_bounds_and_immutable_inputs(change):
    with pytest.raises(DraftError):replace(brief(),**change)


def campaign():
    return Campaign('campaign',START,NOW,(
        MetricDefinition('clicks','Переходы','Число переходов по переданному отчёту за период','count'),
        MetricDefinition('orders','Заказы','Число заказов по переданному отчёту за период','count')))


def metric(value='0',coverage='complete'):
    return MetricObservation('metric','campaign','clicks','count',value,START,NOW,NOW,coverage,'metric-source')


def test_period_report_preserves_metric_definition_unit_coverage_and_source():
    report=campaign_report(campaign(),(metric('12.50'),),now=NOW)
    assert report.period_state=='closed' and report.rows[0].value==Decimal('12.50')
    assert report.rows[0].definition.unit=='count' and report.rows[0].source_id=='metric-source'
    assert report.rows[1].value is None and report.rows[1].coverage=='missing'
    draft=render_campaign_report(report)
    assert '12.50 count' in draft.text and '[нет наблюдения]' in draft.text
    assert campaign().metrics[0].definition in draft.text
    assert draft.source_ids==('metric-source',) and report.review_required and not report.live_authorized
    assert draft.review_required and not draft.live_authorized


def test_explicit_zero_is_not_missing_and_no_ratio_or_financial_inference():
    report=campaign_report(campaign(),(metric('0'),),now=NOW)
    assert report.rows[0].value==0 and report.rows[0].coverage=='complete'
    assert report.rows[1].value is None and report.rows[1].coverage=='missing'
    assert len(report.rows)==2 and 'ROI' not in render_campaign_report(report).text


def test_partial_campaign_report_does_not_claim_closed_period_or_full_coverage():
    observed=NOW-timedelta(days=1)
    item=replace(metric('5','partial'),observed_at=observed)
    report=campaign_report(campaign(),(item,),now=observed)
    assert report.period_state=='in_progress' and report.rows[0].coverage=='partial'
    assert campaign_report(campaign(),(),now=START-timedelta(seconds=1)).period_state=='before_start'
    with pytest.raises(DraftError):replace(item,coverage='complete')


@pytest.mark.parametrize('change',['campaign','metric','unit','period','future','duplicate-key','duplicate-id'])
def test_campaign_exact_identity_definition_units_and_window(change):
    item=metric();items=(item,)
    if change=='campaign':items=(replace(item,campaign_id='other'),)
    if change=='metric':items=(replace(item,metric_key='unknown'),)
    if change=='unit':items=(replace(item,unit='EUR'),)
    if change=='period':items=(replace(item,start=START+timedelta(seconds=1)),)
    if change=='future':items=(replace(item,observed_at=NOW+timedelta(seconds=1)),)
    if change=='duplicate-key':items+=(replace(item,id='other'),)
    if change=='duplicate-id':items+=(replace(item,metric_key='orders'),)
    with pytest.raises(DraftError):campaign_report(campaign(),items,now=NOW)


@pytest.mark.parametrize('value',[True,False,1,1.5,None,'',' 1','NaN','Infinity',Decimal('sNaN'),'1e999999'])
def test_metric_values_are_explicit_bounded_finite_decimal(value):
    with pytest.raises(DraftError):metric(value)


def test_timezone_normalization_preserves_instants_without_naive_assumptions():
    moscow=NOW.astimezone(ZoneInfo('Europe/Moscow'))
    assert gap(observation(observed=moscow),now=moscow)==gap(observation())
    assert replace(brief(),deadline=moscow).deadline==NOW


def test_record_bounds_and_no_live_authorization_constructor():
    with pytest.raises(DraftError):replace(campaign(),metrics=campaign().metrics*2)
    with pytest.raises(DraftError):replace(campaign(),metrics=tuple(replace(campaign().metrics[0],key=str(i)) for i in range(21)))
    with pytest.raises(DraftError):observation(publications=tuple(Publication(str(i),'channel',START,'s') for i in range(201)))
    draft=render_design_brief(brief())
    with pytest.raises(ValueError):replace(draft,live_authorized=True)


def test_all_helpers_are_local_without_io_clock_network_or_sqlite(monkeypatch):
    import builtins,socket,sqlite3,time
    def forbidden(*a,**k):pytest.fail('unexpected external effect')
    for obj,name in ((builtins,'open'),(socket,'socket'),(sqlite3,'connect'),(time,'time')):monkeypatch.setattr(obj,name,forbidden)
    assert gap(observation()).state=='gap'
    assert render_design_brief(brief()).review_required
    assert not render_campaign_report(campaign_report(campaign(),(metric(),),now=NOW)).live_authorized
