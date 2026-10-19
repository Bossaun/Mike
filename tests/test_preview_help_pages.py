"""I56 bounded page parser and actual private Engine boundary."""
import pytest
from test_preview_help_command import case, reply
import goldway.preview_help_command as command
ORIGINAL_WORKFLOW_HELP=('Ещё доступны команды:', '• «Сверь бланк по сценарию» — условная сверка.', '• «Сравни недели продаж» — сравнение переданных строк двух недель с покрытием себестоимости.', '• «Проверь сценарий лимита бартера» — сравнение условных затрат одного участника с введённым лимитом.', '• «Рассчитай сценарий окупаемости бартера» — расчёт по условным суммам, возвратам и полноте сведений.', '• «Проверь срок отчёта по отгрузке» — условная проверка правила 168 часов по введённым наблюдениям.', '• «Проверь измеримость бартера» — условная проверка связи отправки, промокода и отметки результата.', '• «Сверь касание по сценарию» — условная сверка плана и отметок квитанций.', '• «Проверь сценарий касания» — проверка условного плана контакта; это не разрешение на отправку.', '• «Проверь обещание по сценарию» — ручная проверка условного срока и результата; поручения и уведомления не создаются.', '• «Проверь сценарий ожидания ответа» — ручная проверка условных событий диалога; переписка не загружается.', '• «Подведи итоги акции» — черновик за семь суток по введённым счётчикам; неизвестные значения сохраняются.', '• «Проверь перерыв в публикациях» — ручная проверка введённых наблюдений; мониторинг аккаунтов и напоминания не включаются.', '• «Подготовь ответ о товаре» — шаблон черновика по введённому сценарию; наличие, цена и доставка не проверяются.', '• «Что мешает моим задачам?» — собственные задачи с отметкой препятствия и записанный следующий шаг.', '• «Покажи историю регулярной задачи №N» — сохранённые экземпляры и их текущие статусы; это не подтверждение фактического выполнения.', '• «Покажи мои справки» — названия и коды доступных личных справок; по восемь на странице.', '• «Сохрани справку для меня» — шаблон личной непроверенной справки. Ссылки не открываю и достоверность текста не проверяю.', '• После разбора новой планёрки со связанными черновиками вы получите шаблон «Регистрация планёрки MB-КОД». Заполните именно выданный шаблон: дата, строки решений, номера черновиков, личный доступ и срок. Регистрация связей не согласовывает задачи.', '• «Отчёт по планёрке MB-КОД» — шаблон вашего датированного отчёта по одной связанной задаче. Это ваше утверждение, не независимая проверка; статус задачи не меняется.', '• «Сверь планёрку MB-КОД за ГГГГ-ММ-ДД — ГГГГ-ММ-ДД» — разбор за семь дней по зарегистрированным связям и вашим датированным отчётам. Без отчёта результат остаётся неизвестным; противоречия отмечаются. Только по вашему запросу.', 'Замените MB-КОД на выданный код регистрации, N — на номер задачи, обе даты — на нужные даты. Для недельного разбора укажите семь календарных дней, включая первую и последнюю даты.')


def test_default_preserves_every_workflow_and_six_descriptors():
    text=command.build_preview_help()
    assert text==command.build_preview_help(1)
    assert command.MAX_DESCRIPTORS==6 and len(command.DESCRIPTORS)==6
    assert command._WORKFLOW_HELP==ORIGINAL_WORKFLOW_HELP
    for line in ORIGINAL_WORKFLOW_HELP:assert line in text
    for item in command.DESCRIPTORS:
        assert item.data_scope=='supplied_values_only' and text.count(item.request)==1
    assert text.endswith('Ещё команды: «Что умеешь 2».')

@pytest.mark.parametrize('page',[1,2])
def test_pages_bounded_honest_and_navigable(page):
    text=command.build_preview_help(page)
    assert len(text.encode('utf-16-le'))//2<=3500
    assert 'Что умеешь '+str(3-page) in text
    if page==2:
        for phrase in ('Проверь дневные показатели по сценарию','не оценка реальных сотрудников',
            'проверки и уведомления не включаются','независимо не проверяются'):assert phrase in text

@pytest.mark.parametrize('page',[None,True,False,0,3,-1,1.0,'2',(),[]])
def test_invalid_page_rejected(page):
    with pytest.raises(command.PreviewHelpUnavailable):command.build_preview_help(page)

@pytest.mark.parametrize('text,page',[('Что умеешь?',1),('Что ты умеешь!',1),('Что умеешь 1',1),
    ('Что ты умеешь 1?',1),('Что умеешь 2',2),('ЧТО ТЫ УМЕЕШЬ 2!!!',2),('  что  умеешь\t2 ? ',2)])
def test_normalized_exact_page_requests(text,page):
    assert command.is_preview_help_command(text) and command._request_page(text)==page

@pytest.mark.parametrize('text',['Что умеешь 0','Что умеешь 3','Что умеешь 02','Что умеешь +2',
    'Что умеешь 2.0','Что умеешь 2 завтра','Что умеешь 2 1','не что умеешь 2',
    'Что умеешь\n2\nРассчитай закупку',None,True,'x'*129])
def test_invalid_requests_not_help(text):
    assert not command.is_preview_help_command(text) and command._request_page(text) is None

@pytest.mark.parametrize('channel',['bot','business'])
@pytest.mark.parametrize('text,page',[('Что умеешь 1',1),('Что ты умеешь 2?',2)])
def test_actual_engine_queue_transport_dedupe_no_offer(case,channel,text,page):
    added,out=reply(case,text,channel=channel,event='page')
    assert added and out['text']==command.build_preview_help(page)
    assert out['recipient']=='test_operator' and out['task_id'] is None
    assert out['preferred_transport']==channel and out['parse_mode'] is None
    assert case[1].rows('SELECT state FROM events')[0]['state']=='done'
    assert case[1].rows('SELECT phase FROM answer_citation_turns')[0]['phase']=='pending'
    for table in ('runtime','tasks','sources'):assert not case[1].rows('SELECT * FROM '+table)
    assert reply(case,text,channel=channel,event='page')[0] is False
    assert len(case[1].rows('SELECT * FROM outbox'))==1
    assert len(case[1].rows('SELECT * FROM answer_citation_turns'))==1

@pytest.mark.parametrize('gate',['staff','group','nonclosed','missing_intelligence'])
def test_page_two_initial_gate_falls_through(case,gate):
    e,s,_=case;sender='test_operator';channel='bot';source='telegram:1:20'
    if gate=='staff':sender='frost';source='telegram:2:20'
    elif gate=='group':channel='group';source='telegram:-20:20'
    elif gate=='nonclosed':e.c.pop('team_intake')
    else:e.intelligence=None
    fallback=[]
    e._context=lambda *a,**k: (fallback.append('context') or {'tasks':[]})
    added,out=reply(case,'Что умеешь 2',sender=sender,channel=channel,source=source)
    assert added and out is None and not s.rows('SELECT * FROM outbox')
    assert s.rows('SELECT state FROM events')[0]['state']=='pending'
    if gate in ('staff','missing_intelligence'):assert fallback==['context']

@pytest.mark.parametrize('drift',['admin','intelligence'])
def test_page_two_postqueue_drift_rolls_back(case,monkeypatch,drift):
    e,s,_=case;queue=s.queue
    def changed(*a,**k):
        queue(*a,**k)
        if drift=='admin':e.c['admins']=[]
        else:e.intelligence=None
    monkeypatch.setattr(s,'queue',changed)
    with pytest.raises(ValueError):reply(case,'Что умеешь 2')
    for table in ('events','outbox','answer_citation_turns'):assert not s.rows('SELECT * FROM '+table)

@pytest.mark.parametrize('page',[1,2])
def test_whole_page_budget_refusal_rollback(case,monkeypatch,page):
    monkeypatch.setattr(command,'MAX_OUTPUT_UNITS',10)
    with pytest.raises(command.PreviewHelpUnavailable):reply(case,'Что умеешь '+str(page))
    for table in ('events','outbox'):assert not case[1].rows('SELECT * FROM '+table)
