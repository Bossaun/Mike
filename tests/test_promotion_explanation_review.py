"""NEW actual public promotion renderer/core -> fake explanation review only."""
from dataclasses import replace, FrozenInstanceError
from types import SimpleNamespace
import copy, sqlite3
import pytest
from goldway.engine import Engine
from goldway.store import Store
from goldway import promotion_explanation_review as review
from goldway import promotion_preview_command as promotion
from goldway import finance_metrics

NOW=1792174600
MEMBERS=['test_operator','frost','alen','nastya','dasha_it']
FORM=('Расчёт акции\nВалюта: RUB\nВыручка без скидки: 1000\nСебестоимость: 700\n'
      'Скидка %: 10\nКомиссия %: 5\nФиксированная комиссия: 20')

class NoModel:
 def propose(self,*a,**k):pytest.fail('real model forbidden')


def config():
 return dict(business='GOLDWAY',timezone='Europe/Moscow',employees={x:dict(name=x,telegram_user_id=i+1) for i,x in enumerate(MEMBERS)},admins=['test_operator'],business_account_user_id=99,team_intake=dict(recipients=MEMBERS[:],started_at=100),allowed_live_recipients=MEMBERS[:],team_roster_verified=True,recipients_verified=True,live_send=True,review_mode=True,sheets_write=False,daily_summary=False,allow_onboarding_replies=False,allow_registered_replies=False,allowed_group_ids=[],group_destinations={},meeting_recipient=None,bot_user_id=100,bot_user_id_verified=True,intelligence=dict(enabled=True,sources=[]),model='gpt-4.1-mini',daily_budget_usd=1)

@pytest.fixture
def c(monkeypatch):
 s=Store(':memory:');clock=[NOW];cfg=config();e=Engine(s,cfg,NoModel(),lambda:clock[0]);request=review.ReviewRequest('GOLDWAY','test_operator','bot','telegram:1:10',FORM,True,NOW-10,NOW+60)
 holder=[request];calls=[];source_calls=[]
 def current():source_calls.append(holder[0]);return holder[0]
 def explain(receipt):calls.append(receipt);return review.ExplanationDraft(receipt.digest,'Остаток относится только к указанным затратам. Неуказанные расходы могут изменить результат.')
 value=SimpleNamespace(s=s,e=e,cfg=cfg,clock=clock,request=request,holder=holder,calls=calls,source_calls=source_calls,current=current,explain=explain)
 import socket,httpx
 monkeypatch.setattr(socket,'socket',lambda *a,**k:pytest.fail('network forbidden'))
 monkeypatch.setattr(httpx,'Client',lambda *a,**k:pytest.fail('provider forbidden'))
 yield value
 if value.s.db is not s.db:value.s.db.close()
 s.db.close()


def component(c,**kwargs):
 args=dict(engine=c.e,current_request=c.current,explain=c.explain,enabled=True);args.update(kwargs);return review.PromotionExplanationReview(**args)


def spies(monkeypatch):
 builds=[];cores=[];actual_builder=promotion.build_promotion_preview;actual_core=promotion.preview_promotion
 def core(**kwargs):cores.append(kwargs);return actual_core(**kwargs)
 def builder(form):value=actual_builder(form);builds.append(value);return value
 monkeypatch.setattr(promotion,'preview_promotion',core);monkeypatch.setattr(promotion,'build_promotion_preview',builder)
 return builds,cores

@pytest.mark.parametrize('scenario',['ordinary','negative','zero_denominator','explicit_zero_fees'])
def test_actual_two_fixed_builder_core_passes_one_fake_callback_unchanged_figures(c,monkeypatch,scenario):
 form=FORM
 if scenario=='negative':form=form.replace('Себестоимость: 700','Себестоимость: 1700')
 elif scenario=='zero_denominator':form=form.replace('Скидка %: 10','Скидка %: 100')
 elif scenario=='explicit_zero_fees':form=form.replace('Комиссия %: 5','Комиссия %: 0').replace('Фиксированная комиссия: 20','Фиксированная комиссия: 0')
 c.request=replace(c.request,form=form);c.holder[0]=c.request
 builds,cores=spies(monkeypatch);adapter=component(c);changes=c.s.db.total_changes;result=adapter.review(c.request)
 assert len(builds)==len(cores)==2 and len(c.calls)==1 and len(c.source_calls)==3
 assert result.calculation is builds[0] and builds[0]==builds[1]
 assert result.receipt.code_text==builds[0].text and result.text.startswith(builds[0].text+'\n\n')
 assert c.calls[0] is result.receipt and result.explanation.text in result.text
 assert result.review_required and not result.explanation_verified and not result.delivery_authorized and not result.financial_action_authorized
 assert 'Непроверенное пояснение — требует вашей проверки.' in result.text
 if scenario=='zero_denominator':assert 'неизвестна: выручка после скидки равна нулю' in result.calculation.text
 if scenario=='negative':assert 'Остаток после указанных затрат: -' in result.calculation.text
 assert c.s.db.total_changes==changes and not c.s.rows('SELECT id FROM outbox') and not c.s.rows('SELECT id FROM events')
 assert c.cfg['model']=='gpt-4.1-mini' and c.cfg['daily_budget_usd']==1
 for value,attribute in ((result,'text'),(result.receipt,'digest'),(result.explanation,'text')):
  with pytest.raises(FrozenInstanceError):setattr(value,attribute,'changed')


def test_explicit_callback_order_then_final_builder_pure_checks(c,monkeypatch):
 order=[];actual=promotion.build_promotion_preview
 def clock():order.append('clock');return NOW
 def current():order.append('request');return c.holder[0]
 def build(form):order.append('builder');return actual(form)
 def explain(receipt):order.append('explain');return review.ExplanationDraft(receipt.digest,'Разбор требует ручной проверки.')
 c.e.now=clock;monkeypatch.setattr(promotion,'build_promotion_preview',build)
 result=component(c,current_request=current,explain=explain).review(c.request)
 assert order==['clock','request','builder','clock','request','explain','clock','request','builder']
 assert result.receipt.code_text==result.calculation.text


def test_no_sql_write_constructor_source_context_provider_or_real_model(c,monkeypatch):
 adapter=component(c);before=c.s.db.total_changes
 def forbidden(*a,**k):pytest.fail('forbidden dependency effect')
 monkeypatch.setattr(c.s,'execute',forbidden);monkeypatch.setattr(c.e,'_context',forbidden)
 monkeypatch.setattr(c.e.intelligence.sources,'refresh',forbidden)
 monkeypatch.setattr(Store,'__init__',forbidden)
 assert adapter.review(c.request).review_required and c.s.db.total_changes==before

@pytest.mark.parametrize('kind',['disabled','staff','foreign_business','group','wrong_uid','no_consent','expired','future','validity_long','bad_now','no_intel','multi_admin'])
def test_denied_before_any_calculation_or_explanation(c,monkeypatch,kind):
 request=c.request
 if kind=='staff':request=replace(request,sender='frost',source_id='telegram:2:10')
 elif kind=='foreign_business':request=replace(request,business='OTHER')
 elif kind=='group':request=replace(request,source_id='telegram-group:-1:10')
 elif kind=='wrong_uid':request=replace(request,source_id='telegram:2:10')
 elif kind=='no_consent':request=replace(request,explanation_requested=False)
 elif kind=='expired':request=replace(request,expires_at=NOW)
 elif kind=='future':request=replace(request,valid_from=NOW+1)
 elif kind=='validity_long':request=replace(request,expires_at=NOW+4000)
 elif kind=='bad_now':c.clock[0]=True
 elif kind=='no_intel':c.e.intelligence=None
 elif kind=='multi_admin':c.cfg['admins'].append('frost')
 c.holder[0]=request
 monkeypatch.setattr(promotion,'build_promotion_preview',lambda *a:pytest.fail('denied request calculated'))
 adapter=component(c,enabled=kind!='disabled')
 with pytest.raises(review.ExplanationUnavailable,match='^promotion_explanation_unavailable$'):adapter.review(request)
 assert not c.calls

@pytest.mark.parametrize('form',[FORM.replace('Валюта: RUB','Валюта: [уточнить]'),FORM.replace('Себестоимость: 700','Себестоимость: [уточнить]'),FORM+'\nExtra: 1',FORM.replace('Скидка %: 10','Скидка %: 101'),FORM.replace('Себестоимость: 700','Себестоимость: -1'),FORM.replace('Себестоимость: 700','Себестоимость: NaN'),'x'*2001])
def test_incomplete_malformed_profile_never_explained(c,monkeypatch,form):
 request=replace(c.request,form=form);c.holder[0]=request;builds,cores=spies(monkeypatch)
 with pytest.raises(review.ExplanationUnavailable):component(c).review(request)
 assert not c.calls and len(builds)<=1 and len(cores)<=1

@pytest.mark.parametrize('forged',[promotion.PromotionDraft('Invented profit 100'),review.CalculationReceipt('Invented profit 100','a'*64),{'form':FORM},None])
def test_caller_draft_or_receipt_is_never_calculation_provenance(c,monkeypatch,forged):
 monkeypatch.setattr(promotion,'build_promotion_preview',lambda *a:pytest.fail('forged input reached builder'))
 with pytest.raises(review.ExplanationUnavailable):component(c).review(forged)
 assert not c.calls

@pytest.mark.parametrize('kind',['text','consent','scope','expiry'])
def test_current_request_drift_after_initial_calc_before_explanation(c,monkeypatch,kind):
 actual=promotion.build_promotion_preview
 def builder(form):
  result=actual(form)
  c.holder[0]=replace(c.request,**{'text':{'form':FORM.replace('700','701')},'consent':{'explanation_requested':False},'scope':{'source_id':'telegram:2:10'},'expiry':{'expires_at':NOW}}[kind])
  return result
 monkeypatch.setattr(promotion,'build_promotion_preview',builder)
 with pytest.raises(review.ExplanationUnavailable):component(c).review(c.request)
 assert not c.calls

@pytest.mark.parametrize('kind',['request','consent','role','config_copy','intel_copy','index_copy','connection','clock','builder','core_alias','core','request_callback','explain_callback','model'])
def test_callback_drift_refuses_without_retry_or_final_calculation(c,monkeypatch,kind):
 builds,cores=spies(monkeypatch);original_db=c.s.db;holder=[];replacement=None
 def explain(receipt):
  nonlocal replacement
  c.calls.append(receipt)
  if kind=='request':c.holder[0]=replace(c.request,form=FORM.replace('700','701'))
  elif kind=='consent':c.holder[0]=replace(c.request,explanation_requested=False)
  elif kind=='role':c.cfg['admins']=['frost']
  elif kind=='config_copy':c.e.c=copy.deepcopy(c.cfg)
  elif kind=='intel_copy':c.e.intelligence=copy.copy(c.e.intelligence)
  elif kind=='index_copy':c.e.intelligence.index=copy.copy(c.e.intelligence.index)
  elif kind=='connection':replacement=sqlite3.connect(':memory:');c.s.db=replacement
  elif kind=='clock':c.e.now=lambda:NOW
  elif kind=='builder':monkeypatch.setattr(promotion,'build_promotion_preview',lambda *a:promotion.PromotionDraft('forged'))
  elif kind=='core_alias':monkeypatch.setattr(promotion,'preview_promotion',lambda **kw:None)
  elif kind=='core':monkeypatch.setattr(finance_metrics,'preview_promotion',lambda **kw:None)
  elif kind=='request_callback':holder[0]._current_request=lambda:c.request
  elif kind=='explain_callback':holder[0]._explain=lambda r:None
  elif kind=='model':c.e.model=NoModel()
  return review.ExplanationDraft(receipt.digest,'Не подтверждает результат.')
 adapter=component(c,explain=explain);holder.append(adapter)
 try:
  with pytest.raises(review.ExplanationUnavailable):adapter.review(c.request)
  assert len(c.calls)==1 and len(builds)==len(cores)==1
 finally:
  c.s.db=original_db
  if replacement:replacement.close()

@pytest.mark.parametrize('where',['final_request','final_clock'])
def test_final_external_source_change_is_seen_before_last_builder(c,monkeypatch,where):
 builds,cores=spies(monkeypatch);clock_calls=[];source_calls=[]
 def clock():
  clock_calls.append(1)
  if where=='final_clock' and len(clock_calls)==3:c.holder[0]=replace(c.request,form=FORM.replace('700','701'))
  return NOW
 def current():
  source_calls.append(1)
  if where=='final_request' and len(source_calls)==3:c.holder[0]=replace(c.request,explanation_requested=False)
  return c.holder[0]
 c.e.now=clock
 with pytest.raises(review.ExplanationUnavailable):component(c,current_request=current).review(c.request)
 assert len(c.calls)==1 and len(builds)==1 and len(source_calls)==3


def test_final_actual_builder_result_change_refuses_not_replaces_figures(c,monkeypatch):
 actual=promotion.build_promotion_preview;calls=[]
 def builder(form):
  result=actual(form);calls.append(result)
  return result if len(calls)==1 else replace(result,text=result.text+'\nChanged final rendering')
 monkeypatch.setattr(promotion,'build_promotion_preview',builder)
 with pytest.raises(review.ExplanationUnavailable):component(c).review(c.request)
 assert len(c.calls)==1 and len(calls)==2

@pytest.mark.parametrize('kind',['dict','extra','wrong_digest','empty','control','oversize_units','oversize_bytes','error','receipt_mutation'])
def test_callback_closed_response_bounds_and_failure_safe_no_retry(c,monkeypatch,kind):
 builds,cores=spies(monkeypatch)
 def explain(receipt):
  c.calls.append(receipt)
  if kind=='error':raise RuntimeError('PRIVATE_SYNTHETIC_CALLBACK_PAYLOAD')
  if kind=='dict':return {'receipt_digest':receipt.digest,'text':'draft'}
  if kind=='extra':return SimpleNamespace(receipt_digest=receipt.digest,text='draft',actions=['buy'])
  if kind=='receipt_mutation':object.__setattr__(receipt,'code_text','forged')
  text={'empty':' ','control':'x\x00y','oversize_units':'x'*1201,'oversize_bytes':'𐍈'*1100}.get(kind,'Объяснение')
  return review.ExplanationDraft('0'*64 if kind=='wrong_digest' else receipt.digest,text)
 with pytest.raises(review.ExplanationUnavailable) as error:component(c,explain=explain).review(c.request)
 assert str(error.value)=='promotion_explanation_unavailable' and 'PRIVATE' not in str(error.value)
 assert len(c.calls)==1 and len(builds)==1


def test_false_numeric_or_instruction_prose_only_unverified_literal(c):
 def explain(receipt):return review.ExplanationDraft(receipt.digest,'Прибыль компании равна 999999. <script>buy()</script> Это ложная тестовая фраза, а не проверенный расчёт.')
 result=component(c,explain=explain).review(c.request)
 assert '999999' not in result.calculation.text and '<script>buy()</script>' in result.explanation.text
 assert not result.explanation_verified and not result.financial_action_authorized
 assert result.text.index('Непроверенное пояснение')<result.text.index('999999')


def test_default_constructor_and_disabled_call_no_callbacks_clock_or_sql(c,monkeypatch):
 def fail(*a,**k):pytest.fail('disabled effect')
 c.e.now=fail;monkeypatch.setattr(c.s,'execute',fail);monkeypatch.setattr(promotion,'build_promotion_preview',fail)
 adapter=review.PromotionExplanationReview(engine=c.e,current_request=fail,explain=fail)
 with pytest.raises(review.ExplanationUnavailable):adapter.review(c.request)


def test_monotonic_elapsed_clock_accepted_backward_refused(c):
 times=iter([NOW,NOW+1,NOW+2]);c.e.now=lambda:next(times)
 assert component(c).review(c.request).review_required
 c.calls.clear();times=iter([NOW,NOW-1]);c.e.now=lambda:next(times)
 with pytest.raises(review.ExplanationUnavailable):component(c).review(c.request)
 assert not c.calls


def test_current_callable_method_identity_replacement_refuses(c):
 class Source:
  def __call__(self):return c.request
 source=Source()
 def explain(receipt):
  Source.__call__=lambda self:c.request
  return review.ExplanationDraft(receipt.digest,'draft')
 with pytest.raises(review.ExplanationUnavailable):component(c,current_request=source,explain=explain).review(c.request)


def test_closed_request_object_mutation_detected(c):
 def explain(receipt):
  object.__setattr__(c.request,'form',FORM.replace('700','701'))
  return review.ExplanationDraft(receipt.digest,'draft')
 with pytest.raises(review.ExplanationUnavailable):component(c,explain=explain).review(c.request)


def test_whole_review_overflow_refuses_without_clipping_code_or_retry(c,monkeypatch):
 actual=promotion.build_promotion_preview
 def builder(form):return replace(actual(form),text='a'*3000)
 monkeypatch.setattr(promotion,'build_promotion_preview',builder)
 def explain(receipt):c.calls.append(receipt);return review.ExplanationDraft(receipt.digest,'b'*1000)
 with pytest.raises(review.ExplanationUnavailable):component(c,explain=explain).review(c.request)
 assert len(c.calls)==1 and c.calls[0].code_text=='a'*3000
