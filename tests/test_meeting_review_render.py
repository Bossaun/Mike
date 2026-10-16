"""Boundary tests over synthetic typed inputs; no providers or persistence."""
from dataclasses import replace
from datetime import date
from html.parser import HTMLParser
import pytest
from goldway.meeting_review import (Source,Span,BoundValue,Agreement,Claim,Question,Meeting,ReviewError,review_meeting)
from goldway.meeting_review_render import render_meeting_review,PAGE_BUDGET
from goldway.presentation import meeting_messages

DAY=date(2026,10,16)
CONFIG={'employees':{'Alice':{'name':'WRONG INTERPRETATION'}}}

def case(owner=True,conflict=False,decision_conflict=False,tentative=False):
    s=Source('synthetic-source','SYNTHETIC',DAY,('<b>Решили</b> список','Alice','2026-10-20','Формат?','Bob','2026-10-21','Отменить'))
    def sp(n):return Span(s.id,n,n,s.lines[n-1])
    a=Agreement('A','decision','<b>Подготовить & список</b>',(sp(1),sp(2),sp(3)),not tentative,
                BoundValue('owner','Alice',sp(2),owner),BoundValue('deadline','2026-10-20',sp(3),True))
    claims=()
    if conflict:claims=(Claim('C','owner','Bob',(sp(5),),True),Claim('D','deadline','2026-10-21',(sp(6),),True))
    if decision_conflict:claims+=(Claim('E','decision','Отменить',(sp(7),),True),)
    return review_meeting(Meeting('M','SYNTHETIC',DAY,(s,),(a,),(Question('Q','Какой <i>формат</i>?',(sp(4),)),),claims))

def text(r):return '\n'.join(render_meeting_review(r,CONFIG))

def test_zero_tasks_keeps_all_issues():
    r=case(decision_conflict=True);assert not r.proposed_tasks
    out=text(r)
    for item in ('Предложенных задач: 0','Открытый вопрос','Противоречие','Отменить','Источник: synthetic-source · строки 7–7'):assert item in out
    assert 'Новых поручений не выделено' not in out


def test_source_identifiers_preserve_distinct_internal_spaces():
    sources = tuple(Source(identifier,'SYNTHETIC',DAY,('Вопрос?',))
                    for identifier in ('A  B','A B'))
    questions = tuple(Question(str(n),'Вопрос?',(Span(source.id,1,1,'Вопрос?'),))
                      for n,source in enumerate(sources))
    out=text(review_meeting(Meeting('M','SYNTHETIC',DAY,sources,(),questions)))
    assert 'Источник: A  B · строки 1–1' in out
    assert 'Источник: A B · строки 1–1' in out

def test_proposed_only_source_owner_not_config_lookup():
    out=text(case())
    for item in ('Задача черновик 1','👤 Alice','2026-10-20','Поручения не созданы','Ответственный по источнику (не назначение)'):assert item in out
    assert 'WRONG INTERPRETATION' not in out

def test_unknown_owner():
    out=text(case(owner=False))
    assert 'Ответственный: неизвестен — нужно уточнить.' in out and '👤 Alice' not in out

def test_conflicting_fields_unknown_never_assigned():
    out=text(case(conflict=True))
    for item in ('Ответственный: неизвестен — противоречие','Срок: неизвестен — противоречие','Противоречащий вариант: Alice','Противоречащий вариант: Bob','Противоречащий вариант: 2026-10-21'):assert item in out
    assert '👤 Alice' not in out and '📅' not in out

def test_tentative_stays_tentative():
    out=text(case(tentative=True))
    assert 'решение не подтверждено' in out and 'Задача черновик' not in out
    assert 'Источник: synthetic-source · строки 1–1' in out

@pytest.mark.parametrize('confirmed',[False,True])
def test_unsettled_question_answer(confirmed):
    r=case();m=r.meeting;s=m.sources[0]
    bound=BoundValue('format','CSV',Span(s.id,5,5,s.lines[4]),confirmed)
    q=replace(m.questions[0],resolution=bound)
    claims=(Claim('F','format','JSON',(Span(s.id,6,6,s.lines[5]),),True),) if confirmed else ()
    out=text(review_meeting(replace(m,questions=(q,),claims=claims)))
    assert 'Открытый вопрос' in out and 'Вариант ответа требует уточнения: CSV' in out

def test_escaped_source_and_title():
    out=text(case())
    for item in ('&lt;b&gt;Подготовить &amp; список&lt;/b&gt;','&lt;i&gt;формат&lt;/i&gt;','Цитата (фрагмент): «&lt;b&gt;Решили&lt;/b&gt;'):assert item in out
    assert '<i>' not in out and '<b>Решили</b>' not in out

class Tags(HTMLParser):
    def __init__(self):super().__init__();self.stack=[]
    def handle_starttag(self,tag,attrs):assert tag=='b' and not attrs;self.stack.append(tag)
    def handle_endtag(self,tag):assert self.stack.pop()==tag

def test_worst_case_cardinality_html_and_utf16_pages():
    s=Source('<&😀'*25,'B',DAY,('😀<&'*100,));sp=Span(s.id,1,1,s.lines[0])
    aa=tuple(Agreement(str(i),'fact'+str(i),'<&'*150,(sp,),True,BoundValue('owner'+str(i),'<&'*150,sp,True)) for i in range(100))
    pages=render_meeting_review(review_meeting(Meeting('M','B',DAY,(s,),aa)),CONFIG)
    assert len(pages)>100
    for page in pages:
        assert len(page.encode('utf-16-le'))//2<=PAGE_BUDGET
        assert 'Черновик для проверки' in page
        parser=Tags();parser.feed(page);parser.close();assert not parser.stack
    out='\n'.join(pages)
    assert out.count('Задача черновик ')==100 and out.count('Источник: ')==100
    assert 'Задача черновик 100' in out

@pytest.mark.parametrize('mutation',[
    lambda r:{},lambda r:replace(r,proposed_tasks=[]),lambda r:replace(r,proposed_tasks=()),
    lambda r:replace(r,unresolved_questions=()),
    lambda r:replace(r,proposed_tasks=(replace(r.proposed_tasks[0],status='confirmed'),)),
    lambda r:replace(r,proposed_tasks=(replace(r.proposed_tasks[0],owner='Bob'),)),
    lambda r:replace(r,proposed_tasks=({'title':'raw'},)),lambda r:replace(r,contradictions=({},)),
])
def test_raw_or_noncanonical_rejected(mutation):
    with pytest.raises(ReviewError):render_meeting_review(mutation(case()),CONFIG)

def test_omitted_conflict_rejected():
    r=case(conflict=True)
    with pytest.raises(ReviewError):render_meeting_review(replace(r,contradictions=()),CONFIG)

def test_explicit_bridge_and_mixed_legacy_fail_closed():
    r=case();assert meeting_messages([],CONFIG,review=r)==render_meeting_review(r,CONFIG)
    with pytest.raises(ValueError,match='structured_review_requires_no_legacy_tasks'):meeting_messages([{'title':'other'}],CONFIG,review=r)

def test_legacy_no_review_exact_empty_and_card():
    assert meeting_messages([],CONFIG)==['<b>📋 GOLDWAY · итог планёрки</b>\nНовых поручений не выделено.']
    task={'id':'legacy','status':'proposed','title':'Title','owner':None,'deadline':None}
    assert meeting_messages([task],CONFIG)==['<b>📋 GOLDWAY · итог планёрки</b>\nПунктов: 1 · Без ответственного: 1\nТочные сроки не названы.\n\nЧерновики — требуется подтверждение руководителя.\n\n<b>Задача legacy · Черновик</b>\n<b>Title</b>\n👤 Ответственного нужно уточнить\n\n<b>Как уточнить</b>\nНапишите обычным текстом, кто отвечает и когда будет результат. Назовите задачу или ответьте на её карточку; номер можно использовать по желанию.']

def test_empty_structured_still_preview():
    r=review_meeting(Meeting('M','B',DAY,(Source('S','B',DAY,('Планёрка',)),),()))
    out=text(r);assert 'Предложенных задач: 0' in out and 'Открытых вопросов: 0' in out and 'Поручения не созданы' in out

def test_continuation_fragments_keep_subject_attribution():
    r=case(conflict=True)
    out=text(r)
    assert 'Черновик 1\nИсточник: synthetic-source' in out
    assert 'Вопрос Q\nИсточник: synthetic-source' in out
    assert 'Противоречие · поле: owner\nИсточник: synthetic-source' in out
    assert 'Поле: owner\nПротиворечащий вариант: Bob' in out
    assert 'Черновик 1 · Срок: неизвестен' in out
    # Max expansion of a field/key still leaves complete individually bounded blocks.
    s=Source('<&'*64,'B',DAY,('<&'*150,));sp=Span(s.id,1,1,s.lines[0])
    a=Agreement('A','<&'*64,'<&'*150,(sp,),True,BoundValue('o','<&'*150,sp,True))
    c=Claim('C','<&'*64,'B',(sp,),True)
    q=Question('<&'*64,'<&'*150,(sp,))
    pages=render_meeting_review(review_meeting(Meeting('M','B',DAY,(s,),(a,),(q,),(c,))),CONFIG)
    for page in pages:
        assert len(page.encode('utf-16-le'))//2<=PAGE_BUDGET
        parser=Tags();parser.feed(page);parser.close();assert not parser.stack
