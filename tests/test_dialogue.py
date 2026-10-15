import json

import pytest

from goldway.dialogue import DialogueMemory, scope_from_payload
from goldway.store import Store


def scope(sender="operator", chat="101", channel="bot", business="GOLDWAY", connection="conn", thread=None):
    source = f"business:{connection}:{chat}:1" if channel == "business" else f"telegram:{chat}:1"
    payload = {"sender": sender, "channel": channel, "source_id": source}
    if thread is not None:
        payload["message_thread_id"] = thread
    return scope_from_payload(payload, business)


@pytest.fixture
def memory(tmp_path):
    store = Store(str(tmp_path / "memory.sqlite"))
    return DialogueMemory(store), store


def outgoing(store, *, business="GOLDWAY", sender="operator", state="sent", channel="bot", chat="101", connection=None, thread=None):
    number = store.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] + 1
    store.queue(business, sender, "Actual answer", f"test:{number}", 10)
    identifier = store.execute("SELECT id FROM outbox WHERE dedupe=?", (f"test:{number}",)).fetchone()[0]
    store.execute("UPDATE outbox SET state=?,transport=?,destination_chat=?,destination_connection=?,destination_thread=? WHERE id=?",
                  (state, channel, chat, connection, thread, identifier))
    return identifier


def test_scope_uses_chat_sender_business_channel_connection_and_thread():
    candidates = {
        scope(), scope(sender="employee"), scope(chat="102"), scope(business="OTHER"),
        scope(channel="business"), scope(channel="business", connection="new"),
        scope(chat="-100", channel="group", thread=22),
        scope(chat="-100", channel="group", thread=23),
    }
    assert None not in candidates and len(candidates) == 8
    assert scope(chat="-100", channel="group", thread=1) == scope(chat="-100", channel="group")


def test_opaque_business_connection_can_contain_colons():
    assert scope(channel="business", connection="opaque:a:b") is not None
    assert scope(channel="business", connection="opaque:a:b") != scope(channel="business", connection="b")


@pytest.mark.parametrize("payload", [
    {}, {"sender": "operator", "channel": "bot"},
    {"sender": "operator", "channel": "bot", "source_id": "event:1"},
    {"sender": "operator", "channel": "bot", "source_id": "telegram:101:0"},
    {"sender": "operator", "channel": "bot", "source_id": "telegram:001:1"},
    {"sender": "operator", "channel": "bot", "source_id": "telegram:0:1"},
    {"sender": "operator", "channel": "bot", "source_id": "telegram:-100:1"},
    {"sender": "operator", "channel": "group", "source_id": "telegram:101:1"},
    {"sender": "operator", "channel": "bot", "source_id": "business:conn:101:1"},
    {"sender": "operator", "channel": "business", "source_id": "telegram:101:1"},
    {"sender": "operator", "channel": "business", "source_id": "business::101:1"},
    {"sender": None, "channel": "bot", "source_id": "telegram:101:1"},
    {"sender": "operator", "channel": "bot", "source_id": "telegram:101:1", "message_thread_id": 22},
    {"sender": "operator", "channel": "group", "source_id": "telegram:-100:1", "message_thread_id": True},
    {"sender": "operator", "channel": "group", "source_id": "telegram:-100:1", "message_thread_id": 22, "thread_id": 23},
])
def test_no_actor_only_scope_or_malformed_source(payload):
    payload = dict(payload, dialogue_scope=scope())
    assert scope_from_payload(payload, "GOLDWAY") is None


def test_private_dialogues_and_topics_never_cross_scopes(memory):
    mem, _ = memory
    for index, current in enumerate((scope(), scope(sender="employee"), scope(channel="business"), scope(business="OTHER"))):
        mem.record(current, "same-event", f"Question {index}", f"Answer {index}", state={"topic": f"Topic {index}"}, at=10)
    for index, current in enumerate((scope(), scope(sender="employee"), scope(channel="business"), scope(business="OTHER"))):
        result = mem.get(current, readable_ids=set(), now=20)
        assert result["topic"] == f"Topic {index}"
        assert result["turns"][-1]["content"] == f"Answer {index}"


def test_restart_reuses_only_recorded_current_turns(tmp_path):
    path = str(tmp_path / "state.sqlite")
    first = Store(path)
    first.audit("GOLDWAY", "old_chat", {"text": "Historical private text"}, 1)
    DialogueMemory(first).record(scope(), "one", "Actual question", "Actual direct answer", state={"topic": "Packing"}, at=10)
    first.db.close()
    reopened = Store(path)
    result = DialogueMemory(reopened).get(scope(), readable_ids=set(), now=20)
    assert result["turns"] == [{"role": "user", "content": "Actual question"}, {"role": "assistant", "content": "Actual direct answer"}]
    assert result["topic"] == "Packing"
    assert "Historical private text" not in json.dumps(result)
    reopened.db.close()


def test_role_bounds_order_and_idempotence_survive_pruning(memory):
    _, store = memory
    mem = DialogueMemory(store, max_turns=4)
    for index in (1, 3, 2, 4):
        assert mem.record(scope(), f"e{index}", f"Q{index}", f"A{index}", at=index)
    assert not mem.record(scope(), "e1", "Repeated old question", "Duplicate", at=100)
    result = mem.get(scope(), readable_ids=set(), now=100)
    assert [m["content"] for m in result["turns"]] == ["Q3", "A3", "Q4", "A4"]
    assert len(result["turns"]) == 4


def test_state_checkpoint_survives_generic_turns_and_history_pruning(memory):
    _, store = memory
    mem = DialogueMemory(store, max_turns=2)
    mem.record(scope(), "topic", "Explain packaging", "Packaging answer", state={"topic": "Packaging", "pending_offer": {"kind": "draft", "text": "Draft a checklist"}}, evidence_ids=("manual",), at=1)
    for index in (2, 3, 4):
        mem.record(scope(), str(index), "Generic follow-up", "Direct answer", at=index)
    assert mem.get(scope(), readable_ids={"manual"}, now=5)["topic"] == "Packaging"
    revoked = mem.get(scope(), readable_ids=set(), now=5)
    assert revoked["topic"] == "" and revoked["pending_offer"] is None
    assert len(revoked["turns"]) == 2


def test_entire_knowledge_turn_and_state_are_removed_on_any_revocation(memory):
    mem, _ = memory
    mem.record(scope(), "known", "Question", "Knowledge-bearing answer", state={"topic": "Private topic", "entities": ["private entity"], "task_id": "task-secret"}, evidence_ids=("a", "b"), at=10)
    assert len(mem.get(scope(), readable_ids={"a", "b"}, now=20)["turns"]) == 2
    revoked = mem.get(scope(), readable_ids={"a"}, now=20)
    assert revoked == {"turns": [], "topic": "", "entities": [], "pending_offer": None, "task_id": None}
    assert mem.get(scope(), readable_ids={"a", "b"}, now=20)["topic"] == "Private topic"


@pytest.mark.parametrize("delivery_state", ["pending", "retry", "unknown", "sending", "cancelled", "failed", "blocked"])
def test_nonconfirmed_delivery_preserves_prior_topic(memory, delivery_state):
    mem, store = memory
    mem.record(scope(), "old", "Old question", "Old delivered answer", state={"topic": "Confirmed"}, at=1)
    identifier = outgoing(store, state=delivery_state)
    mem.record(scope(), "new", "New question", "Undelivered answer", state={"topic": "Unconfirmed"}, at=2, outbox_ids=(identifier,))
    result = mem.get(scope(), readable_ids=set(), now=20)
    assert result["topic"] == "Confirmed"
    assert "Undelivered answer" not in json.dumps(result)


def test_pending_turn_promotes_only_after_all_messages_are_sent(memory):
    mem, store = memory
    first, second = outgoing(store), outgoing(store, state="pending")
    mem.record(scope(), "event", "Question", "Delivered answer", state={"topic": "New topic"}, at=10, outbox_ids=(first, second))
    assert mem.get(scope(), readable_ids=set(), now=20)["turns"] == []
    store.execute("UPDATE outbox SET state='sent' WHERE id=?", (second,))
    result = mem.get(scope(), readable_ids=set(), now=20)
    assert result["topic"] == "New topic" and len(result["turns"]) == 2
    assert not mem.record(scope(), "event", "Question", "Duplicate", at=30, outbox_ids=(first, second))
    assert len(mem.get(scope(), readable_ids=set(), now=40)["turns"]) == 2


@pytest.mark.parametrize("change", [
    {"business": "OTHER"}, {"sender": "employee"}, {"chat": "102"},
    {"channel": "business", "connection": "conn"}, {"connection": "wrong"}, {"thread": 22},
])
def test_sent_delivery_must_match_actual_scope_route(memory, change):
    mem, store = memory
    identifier = outgoing(store, **change)
    mem.record(scope(), "event", "Question", "Wrong-route answer", state={"topic": "Wrong"}, at=10, outbox_ids=(identifier,))
    assert mem.get(scope(), readable_ids=set(), now=20)["turns"] == []


def test_business_fallback_does_not_overwrite_business_topic(memory):
    mem, store = memory
    current = scope(channel="business")
    mem.record(current, "old", "Business question", "Business answer", state={"topic": "Confirmed business topic"}, at=1)
    identifier = outgoing(store)  # Actual fallback was delivered by ordinary bot.
    mem.record(current, "new", "Follow-up", "Bot fallback answer", state={"topic": "Wrong channel topic"}, at=2, outbox_ids=(identifier,))
    result = mem.get(current, readable_ids=set(), now=20)
    assert result["topic"] == "Confirmed business topic"
    assert len(result["turns"]) == 2


def test_group_delivery_requires_the_same_topic(memory):
    mem, store = memory
    current = scope(channel="group", chat="-100", thread=22)
    identifier = outgoing(store, channel="group", chat="-100", thread=23)
    mem.record(current, "event", "Topic question", "Answer in another topic", at=10, outbox_ids=(identifier,))
    assert mem.get(current, readable_ids=set(), now=20)["turns"] == []
    store.execute("UPDATE outbox SET destination_thread=22 WHERE id=?", (identifier,))
    assert len(mem.get(current, readable_ids=set(), now=20)["turns"]) == 2


def test_pending_pruning_cannot_overwrite_confirmed_topic_or_replay(memory):
    _, store = memory
    mem = DialogueMemory(store, max_turns=2)
    mem.record(scope(), "confirmed", "Q", "A", state={"topic": "Confirmed topic"}, at=1)
    for index in range(2, 6):
        identifier = outgoing(store, state="pending")
        mem.record(scope(), f"pending{index}", "Q", "A", state={"topic": "Pending topic"}, at=index, outbox_ids=(identifier,))
    assert not mem.record(scope(), "pending2", "Replay", "Replay", at=50)
    assert mem.get(scope(), readable_ids=set(), now=100)["topic"] == "Confirmed topic"


def test_readability_is_checked_before_delivery_promotion(memory):
    mem, store = memory
    identifier = outgoing(store)
    mem.record(scope(), "event", "Q", "Knowledge answer", state={"topic": "Protected"}, at=10, evidence_ids=("manual",), outbox_ids=(identifier,))
    assert mem.get(scope(), readable_ids=set(), now=20)["turns"] == []
    assert mem.get(scope(), readable_ids={"manual"}, now=20)["topic"] == "Protected"
    assert mem.get(scope(), readable_ids=set(), now=20)["topic"] == ""


def test_future_turn_is_not_current_context(memory):
    mem, _ = memory
    mem.record(scope(), "future", "Q", "A", state={"topic": "Future"}, at=100)
    assert mem.get(scope(), readable_ids=set(), now=99)["turns"] == []
    assert mem.get(scope(), readable_ids=set(), now=100)["topic"] == "Future"


def test_state_projection_excludes_actions_summaries_and_unknown_properties(memory):
    mem, store = memory
    mem.record(scope(), "event", "Actual user text", "Actual direct answer", state={
        "topic": "Packaging", "entities": ["Box", "Box"],
        "pending_offer": {"kind": "confirm_task", "text": "Confirm this draft?", "execute": True},
        "task_id": "t1", "actions": [{"op": "delete_all"}], "summary": "Internal summary",
    }, at=10)
    result = mem.get(scope(), readable_ids=set(), now=20)
    assert result["entities"] == ["Box"]
    assert result["pending_offer"] == {"kind": "confirm_task", "text": "Confirm this draft?"}
    assert "actions" not in result and "summary" not in result
    assert not store.rows("SELECT * FROM tasks") and not store.rows("SELECT * FROM outbox")


@pytest.mark.parametrize("invalid_state", [
    [], "topic", {"topic": []}, {"entities": "box"}, {"entities": [1]},
    {"task_id": 3}, {"pending_offer": []},
    {"pending_offer": {"kind": "execute", "text": "Do it"}},
    {"pending_offer": {"kind": "draft", "text": ""}},
])
def test_malformed_state_is_rejected_without_replacing_prior_confirmation(memory, invalid_state):
    mem, _ = memory
    mem.record(scope(), "old", "Q", "A", state={"topic": "Confirmed"}, at=1)
    with pytest.raises(ValueError):
        mem.record(scope(), "bad", "Q", "A", state=invalid_state, at=2)
    assert mem.get(scope(), readable_ids=set(), now=20)["topic"] == "Confirmed"


def test_failed_provider_turn_does_not_clear_previous_context(memory):
    mem, _ = memory
    mem.record(scope(), "old", "Q", "A", state={"topic": "Confirmed"}, at=1)
    with pytest.raises(ValueError):
        mem.record(scope(), "provider-failed", "Q", "", at=2)
    assert mem.get(scope(), readable_ids=set(), now=20)["topic"] == "Confirmed"


def test_explicit_state_replacement_can_clear_offer(memory):
    mem, _ = memory
    mem.record(scope(), "old", "Q", "A", state={"topic": "Packing", "pending_offer": {"kind": "draft", "text": "Draft a message"}}, at=1)
    mem.record(scope(), "done", "Yes", "Draft supplied", state={"topic": "Packing", "pending_offer": None}, at=2)
    assert mem.get(scope(), readable_ids=set(), now=20)["pending_offer"] is None


def test_json_size_is_bounded_and_no_pair_is_split(memory):
    _, store = memory
    mem = DialogueMemory(store, max_turns=5, max_chars=512)
    for index in range(10):
        mem.record(scope(), str(index), "User " + "u"*5000, "Actual answer " + "a"*5000,
                   state={"topic": "t"*5000, "entities": ["e"*5000]*30,
                          "pending_offer": {"kind": "draft", "text": "d"*5000}}, at=index)
    result = mem.get(scope(), readable_ids=set(), now=100)
    assert len(json.dumps(result, ensure_ascii=False, separators=(",", ":"))) <= 512
    assert 2 <= len(result["turns"]) <= 5 and len(result["turns"]) % 2 == 0
    assert [m["role"] for m in result["turns"]] == ["user", "assistant"] * (len(result["turns"])//2)
    assert all(m["content"] for m in result["turns"])


def test_record_and_receipt_share_the_callers_transaction(memory):
    mem, store = memory
    with pytest.raises(RuntimeError):
        with store.transaction():
            mem.record(scope(), "event", "Q", "A", at=1)
            raise RuntimeError("Rollback")
    assert mem.get(scope(), readable_ids=set(), now=20)["turns"] == []
    assert mem.record(scope(), "event", "Q", "A", at=2)


@pytest.mark.parametrize("kwargs", [{"max_turns": 1}, {"max_turns": True}, {"max_chars": 0}, {"max_chars": True}])
def test_invalid_memory_budget_is_rejected(memory, kwargs):
    _, store = memory
    with pytest.raises(ValueError):
        DialogueMemory(store, **kwargs)
