"""Fictional offline plumbing checks, not evidence of a real LLM's quality."""
import copy
import json
from pathlib import Path

import pytest

from goldway.engine import Engine
from goldway.model import ScriptedModel
from goldway.store import Store
from goldway.telegram import Dispatcher, Updates
from test_operator_tasks import message
from test_personal_intake import profile
from test_team_tasks import team
from test_telegram import API


PROMPTS = (
    "Что такое GOLDWAY?",
    "Какие бренды мы продаём?",
    "Давай",
    "Расскажи подробнее о втором бренде",
    "А какие товары у него есть?",
    "Сделай короткое описание для клиента",
    "Кто отвечает за это направление?",
    "Не знаю, уточни",
    "Поставь задачу проверить ассортимент к пятнице",
    "Нет, я просто спрашивал, задачу создавать не надо",
    "Что ты знаешь о наших внутренних финансах?",
    "Откуда ты это знаешь?",
)


def snapshot(topic="", entities=(), offer=None, task=None):
    return {"topic": topic, "entities": list(entities), "pending_offer": offer, "task_id": task}


def answer(text, *, state=None, refs=(), mode="not_needed", actions=None, intent="conversation"):
    return {"actions": actions or [], "intent": intent, "summary": "Internal event summary: never a direct answer",
            "reply_text": text, "dialogue_state": state or snapshot(), "evidence_ids": list(refs), "knowledge_mode": mode}


def seed_documents(engine, now):
    """Every name, product and financial amount here is explicitly fictional."""
    shared = [engine.intelligence.reader(x) for x in engine.c["employees"]]
    documents = (
        ("company", "Вымышленная справка GOLDWAY", "GOLDWAY — вымышленный учебный магазин. GOLDWAY продаёт два вымышленных бренда: Маяк и Лира.", shared),
        ("mayak", "Вымышленный бренд Маяк", "Маяк — вымышленный первый бренд. Маяк выпускает учебное мыло М-1.", shared),
        ("lira", "Вымышленный бренд Лира", "Лира — вымышленный второй бренд. Ассортимент Лиры: учебная сыворотка Л-1 и учебный крем Л-2. Ответственный за направление Лира в этом источнике не указан.", shared),
        ("finance", "Вымышленные внутренние финансы", "Внутренние финансы — полностью вымышленный учебный пример. Секретный резерв составляет 17 учебных единиц.", [engine.intelligence.reader("test_operator")]),
    )
    for identifier, title, text, readers in documents:
        engine.intelligence.index.upsert({
            "source_id": "fictional-docs", "document_id": identifier, "title": title,
            "url": "https://example.invalid/fictional/" + identifier, "text": text,
            "readers": readers, "observed_at": now - 10, "permission_expires_at": now + 3600,
            "updated_at": "2026-10-14", "confirmed": True,
        })


@pytest.fixture
def enabled_personal(tmp_path):
    config = json.loads((Path(__file__).parents[1] / "examples/config.json").read_text())
    clock = [1791979200.0]
    profile(config, clock)
    config.update(live_send=True, recipients_verified=True,
                  intelligence={"enabled": True, "sources": []})
    store = Store(str(tmp_path / "personal.sqlite"))
    engine = Engine(store, config, ScriptedModel({}), lambda: clock[0])
    seed_documents(engine, clock[0])
    return engine, store, config, clock, API()


def invoke(case, text, identifier, model, *, flush=True):
    engine, store, config, clock, api = case
    engine.model = model
    Updates(engine, api, 999).handle(message(clock, text, identifier))
    engine.process("update:" + str(identifier))
    if flush:
        Dispatcher(store, config, api, lambda: clock[0]).flush()
    clock[0] += 1
    return store.rows("SELECT * FROM outbox WHERE business=? ORDER BY id DESC LIMIT 1", (engine.b,))[0]


class TwelvePromptFixture:
    """Inspect real enriched context; responses remain deterministic test data."""
    def __init__(self):
        self.contexts = []

    def propose(self, context):
        context = copy.deepcopy(context)
        self.contexts.append(context)
        index = len(self.contexts) - 1
        assert context["text"].endswith(PROMPTS[index])
        dialogue = context["dialogue"]
        evidence = context["knowledge"]["evidence"]
        assert context.get("recent_summaries") == []
        if index:
            assert dialogue["turns"], "Only an actually sent prior answer may supply continuation context"
            assert dialogue["turns"][-1]["role"] == "assistant"
            assert "Internal event summary" not in json.dumps(dialogue, ensure_ascii=False)
        def refs(document):
            selected = [e["id"] for e in evidence if e["document_id"] == document]
            assert selected, f"Missing current readable source for {document}"
            return selected[:1]
        if index == 0:
            assert dialogue["turns"] == []
            return answer("GOLDWAY — вымышленный учебный магазин.", state=snapshot("GOLDWAY", ["GOLDWAY"]), refs=refs("company"), mode="grounded")
        if index == 1:
            assert "учебный магазин" in dialogue["turns"][-1]["content"]
            return answer("В учебном примере продаются Маяк и Лира. Рассказать о них подробнее?", state=snapshot("Бренды Маяк и Лира", ["Маяк", "Лира"], {"kind": "explain", "text": "Рассказать о вымышленных брендах Маяк и Лира"}), refs=refs("company"), mode="grounded")
        if index == 2:
            assert dialogue["pending_offer"]["kind"] == "explain"
            assert dialogue["entities"] == ["Маяк", "Лира"]
            return answer("Маяк — первый учебный бренд, Лира — второй.", state=snapshot("Бренды Маяк и Лира", ["Маяк", "Лира"]), refs=refs("mayak") + refs("lira"), mode="grounded")
        if index == 3:
            assert dialogue["entities"][1] == "Лира"
            return answer("Второй бренд — Лира. В учебном ассортименте есть сыворотка Л-1 и крем Л-2.", state=snapshot("Лира", ["Лира"]), refs=refs("lira"), mode="grounded")
        if index == 4:
            assert dialogue["entities"] == ["Лира"]
            assert "Второй бренд — Лира" in dialogue["turns"][-1]["content"]
            return answer("У Лиры в вымышленном каталоге: сыворотка Л-1 и крем Л-2.", state=snapshot("Лира", ["Лира"]), refs=refs("lira"), mode="grounded")
        if index == 5:
            assert dialogue["entities"] == ["Лира"]
            return answer("Лира: учебная сыворотка Л-1 и крем Л-2. Это описание вымышленного примера, а не проверенная реклама.", state=snapshot("Лира", ["Лира"]), refs=refs("lira"), mode="grounded")
        if index == 6:
            assert dialogue["topic"] == "Лира"
            return answer("Ответственный в подключённой учебной справке не указан. Кто может подтвердить эту роль?", state=snapshot("Лира", ["Лира"], {"kind": "clarify", "text": "Уточнить ответственного за направление Лира"}), mode="not_found")
        if index == 7:
            assert dialogue["pending_offer"]["kind"] == "clarify"
            return answer("В источнике этого нет. Скажите, у кого можно уточнить; назначать ответственного наугад не буду.", state=snapshot("Лира", ["Лира"]), mode="not_found")
        if index == 8:
            action = {"op": "create", "title": "Проверить ассортимент Лиры", "owner": None,
                      "owner_lines": [], "deadline": None, "deadline_lines": [1],
                      "next_step": "Сверить учебный ассортимент", "criterion": "Учебный ассортимент проверен",
                      "status": "proposed", "evidence_lines": [1], "set_fields": []}
            return answer("Создам только черновик; уточните дату пятницы и ответственного.", state=snapshot("Лира", ["Лира"]), actions=[action], intent="task_action")
        if index == 9:
            assert dialogue["task_id"] in {task["id"] for task in context["tasks"]}
            assert "Черновик" in dialogue["turns"][-1]["content"]
            return answer("Понял, новых задач не создаю. Уже показанный черновик не подтверждён.", state=snapshot("Лира", ["Лира"], task=dialogue["task_id"]))
        if index == 10:
            return answer("Только из вымышленного учебного источника: секретный резерв — 17 учебных единиц.", state=snapshot("Внутренние финансы", ["Внутренние финансы"]), refs=refs("finance"), mode="grounded")
        assert index == 11
        assert dialogue["topic"] == "Внутренние финансы"
        assert "https://example.invalid/fictional/finance" in dialogue["turns"][-1]["content"]
        return answer("Из подключённой вымышленной финансовой справки; это учебные данные.", state=snapshot("Внутренние финансы", ["Внутренние финансы"]), refs=refs("finance"), mode="grounded")


def test_twelve_requested_prompts_retrieve_current_evidence_and_actual_answers(enabled_personal):
    model = TwelvePromptFixture()
    engine, store, *_ = enabled_personal
    for index, prompt in enumerate(PROMPTS, 1):
        row = invoke(enabled_personal, prompt, index, model)
        assert row["state"] == "sent" and row["transport"] == "bot"
        assert row["recipient"] == "test_operator"
        assert "Internal event summary" not in row["text"]
        assert len(engine.tasks()) == (0 if index < 9 else 1)
    assert len(model.contexts) == 12
    task = engine.tasks()[0]
    assert task["status"] == "proposed" and task["owner"] is None and task["deadline"] is None
    assert store.execute("SELECT COUNT(*) FROM events WHERE state='done'").fetchone()[0] == 12
    assert all(row["recipient"] == "test_operator" for row in store.rows("SELECT recipient FROM outbox"))
    final = store.rows("SELECT text FROM outbox ORDER BY id DESC LIMIT 1")[0]["text"]
    assert "https://example.invalid/fictional/finance" in final and "Источники:" in final


def test_sent_answer_is_required_before_short_continuation_has_memory(enabled_personal):
    case = enabled_personal
    initial = answer("Предлагаю объяснить Лиру.", state=snapshot("Лира", ["Лира"], {"kind": "explain", "text": "Объяснить учебный бренд Лира"}))
    invoke(case, "Расскажи о Лире", 21, ScriptedModel(initial), flush=False)
    class CheckPending:
        def propose(self, context):
            assert context["dialogue"]["turns"] == []
            assert context["dialogue"]["pending_offer"] is None
            return answer("Уточните, что именно объяснить.")
    invoke(case, "Давай", 22, CheckPending())
    assert not case[0].tasks()


def test_explanation_acceptance_cannot_be_misclassified_as_task_consent(enabled_personal):
    invoke(enabled_personal, "Расскажи о Лире", 31, ScriptedModel(answer("Рассказать подробнее?", state=snapshot("Лира", ["Лира"], {"kind": "explain", "text": "Объяснить Лиру"}))))
    bad_action = {"op": "create", "title": "Придуманная задача", "owner": None, "deadline": None,
                  "next_step": "Ничего", "criterion": "Ничего", "status": "proposed", "evidence_lines": [1]}
    with pytest.raises(ValueError, match="not task consent"):
        invoke(enabled_personal, "Давай", 32, ScriptedModel(answer("Создам задачу.", actions=[bad_action], intent="task_action")))
    assert not enabled_personal[0].tasks()


def test_negated_creation_rejects_mutation_even_if_offline_model_is_wrong(enabled_personal):
    bad_action = {"op": "create", "title": "Проверить ассортимент", "owner": None, "deadline": None,
                  "next_step": "Сверить", "criterion": "Сверено", "status": "proposed", "evidence_lines": [1]}
    with pytest.raises(ValueError, match="declined task creation"):
        invoke(enabled_personal, PROMPTS[9], 41, ScriptedModel(answer("Создам.", actions=[bad_action], intent="task_action")))
    assert not enabled_personal[0].tasks()


def test_finance_acl_is_enforced_before_employee_model_context(team):
    old, store, config, clock, api = team
    config["intelligence"] = {"enabled": True, "sources": []}
    engine = Engine(store, config, ScriptedModel({}), lambda: clock[0])
    seed_documents(engine, clock[0])
    class EmployeeQuestion:
        def propose(self, context):
            serialized = json.dumps(context, ensure_ascii=False)
            assert "17 учебных единиц" not in serialized
            assert all(item["document_id"] != "finance" for item in context["knowledge"]["evidence"])
            assert context["dialogue"]["turns"] == []
            return answer("У меня нет доступного вам подтверждённого финансового источника.", mode="not_found")
    engine.model = EmployeeQuestion()
    uid = config["employees"]["alen"]["telegram_user_id"]
    engine.reply("finance-employee", "alen", PROMPTS[10], source_id=f"telegram:{uid}:1", channel="bot")
    engine.process("finance-employee")
    assert not engine.tasks()
    assert store.rows("SELECT recipient FROM outbox") == [{"recipient": "alen"}]


@pytest.mark.parametrize("source,channel", [("telegram:78:51", "bot"), ("business:new-connection:77:51", "business")])
def test_context_does_not_merge_same_actor_other_chat_or_channel(enabled_personal, source, channel):
    invoke(enabled_personal, "Расскажи о Лире", 50, ScriptedModel(answer("Предыдущий личный ответ.", state=snapshot("Лира", ["Лира"]))))
    engine, _, config, _, _ = enabled_personal
    if channel == "business":
        # Scope isolation can be checked without enabling a Business action API.
        config.pop("personal_intake")
    class OtherScope:
        def propose(self, context):
            assert context["dialogue"]["turns"] == []
            assert context["dialogue"]["topic"] == ""
            return answer("Уточните новый вопрос.")
    engine.model = OtherScope()
    engine.reply("other-scope", "test_operator", "Подробнее о новом вопросе", source_id=source, channel=channel)
    engine.process("other-scope")
