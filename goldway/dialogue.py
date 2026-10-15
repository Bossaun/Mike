"""Small current-dialogue memory with delivery and current-evidence gates.

Only explicit new turns enter this memory. It neither imports Telegram history
nor reads audits, invokes a model, sends messages, or grants action authority.
"""

from contextlib import nullcontext
import hashlib
import json
import math


_PREFIX = "dialogue-scope:v1:"
_KINDS = {"explain", "draft", "clarify", "confirm_task"}


def _dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _label(value, maximum=128):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= maximum


def _chat(value):
    if not isinstance(value, str):
        return None
    try:
        number = int(value)
    except ValueError:
        return None
    return str(number) if number and str(number) == value else None


def _scope_parts(scope):
    if not isinstance(scope, str) or not scope.startswith(_PREFIX):
        return None
    try:
        parts = json.loads(scope[len(_PREFIX):])
    except (ValueError, TypeError):
        return None
    if not isinstance(parts, list) or len(parts) != 6:
        return None
    business, sender, channel, chat, connection, thread = parts
    if not _label(business) or not _label(sender) or not _chat(chat):
        return None
    if channel not in ("bot", "business", "group"):
        return None
    if channel == "business":
        if int(chat) <= 0 or not _label(connection, 512) or thread is not None:
            return None
    elif connection is not None:
        return None
    if channel == "bot" and (int(chat) <= 0 or thread is not None):
        return None
    if channel == "group" and (int(chat) >= 0 or
            (thread is not None and (type(thread) is not int or thread <= 0))):
        return None
    return parts


def scope_from_payload(payload, business):
    """Derive scope only from a trusted sender/channel and original source ID.

    An actor-only fallback and a supplied ``dialogue_scope`` override are never
    used. Telegram topic 1 is General, matching the ordinary delivery route.
    """
    if not isinstance(payload, dict) or not _label(business):
        return None
    sender, channel, source = (payload.get(k) for k in ("sender", "channel", "source_id"))
    if not _label(sender) or not isinstance(source, str) or len(source) > 1024:
        return None
    connection = None
    if source.startswith("business:") and channel == "business":
        pieces = source[len("business:"):].rsplit(":", 2)
        if len(pieces) != 3:
            return None
        connection, chat, message = pieces
    elif source.startswith("telegram:") and channel in ("bot", "group"):
        pieces = source.split(":")
        if len(pieces) != 3:
            return None
        _, chat, message = pieces
    else:
        return None
    if not _chat(chat) or not _chat(message) or int(message) <= 0:
        return None
    thread = payload.get("message_thread_id", payload.get("thread_id"))
    if ("message_thread_id" in payload and "thread_id" in payload
            and payload["message_thread_id"] != payload["thread_id"]):
        return None
    if thread == 1 and type(thread) is int:
        thread = None
    scope = _PREFIX + _dump([business, sender, channel, chat, connection, thread])
    return scope if _scope_parts(scope) is not None else None


def _empty():
    return {"turns": [], "topic": "", "entities": [], "pending_offer": None, "task_id": None}


def _clip(text, size):
    return text if len(text) <= size else text[:max(0, size-1)] + ("…" if size else "")


def _timestamp(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


class DialogueMemory:
    """Persist bounded complete pairs; ``max_turns`` counts role messages.

    A supplied state is a replacement snapshot, not an action or partial patch.
    ``state=None`` preserves the last confirmed state and its provenance. A
    separate bounded candidate list waits for actual matching sent outbox rows.
    """

    def __init__(self, store, max_turns=6, max_chars=6000):
        if type(max_turns) is not int or not 2 <= max_turns <= 50:
            raise ValueError("Dialogue max_turns must be between 2 and 50")
        if type(max_chars) is not int or not 256 <= max_chars <= 60000:
            raise ValueError("Dialogue max_chars must be between 256 and 60000")
        self.s, self.max_turns, self.max_chars = store, max_turns, max_chars
        self.pairs = max_turns // 2

    def _state(self, state):
        if state is None:
            return None
        if not isinstance(state, dict):
            raise ValueError("Dialogue state must be an object")
        result = {k: v for k, v in _empty().items() if k != "turns"}
        for key in ("topic", "task_id"):
            if key not in state:
                continue
            value = state[key]
            if key == "task_id" and value is None:
                continue
            if not isinstance(value, str):
                raise ValueError("Invalid dialogue " + key)
            result[key] = _clip(value.strip(), 512 if key == "topic" else 128)
        if "entities" in state:
            values = state["entities"]
            if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
                raise ValueError("Invalid dialogue entities")
            result["entities"] = list(dict.fromkeys(_clip(v.strip(), 128) for v in values if v.strip()))[:12]
        if state.get("pending_offer") is not None:
            offer = state["pending_offer"]
            if (not isinstance(offer, dict) or offer.get("kind") not in _KINDS
                    or not isinstance(offer.get("text"), str) or not offer["text"].strip()):
                raise ValueError("Invalid dialogue pending offer")
            result["pending_offer"] = {"kind": offer["kind"], "text": _clip(offer["text"].strip(), 1000)}
        # Leave at least half the return budget for actual dialogue messages.
        while len(_dump(result)) > self.max_chars // 2:
            if result["entities"]:
                result["entities"].pop()
            elif result["pending_offer"] and len(result["pending_offer"]["text"]) > 16:
                result["pending_offer"]["text"] = _clip(result["pending_offer"]["text"], len(result["pending_offer"]["text"])//2)
            elif len(result["topic"]) > 16:
                result["topic"] = _clip(result["topic"], len(result["topic"])//2)
            elif result["task_id"]:
                result["task_id"] = None
            elif result["pending_offer"]:
                result["pending_offer"] = None
            else:
                result["topic"] = ""
        return result

    def _key(self, scope):
        return "dialogue:v1:" + hashlib.sha256(scope.encode()).hexdigest()

    def _load(self, scope):
        row = self.s.execute("SELECT value FROM runtime WHERE key=?", (self._key(scope),)).fetchone()
        if row:
            try:
                value = json.loads(row[0])
                if (value.get("scope") == scope and value.get("version") == 1
                        and isinstance(value.get("confirmed"), list)
                        and isinstance(value.get("pending"), list)):
                    return value
            except (ValueError, TypeError, AttributeError):
                pass
        return {"version": 1, "scope": scope, "confirmed": [], "pending": [], "checkpoint": None, "sequence": 0}

    def _save(self, scope, document):
        self.s.execute("INSERT INTO runtime(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (self._key(scope), _dump(document)))

    def _transaction(self):
        return nullcontext() if self.s.db.in_transaction else self.s.transaction()

    def _bounded(self, records):
        records = sorted(records, key=lambda r: (r["at"], r["sequence"]))[-self.pairs:]
        while len(records) > 1 and len(_dump(records)) > self.max_chars:
            records.pop(0)
        if records:
            record = records[0]
            while len(_dump(records)) > self.max_chars and (record["user"] or record["assistant"]):
                key = max(("user", "assistant"), key=lambda k: len(record[k]))
                record[key] = _clip(record[key], len(record[key])//2)
            if len(_dump(records)) > self.max_chars:
                return []
        return records

    def _delivered(self, record, parts):
        if not record["outbox_ids"]:
            return True  # Explicit synthetic/direct insertion, not a delivery claim.
        business, sender, channel, chat, connection, thread = parts
        for identifier in record["outbox_ids"]:
            row = self.s.execute("SELECT * FROM outbox WHERE id=?", (identifier,)).fetchone()
            if (not row or row["business"] != business or row["recipient"] != sender
                    or row["state"] != "sent" or row["transport"] != channel
                    or str(row["destination_chat"]) != chat
                    or (row["destination_connection"] or None) != connection
                    or (row["destination_thread"] or None) != thread):
                return False
        return True

    def record(self, scope, event_id, user_text, assistant_text, *, state=None,
               evidence_ids=(), at, outbox_ids=(), context_kind=None):
        if _scope_parts(scope) is None:
            return False
        if (not _label(event_id, 256) or not _timestamp(at)
                or not isinstance(user_text, str) or not user_text.strip()
                or not isinstance(assistant_text, str) or not assistant_text.strip()):
            raise ValueError("Invalid dialogue turn")
        if (not isinstance(evidence_ids, (tuple, list, set, frozenset))
                or len(evidence_ids) > 16 or any(not _label(v, 160) for v in evidence_ids)):
            raise ValueError("Invalid dialogue evidence IDs")
        if (not isinstance(outbox_ids, (tuple, list, set, frozenset))
                or len(outbox_ids) > 16 or any(type(v) is not int or v <= 0 for v in outbox_ids)):
            raise ValueError("Invalid dialogue outbox IDs")
        if context_kind not in (None, 'work'):
            raise ValueError('Invalid dialogue context kind')
        validated_state = self._state(state)
        receipt = "dialogue:receipt:v1:" + hashlib.sha256(_dump([scope, event_id]).encode()).hexdigest()
        with self._transaction():
            if self.s.execute("SELECT 1 FROM runtime WHERE key=?", (receipt,)).fetchone():
                return False
            doc = self._load(scope)
            doc["sequence"] += 1
            entry = {"event": event_id, "at": at, "sequence": doc["sequence"],
                     "user": _clip(user_text, self.max_chars), "assistant": _clip(assistant_text, self.max_chars),
                     "state": validated_state, "evidence_ids": sorted(set(evidence_ids)),
                     "outbox_ids": sorted(set(outbox_ids))}
            if context_kind:
                entry['context_kind'] = context_kind
            category = "pending" if entry["outbox_ids"] else "confirmed"
            doc[category] = self._bounded(doc[category] + [entry])
            if category == "confirmed" and validated_state is not None:
                self._checkpoint(doc, entry)
            self._save(scope, doc)
            self.s.execute("INSERT INTO runtime(key,value) VALUES(?,?)", (receipt, "recorded"))
        return True

    def _checkpoint(self, document, entry):
        previous = document.get("checkpoint")
        if previous is None or (entry["at"], entry["sequence"]) > (previous["at"], previous["sequence"]):
            document["checkpoint"] = {k: entry[k] for k in
                ("at", "sequence", "state", "evidence_ids", "outbox_ids")}

    def get(self, scope, *, readable_ids: set, now, include_evidence=False):
        parts = _scope_parts(scope)
        if parts is None:
            return _empty()
        if not _timestamp(now) or not isinstance(readable_ids, (set, frozenset)):
            raise ValueError("Current dialogue access and time are required")
        def visible(entry):
            return (entry["at"] <= now and set(entry["evidence_ids"]).issubset(readable_ids)
                    and self._delivered(entry, parts))
        with self._transaction():
            doc = self._load(scope)
            promoted = [entry for entry in doc["pending"] if visible(entry)]
            if promoted:
                doc["pending"] = [entry for entry in doc["pending"] if entry not in promoted]
                doc["confirmed"] = self._bounded(doc["confirmed"] + promoted)
                for entry in promoted:
                    if entry["state"] is not None:
                        self._checkpoint(doc, entry)
                self._save(scope, doc)
            entries = [entry for entry in doc["confirmed"] if visible(entry)]
            states = [entry for entry in entries if entry["state"] is not None]
            checkpoint = doc.get("checkpoint")
            if checkpoint and visible(checkpoint):
                states.append(checkpoint)
            result = _empty()
            if states:
                result.update(max(states, key=lambda r: (r["at"], r["sequence"]))["state"])
            pairs = [[{"role": "user", "content": e["user"]},
                      {"role": "assistant", "content": e["assistant"]}] for e in entries]
            for pair in reversed(pairs):
                candidate = dict(result, turns=pair + result["turns"])
                if len(_dump(candidate)) <= self.max_chars:
                    result = candidate
                elif result["turns"]:
                    break
                else:
                    while len(_dump(candidate)) > self.max_chars and any(m["content"] for m in pair):
                        message = max(pair, key=lambda m: len(m["content"]))
                        message["content"] = _clip(message["content"], len(message["content"])//2)
                    if len(_dump(candidate)) <= self.max_chars and all(m["content"] for m in pair):
                        result = candidate
                    break
            if include_evidence:
                references = {ref for entry in entries for ref in entry['evidence_ids']}
                if checkpoint and visible(checkpoint): references.update(checkpoint['evidence_ids'])
                result['_evidence_ids'] = sorted(references)
                if entries and result['turns'] and entries[-1].get('context_kind') == 'work':
                    result['_last_context_kind'] = 'work'
            return result
