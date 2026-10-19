"""I60 actual two-list unmatched recount sample via private Engine."""
from types import SimpleNamespace
from datetime import datetime,timezone
from decimal import Decimal
import json
import pytest
from goldway.engine import Engine
from goldway.store import Store
from goldway.telegram import Dispatcher
import goldway.recount_sample_command as command

MEMBERS=['test_operator','frost','alen','nastya','dasha_it'];NOW=1792174600


@pytest.fixture
def case(monkeypatch):
 s=Store(':memory:');clock=[NOW]
 c=dict(business='GOLDWAY',timezone='Europe/Moscow',employees={x:dict(name=x,telegram_user_id=i+1) for i,x in enumerate(MEMBERS)},admins=['test_operator'],business_account_user_id=99,
  team_intake=dict(recipients=MEMBERS[:],started_at=100),allowed_live_recipients=MEMBERS[:],team_roster_verified=True,recipients_verified=True,live_send=True,review_mode=True,
  sheets_write=False,daily_summary=False,allow_onboarding_replies=False,allow_registered_replies=False,allowed_group_ids=[],group_destinations={},meeting_recipient=None,intelligence=dict(enabled=True,sources=[]))
 e=Engine(s,c,None,lambda:clock[0]);s.execute('INSERT INTO chats(business,recipient,bot_chat,business_chat,connection,last_inbound) VALUES(?,?,?,?,?,?)',(c['business'],'test_operator','1','1','conn',NOW))
 class API:
  def __init__(self):self.calls=[]
  def call(self,method,**kw):
   if method=='getBusinessConnection':return dict(is_enabled=True,rights=dict(can_reply=True),user=dict(id=99))
   assert method=='sendMessage';self.calls.append(kw);return dict(message_id=100+len(self.calls))
 api=API();d=Dispatcher(s,c,api,lambda:clock[0])
 def fail(*a,**kw):pytest.fail('unexpected model/source/context/constructor/network call')
 e.model=SimpleNamespace(propose=fail);e._context=fail;e.intelligence.sources.refresh=fail;e.intelligence.index.search=fail;e.intelligence.memory.get=fail
 import builtins,socket,httpx
 monkeypatch.setattr(builtins,'open',fail);monkeypatch.setattr(socket,'socket',fail);monkeypatch.setattr(httpx,'Client',fail)
 yield SimpleNamespace(s=s,c=c,e=e,d=d,api=api,clock=clock)
 s.db.close()


A='A <item>; base; L1; B1'
B='B; base; L1; B1'
C='C; base; L1; B1'
BLOCK=(command.HEADER+'\nНазвание сценария: Выборка <A> & тест\nДата наблюдения: 2026-10-19\nЕдиница: шт\nУчётных строк: 2\nФактических строк: 2\n'
 'Учёт 1: '+A+'; 10\nУчёт 2: '+B+'; 0\nФакт 1: '+A+'; 8\nФакт 2: '+C+'; неизвестно\nОснование: '+command.BASIS)


def submit(c,text=BLOCK,event='recount-sample',sender='test_operator',channel='bot',source=None):
 c.e.reply(event,sender,text,channel=channel,source_id=source or ('telegram:1:10' if channel=='bot' else 'business:conn:1:10'),message_at=c.clock[0]);c.e.process(event)
 return c.s.execute('SELECT * FROM outbox WHERE business=? AND dedupe=?',(c.c['business'],command.PREFIX+command._hash(event))).fetchone()


def changed(text=BLOCK,**fields):
 return '\n'.join(line.split(': ',1)[0]+': '+fields[line.split(': ',1)[0]] if ': ' in line and line.split(': ',1)[0] in fields else line for line in text.splitlines())


@pytest.mark.parametrize('change',['employee','group','foreign_private','nonclosed','no_intel','business_mismatch','two_admins'])
def test_private_current_admin_host_scope_only(case,change):
 kwargs={}
 if change=='employee':kwargs['sender']='frost';kwargs['source']='telegram:2:10'
 elif change=='group':kwargs['channel']='group';kwargs['source']='telegram:-4:10'
 elif change=='foreign_private':kwargs['source']='telegram:2:10'
 elif change=='nonclosed':case.c['team_roster_verified']=False
 elif change=='no_intel':case.e.intelligence=None
 elif change=='business_mismatch':case.e.b='OTHER'
 elif change=='two_admins':case.c['admins'].append('frost')
 assert submit(case,**kwargs) is None
 assert not case.s.rows("SELECT * FROM outbox WHERE dedupe LIKE 'recount-sample-preview:%'")


@pytest.mark.parametrize('change',['config','intelligence','index','policy','recipient','body','transport','queue_failure'])
def test_atomic_host_policy_body_original_transport_race_rollback(case,monkeypatch,change):
 actual=case.s.queue
 def queue(*a,**kw):
  result=actual(*a,**kw)
  if str(a[3]).startswith(command.PREFIX):
   import copy
   if change=='config':case.e.c=copy.deepcopy(case.c)
   elif change=='intelligence':case.e.intelligence=copy.copy(case.e.intelligence)
   elif change=='index':case.e.intelligence.index=copy.copy(case.e.intelligence.index)
   elif change=='policy':case.c['review_mode']=False
   elif change=='recipient':case.s.execute("UPDATE outbox SET recipient='frost' WHERE dedupe=?",(a[3],))
   elif change=='body':case.s.execute("UPDATE outbox SET text='forged' WHERE dedupe=?",(a[3],))
   elif change=='transport':case.s.execute("UPDATE outbox SET preferred_transport='group' WHERE dedupe=?",(a[3],))
   elif change=='queue_failure':raise RuntimeError('synthetic queue failure')
  return result
 monkeypatch.setattr(case.s,'queue',queue)
 with pytest.raises((ValueError,RuntimeError)):submit(case)
 assert not case.s.rows("SELECT * FROM events WHERE id='recount-sample'") and not case.s.rows('SELECT * FROM outbox')




def spy_core(monkeypatch):
 actual=command.core.recount;calls=[];results=[]
 def recount(book,physical):
  calls.append((book,physical));value=actual(book,physical);results.append(value);return value
 monkeypatch.setattr(command.core,'recount',recount)
 return calls,results


def lists(book,physical,text=BLOCK):
 lines=[line for line in text.splitlines() if not line.startswith('Учёт ') and not line.startswith('Факт ')]
 lines=[('Учётных строк: '+str(len(book))) if line.startswith('Учётных строк: ') else ('Фактических строк: '+str(len(physical))) if line.startswith('Фактических строк: ') else line for line in lines]
 lines[-1:-1]=['Учёт '+str(i)+': '+row for i,row in enumerate(book,1)]+['Факт '+str(i)+': '+row for i,row in enumerate(physical,1)]
 return '\n'.join(lines)


@pytest.mark.parametrize('channel',['bot','business'])
def test_actual_engine_two_lists_recount_comparable_bookonly_physicalonly_own_dispatch(case,monkeypatch,channel):
 calls,results=spy_core(monkeypatch)
 snapshots={t:case.s.rows('SELECT * FROM '+t) for t in ('tasks','sources','task_sources','knowledge_documents','runtime','usage','task_status_history')}
 import sqlite3
 writes=[]
 def authorize(action,table,column,*args):
  if action==sqlite3.SQLITE_READ and (table in snapshots or table in ('knowledge_chunks','knowledge_readers')):return sqlite3.SQLITE_DENY
  if action in (sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE):writes.append(table)
  return sqlite3.SQLITE_OK
 case.s.db.set_authorizer(authorize)
 from goldway.knowledge import KnowledgeIndex
 with monkeypatch.context() as m:
  m.setattr(KnowledgeIndex,'__init__',lambda *a,**kw:pytest.fail('new command constructed Index'))
  for name in ('coverage','stock_flags','procurement_preview','completeness'):
   m.setattr(command.core,name,lambda *a,**kw:pytest.fail('not another inventory calculator'))
  row=submit(case,channel=channel)
 case.s.db.set_authorizer(None)
 assert row and len(calls)==len(results)==1
 book,physical=calls[0];lines=results[0]
 assert len(book)==len(physical)==2 and len(lines)==3
 assert all(type(r) is command.core.StockSnapshot and r.kind is command.core.StockKind.BOOK for r in book)
 assert all(type(r) is command.core.StockSnapshot and r.kind is command.core.StockKind.PHYSICAL for r in physical)
 assert type(book[0].key) is command.core.StockKey and type(book[0].key.item) is command.core.ItemKey and type(book[0].units) is Decimal
 assert [line.state for line in lines]==[command.core.RecountState.COMPARABLE,command.core.RecountState.MISSING_PHYSICAL,command.core.RecountState.MISSING_BOOK]
 assert lines[0].delta==Decimal('-2') and lines[1].book_units==0 and lines[1].physical_units is command.core.UNKNOWN and lines[2].physical_units is command.core.UNKNOWN
 assert lines[1].delta is lines[2].delta is command.core.UNKNOWN
 body=row['text'];assert 'Выборка &lt;A&gt; &amp; тест' in body and 'SKU A &lt;item&gt;' in body and 'Разница факт − учёт: -2' in body
 assert 'в переданном списке подсчёта нет строки' in body and 'в переданном учётном списке нет строки' in body
 assert 'независимо не проверены' in body and 'не вывод о реальных остатках, потерях или завершении' in body
 assert body.count('<b>Позиция ')==3
 for r in (*book,*physical):assert r.source_id not in body
 for raw in ('missing_book','UNKNOWN','comparable','source_id','Итого'):assert raw not in body
 assert row['recipient']=='test_operator' and row['task_id'] is None and row['preferred_transport']==channel and row['parse_mode']=='HTML'
 assert set(writes)<={'events','answer_citation_turns','outbox'}
 for table,before in snapshots.items():assert before==case.s.rows('SELECT * FROM '+table)
 assert not case.s.rows('SELECT * FROM intelligence_outbox_acl')
 case.d.flush();assert len(case.api.calls)==1 and case.api.calls[0]['chat_id']=='1'
 if channel=='business':assert case.api.calls[0]['business_connection_id']=='conn'


@pytest.mark.parametrize('side',['book','physical'])
def test_zero_side_is_no_supplied_records_not_unknown_or_zero_padding(case,monkeypatch,side):
 calls,results=spy_core(monkeypatch)
 row=submit(case,lists([A+'; 0'] if side=='book' else [],[A+'; 0'] if side=='physical' else []))
 book,physical=calls[0];line=results[0][0]
 assert len(book)==(1 if side=='book' else 0) and len(physical)==(1 if side=='physical' else 0)
 assert line.state is (command.core.RecountState.MISSING_PHYSICAL if side=='book' else command.core.RecountState.MISSING_BOOK)
 assert line.delta is command.core.UNKNOWN and 'Разница факт − учёт: не определена' in row['text']


@pytest.mark.parametrize('book,physical,delta',[('0','0','0'),('0','5','+5'),('5','0','-5'),('0.30','0.10','-0.20')])
def test_actual_zero_is_present_and_signed_exact_difference_not_handler_math(case,monkeypatch,book,physical,delta):
 calls,results=spy_core(monkeypatch)
 row=submit(case,lists([A+'; '+book],[A+'; '+physical]));line=results[0][0]
 assert line.state is command.core.RecountState.COMPARABLE and line.delta==Decimal(delta)
 assert 'Разница факт − учёт: '+delta in row['text']


@pytest.mark.parametrize('book,physical',[('неизвестно','1'),('1','неизвестно'),('неизвестно','неизвестно')])
def test_actual_unknown_quantity_is_present_unknown_not_missing_record(case,monkeypatch,book,physical):
 calls,results=spy_core(monkeypatch)
 row=submit(case,lists([A+'; '+book],[A+'; '+physical]));line=results[0][0]
 assert len(calls[0][0])==len(calls[0][1])==1 and line.state is command.core.RecountState.UNKNOWN_QUANTITY and line.delta is command.core.UNKNOWN
 assert 'в переданной строке неизвестно количество' in row['text']


@pytest.mark.parametrize('dimension',[1,2,3])
def test_actual_unknown_dimension_never_authorizes_numerical_join(case,monkeypatch,dimension):
 calls,results=spy_core(monkeypatch)
 parts=A.split('; ');parts[dimension]='неизвестно';key='; '.join(parts)
 row=submit(case,lists([key+'; 5'],[key+'; 7']));line=results[0][0]
 assert line.state is command.core.RecountState.UNRESOLVED_KEY and line.delta is command.core.UNKNOWN and 'числовое сопоставление не выполнено' in row['text']
 assert getattr(calls[0][0][0].key.item,'variant') is command.core.UNKNOWN if dimension==1 else getattr(calls[0][0][0].key,'location' if dimension==2 else 'batch') is command.core.UNKNOWN


def test_actual_priority_missing_then_unknown_quantity_then_unresolved_key(case,monkeypatch):
 calls,results=spy_core(monkeypatch)
 key='A; неизвестно; неизвестно; неизвестно'
 row=submit(case,lists([key+'; неизвестно'],[]));assert results[0][0].state is command.core.RecountState.MISSING_PHYSICAL
 row=submit(case,lists([key+'; неизвестно'],[key+'; 1']),event='unknown');assert results[1][0].state is command.core.RecountState.UNKNOWN_QUANTITY


@pytest.mark.parametrize('physical',[
 'A <item>; other; L1; B1; 8','A <item>; base; L2; B1; 8','A <item>; base; L1; B2; 8',
 'a <item>; base; L1; B1; 8','A <item>; неизвестно; L1; B1; 8'])
def test_exact_key_dimensions_and_case_never_fuzzy_merge_different_goods(case,monkeypatch,physical):
 calls,results=spy_core(monkeypatch)
 row=submit(case,lists([A+'; 10'],[physical]))
 assert len(results[0])==2 and {l.state for l in results[0]}=={command.core.RecountState.MISSING_BOOK,command.core.RecountState.MISSING_PHYSICAL}
 assert all(l.delta is command.core.UNKNOWN for l in results[0]) and row['text'].count('<b>Позиция ')==2


def test_max_five_keys_ten_records_all_rows_visible_once_no_totals(case,monkeypatch):
 calls,results=spy_core(monkeypatch)
 book=[str(i)+'; base; L; B; 10' for i in range(1,6)];physical=[str(i)+'; base; L; B; 11' for i in range(1,6)]
 row=submit(case,lists(book,physical))
 assert len(calls[0][0])==len(calls[0][1])==5 and len(results[0])==5 and row['text'].count('<b>Позиция ')==5
 assert 'Итого' not in row['text'] and 'общий остаток' not in row['text']


def test_six_union_keys_whole_refusal_before_core_not_drop_last_key(case,monkeypatch):
 monkeypatch.setattr(command.core,'recount',lambda *a,**kw:pytest.fail('union over limit core'))
 book=[str(i)+'; base; L; B; 1' for i in (1,2,3)];physical=[str(i)+'; base; L; B; 1' for i in (4,5,6)]
 assert submit(case,lists(book,physical))['text']==command.NOTICE


@pytest.mark.parametrize('side',['book','physical'])
def test_duplicate_exact_keys_on_one_side_reject_even_identical_quantities(case,monkeypatch,side):
 monkeypatch.setattr(command.core,'recount',lambda *a,**kw:pytest.fail('duplicate input core'))
 rows=[A+'; 1',A+'; 1']
 assert submit(case,lists(rows,[]) if side=='book' else lists([],rows))['text']==command.NOTICE


def test_actual_difference_independent_of_external_decimal_precision(case,monkeypatch):
 from decimal import localcontext
 calls,results=spy_core(monkeypatch)
 with localcontext() as context:
  context.prec=2
  row=submit(case,lists([A+'; 1000.001'],[A+'; 999.999']))
 assert results[0][0].delta==Decimal('-0.002') and '-0.002' in row['text']


@pytest.mark.parametrize('mode',['unit','date','kind','duplicate'])
def test_actual_typed_core_boundary_mismatches_refuse_not_repaired(case,monkeypatch,mode):
 from dataclasses import replace
 actual=command.core.recount;calls=[]
 def recount(book,physical):
  calls.append((book,physical))
  if mode=='unit':physical=(replace(physical[0],unit='kg'),)+physical[1:]
  if mode=='date':physical=(replace(physical[0],observed_on=command.date(2026,10,20)),)+physical[1:]
  if mode=='kind':physical=(replace(physical[0],kind=command.core.StockKind.BOOK),)+physical[1:]
  if mode=='duplicate':book=book+(book[0],)
  return actual(book,physical)
 monkeypatch.setattr(command.core,'recount',recount)
 assert submit(case)['text']==command.NOTICE and len(calls)==1


def test_literal_template_no_date_unit_side_defaults_or_short_consent(case,monkeypatch):
 monkeypatch.setattr(command.core,'recount',lambda *a,**kw:pytest.fail('template core'))
 row=submit(case,command.HEADER);assert row['text']==command.TEMPLATE and command._units(row['text'])<3500
 assert submit(case,row['text'],event='unfilled')['text']==command.NOTICE
 for text in ('да','нет','согласен','пересчитай склад'):assert not command.is_command(text)


@pytest.mark.parametrize('label',command._LABELS)
def test_missing_each_static_field_no_observation_or_date_guess(case,monkeypatch,label):
 monkeypatch.setattr(command.core,'recount',lambda *a,**kw:pytest.fail('missing field core'))
 text='\n'.join(line for line in BLOCK.splitlines() if not line.startswith(label+': '))
 assert submit(case,text)['text']==command.NOTICE


@pytest.mark.parametrize('text',[
 lists([],[]),BLOCK.replace('Учётных строк: 2','Учётных строк: 0'),BLOCK.replace('Фактических строк: 2','Фактических строк: 3'),
 BLOCK.replace('Учёт 2:','Учёт 1:'),BLOCK.replace('Факт 2:','Факт 3:'),BLOCK.replace('Фактических строк: 2','Фактических строк: 6'),
 BLOCK+'\nСклад подтвержден: да',BLOCK+'\nЕдиница: kg',BLOCK+'\n',BLOCK.replace('Учётных строк: 2','Учётных строк: 02')])
def test_exact_side_counts_ordinals_unknown_fields_no_padding_truncation(case,text):
 assert submit(case,text)['text']==command.NOTICE


@pytest.mark.parametrize('record',[
 'неизвестно; base; L; B; 1','[SKU]; base; L; B; 1','A; base; L; B','A; base; L; B; 1; extra',
 'A; base; L; B; -1','A; base; L; B; NaN','A; base; L; B; Infinity','A; base; L; B; True','A; base; L; B; 1e2',
 'A; base; L; B; 1.123456789','A; base; L; B; '+'1'*25,'A; '+'v'*61+'; L; B; 1','A; base; bad\tL; B; 1'])
def test_closed_required_sku_dimensions_quantity_and_row_caps(case,record):
 assert submit(case,lists([record],[]))['text']==command.NOTICE


@pytest.mark.parametrize('updates',[
 {'Дата наблюдения':'сегодня'},{'Дата наблюдения':'2026-02-30'},{'Дата наблюдения':'2026-10-19T00:00:00Z'},
 {'Единица':'неизвестно'},{'Единица':'x'*41},{'Название сценария':'x'*81},{'Название сценария':'bad\rname'},{'Название сценария':'bad\x7fname'},
 {'Основание':'реальные проверенные остатки'}])
def test_explicit_date_unit_basis_controls(case,updates):
 assert submit(case,changed(**updates))['text']==command.NOTICE


@pytest.mark.parametrize('mode',['bytes','characters','output','type','count','duplicate','foreign_key','unit','date','source','book_value','delta_float','noncomparable_delta','unresolved_as_comparable'])
def test_whole_refusal_output_caps_and_actual_union_projection_invariants(case,monkeypatch,mode):
 from dataclasses import replace
 text=BLOCK
 if mode=='bytes':text=changed(**{'Название сценария':'😀'*2000})
 elif mode=='characters':text=changed(**{'Название сценария':'Ж'*4000})
 elif mode=='output':monkeypatch.setattr(command,'escape',lambda *a,**kw:'&'*4000)
 elif mode=='unresolved_as_comparable':
  text=lists(['A; неизвестно; L; B; 10'],['A; неизвестно; L; B; 8'])
  actual=command.core.recount
  monkeypatch.setattr(command.core,'recount',lambda *a,**kw:tuple(replace(line,state=command.core.RecountState.COMPARABLE,delta=Decimal('-2')) for line in actual(*a,**kw)))
 else:
  actual=command.core.recount
  def recount(*a,**kw):
   r=actual(*a,**kw);line=r[0]
   if mode=='type':return list(r)
   if mode=='count':return r[:1]
   if mode=='duplicate':return (line,line,r[2])
   if mode=='foreign_key':return (replace(line,key=command.core.StockKey(command.core.ItemKey('foreign','base'),'L','B')),)+r[1:]
   if mode=='unit':return (replace(line,unit='kg'),)+r[1:]
   if mode=='date':return (replace(line,observed_on=command.date(2026,10,20)),)+r[1:]
   if mode=='source':return (replace(line,book_source='foreign'),)+r[1:]
   if mode=='book_value':return (replace(line,book_units=Decimal('0')),)+r[1:]
   if mode=='delta_float':return (replace(line,delta=-2.0),)+r[1:]
   if mode=='noncomparable_delta':return (r[0],replace(r[1],delta=Decimal('0')),r[2])
  monkeypatch.setattr(command.core,'recount',recount)
 assert submit(case,text)['text']==command.NOTICE


def test_real_valid_long_escaped_union_refuses_whole_not_subset(case,monkeypatch):
 calls,results=spy_core(monkeypatch)
 rows=[str(i)+'<'*59+'; '+'<'*60+'; '+'<'*60+'; '+'<'*60+'; 1' for i in range(5)]
 row=submit(case,lists(rows,rows))
 assert len(calls)==1 and len(results[0])==5 and row['text']==command.NOTICE


def test_restart_replay_one_actual_recount_no_domain_admission(case,monkeypatch):
 calls,results=spy_core(monkeypatch);row=submit(case)
 case.e=Engine(case.s,case.c,None,lambda:case.clock[0])
 assert submit(case)['id']==row['id'] and len(calls)==len(results)==1
 assert not case.s.rows('SELECT * FROM sources') and not case.s.rows('SELECT * FROM tasks')
