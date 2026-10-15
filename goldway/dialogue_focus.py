"""Bounded advisory focus for a small set of direct follow-up requests.

The caller supplies already ACL-filtered dialogue. These hints describe how to
read the latest delivered answer; they grant no task or permission authority.
No topic fallback, fact inference, history lookup or wall clock is used.
"""

import re


_SOURCE_FOOTER = "\n\nИсточники:\n"
_YES = re.compile(r"(?:давай(?:те)?|да|ага|угу|хорошо|ок|окей|yes|ok|"
                  r"да[, ]+давай(?:те)?)")
_CONTINUE = re.compile(
    r"(?:продолжай(?:те)?|продолжим|да[, ]+продолжай(?:те)?|"
    r"давай(?:те)?\s+(?:продолжим|дальше|подробнее|поподробнее)|"
    r"(?:подробнее|поподробнее))"
)
_SHORTEN = re.compile(
    r"(?:(?:сделай(?:те)?|напиши(?:те)?|перепиши(?:те)?)\s+"
    r"(?:(?:(?:последний|предыдущий|твой|этот)\s+)?ответ\s+)?"
    r"(?:покороче|короче)|(?:можно\s+)?(?:покороче|короче)|"
    r"сократи(?:те)?(?:\s+(?:(?:последний|предыдущий|твой|этот)\s+)?ответ)?)"
)
_CITE = re.compile(
    r"(?:откуда\s+(?:ты\s+это\s+знаешь|это\s+известно|эта\s+информация|данные)|"
    r"(?:покажи(?:те)?|дай(?:те)?|укажи(?:те)?)\s+источник[и]?"
    r"(?:\s+(?:последнего|предыдущего|этого)\s+ответа)?|"
    r"(?:какие\s+)?источники(?:\s+у\s+(?:этого|твоего)\s+ответа)?|"
    r"на\s+чем\s+основан\s+(?:(?:этот|твой|последний|предыдущий)\s+)?ответ)"
)
_ORDINAL_WORDS = (
    ("первый", "первая", "первую", "первой", "первого", "первом"),
    ("второй", "вторая", "вторую", "второго", "втором"),
    ("третий", "третья", "третью", "третьей", "третьего", "третьем"),
    ("четвертый", "четвертая", "четвертую", "четвертой", "четвертого", "четвертом"),
    ("пятый", "пятая", "пятую", "пятой", "пятого", "пятом"),
    ("шестой", "шестая", "шестую", "шестого", "шестом"),
    ("седьмой", "седьмая", "седьмую", "седьмого", "седьмом"),
    ("восьмой", "восьмая", "восьмую", "восьмого", "восьмом"),
    ("девятый", "девятая", "девятую", "девятой", "девятого", "девятом"),
    ("десятый", "десятая", "десятую", "десятой", "десятого", "десятом"),
    ("одиннадцатый", "одиннадцатая", "одиннадцатую", "одиннадцатой", "одиннадцатого", "одиннадцатом"),
    ("двенадцатый", "двенадцатая", "двенадцатую", "двенадцатой", "двенадцатого", "двенадцатом"),
)
_ORDINAL_INDEX = {word: index for index, words in enumerate(_ORDINAL_WORDS) for word in words}
_ORDINAL = "(?:" + "|".join(_ORDINAL_INDEX) + r"|(?:[1-9][0-9]{0,2})(?:[- ]?(?:ый|ой|ий|ом|ем|го|ую|ая|й|м|ю|я))?)"
_ENTITY_NOUN = r"(?:бренд[ае]?|компани[июи]|марк[ауе]|направлени[еию]|товар[еа]?|продукт[еа]?|пункт[еа]?|вариант[еа]?)"
_EXPLAIN = re.compile(
    r"(?:(?:расскажи(?:те)?|объясни(?:те)?)\s+(?:(?:подробнее|поподробнее)\s+)?|"
    r"(?:подробнее|поподробнее)\s+|)"
    r"(?:о|об|про)\s+(?P<ordinal>" + _ORDINAL + r")"
    r"(?:\s+" + _ENTITY_NOUN + r")?(?:\s+(?:подробнее|поподробнее))?"
)


def _request(text):
    if not isinstance(text, str) or not text.strip() or len(text) > 512:
        return None
    value = " ".join(text.casefold().replace("ё", "е").split())
    value = re.sub(r"[.!?…]+$", "", value).strip()
    value = re.sub(r"^(?:пожалуйста|please)(?:,\s*|\s+)", "", value)
    value = re.sub(r"(?:,\s*|\s+)(?:пожалуйста|please)$", "", value).strip()
    return value or None


def _safe_offer(offer):
    if not isinstance(offer, dict):
        return None
    kind, text = offer.get("kind"), offer.get("text")
    if (not isinstance(kind, str) or kind not in {"explain", "draft", "clarify"} or
            not isinstance(text, str) or not text.strip()):
        return None
    return {"kind": kind, "text": text.strip()[:1000]}


def _verified_entities(entities, answer):
    """Verify every candidate's first mention, without overlapping name spans.

    The visible bounded answer must independently support the complete stored
    order. Word/name boundaries reject a shorter name embedded in another word
    or a hyphenated name. Ambiguous overlapping first mentions require a new
    clarification rather than guessing which list the user means.
    """
    if not isinstance(entities, list) or not 1 <= len(entities) <= 12:
        return None
    names = []
    for entity in entities:
        if not isinstance(entity, str) or not entity.strip() or len(entity.strip()) > 128:
            return None
        names.append(entity.strip())
    if len({name.casefold() for name in names}) != len(names):
        return None
    end = 0
    for name in names:
        pattern = r"(?<![\w'’-])" + re.escape(name) + r"(?![\w'’-])"
        match = re.search(pattern, answer, re.IGNORECASE)
        if match is None or match.start() < end:
            return None
        end = match.end()
    return names


def build_followup(text, dialogue) -> dict | None:
    """Return a bounded hint for a direct continuation, or ``None``.

    A latest nonempty assistant turn is required. Unknown/quoted/metalinguistic
    requests fail closed. Ordinals with missing/unverified entities return
    clarify_entity; explain_entity requires all stored names in stated order in
    the bounded latest answer body. Bare agreement with a pending
    confirm_task offer is left to existing task confirmation logic. Output has
    exactly kind, target_answer (<=2000 chars), selected_entity (<=128 chars or
    None), and a copied non-task pending_offer (text <=1000 chars or None).
    """
    request = _request(text)
    if request is None or not isinstance(dialogue, dict):
        return None
    turns = dialogue.get("turns")
    if not isinstance(turns, list) or not turns or not isinstance(turns[-1], dict):
        return None
    last = turns[-1]
    answer = last.get("content")
    if last.get("role") != "assistant" or not isinstance(answer, str):
        return None
    answer = answer.partition(_SOURCE_FOOTER)[0].strip()[:2000]
    if not answer:
        return None

    offer = dialogue.get("pending_offer")
    selected = None
    if _YES.fullmatch(request):
        if isinstance(offer, dict) and offer.get("kind") == "confirm_task":
            return None
        kind = "continue_last_turn"
    elif _CONTINUE.fullmatch(request):
        kind = "continue_last_turn"
    elif _SHORTEN.fullmatch(request):
        kind = "shorten_last_answer"
    elif _CITE.fullmatch(request):
        kind = "cite_last_answer"
    else:
        match = _EXPLAIN.fullmatch(request)
        if match is None:
            return None
        ordinal = match.group("ordinal")
        index = _ORDINAL_INDEX.get(ordinal)
        if index is None:
            index = int(re.match(r"\d+", ordinal).group()) - 1
        entities = _verified_entities(dialogue.get("entities"), answer)
        if entities is None or index >= len(entities):
            kind = "clarify_entity"
        else:
            selected = entities[index]
            kind = "explain_entity"
    return {"kind": kind, "target_answer": answer,
            "selected_entity": selected, "pending_offer": _safe_offer(offer)}
