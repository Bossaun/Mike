"""Finite current-result lookup; no history inference, writes, or model calls."""

import hashlib
import json
import re


MAX_QUERY_CHARS = 2048
MAX_RESULT_BYTES = 2400
MAX_SOURCE_BYTES = 65536
MAX_LINKED_SOURCES = 32
RESULT_LIMITATION = (
    'Зарегистрированный результат со слов источника; '
    'не независимая проверка выполнения и не история изменений.'
)
_TASK = re.compile(r'\b(?:задач[а-я]*|поручени[а-я]*)\b')
_NUMBER = re.compile(r'\b(?:задач[а-я]*|поручени[а-я]*)\s*(?:[№#]\s*)?([0-9]+)')
_RESULT = re.compile(r'\b(?:результат[а-я]*|итог[а-я]*|что\s+(?:было\s+)?сделано|чем\s+закончил[а-я]*)\b')
_ASK = re.compile(r'^(?:пожалуйста[, :]+)?(?:како[йв][а-я]*|какие|что|чем|где|покажи(?:те)?|расскажи(?:те)?)\b')
_CONTROL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')


def result_request(query):
    """Return (result question, unique public number), with no implicit focus.

    An ambiguous/missing number is still a result question, so it cannot fall
    back to an unrelated overview. This recognizes questions, not task actions.
    """
    if not isinstance(query, str) or not 0 < len(query) <= MAX_QUERY_CHARS:
        return False, None
    text = re.sub(r'\s+', ' ', query.casefold().replace('ё', 'е')).strip()
    if not (_TASK.search(text) and _RESULT.search(text)
            and (_ASK.search(text) or text.endswith('?'))):
        return False, None
    numbers = list(_NUMBER.finditer(text))
    # Count marker tokens too: malformed/second references are not ignored.
    markers = list(re.finditer(r'[№#]', text))
    if (len(numbers) != 1 or len(markers) > 1
            or any(not numbers[0].start() <= marker.start() < numbers[0].end()
                   for marker in markers)):
        return True, None
    digits = numbers[0][1]
    if (len(digits) > 9 or digits.startswith('0')
            or re.match(r'(?:\w|[.,][0-9])', text[numbers[0].end():])
            or re.search(r'\d', text[:numbers[0].start()] + text[numbers[0].end():])):
        return True, None
    return True, int(digits)


def _source_ref(business, source_id):
    packed = json.dumps([business, source_id], ensure_ascii=False, separators=(',', ':'))
    return 'task-source:' + hashlib.sha256(packed.encode()).hexdigest()


def registered_result(store, business, task_id, revision):
    """Return only a bounded literal result and its private-state fingerprint.

    The caller MUST check current task role/ACL grants before calling. All
    linked source bodies must fit the finite scan budget; otherwise uniqueness
    cannot be established. No source body or original source ID is projected.
    """
    row = store.execute('''SELECT CASE WHEN typeof(evidence)='text'
        AND length(CAST(evidence AS BLOB))<=? THEN evidence END
        FROM tasks WHERE business=? AND id=? AND status='completed' AND revision=?''',
        (MAX_RESULT_BYTES, business, task_id, revision)).fetchone()
    text = row[0] if row else None
    if not isinstance(text, str) or not text.strip() or _CONTROL.search(text):
        return None
    sources = store.rows('''SELECT ts.source_id,s.deleted,
        CASE WHEN typeof(s.body)='text' AND length(CAST(s.body AS BLOB))<=?
             THEN s.body END AS body
        FROM task_sources ts LEFT JOIN sources s
          ON s.business=ts.business AND s.id=ts.source_id
        WHERE ts.business=? AND ts.task_id=? ORDER BY ts.source_id LIMIT ?''',
        (MAX_SOURCE_BYTES, business, task_id, MAX_LINKED_SOURCES + 1))
    if not sources or len(sources) > MAX_LINKED_SOURCES:
        return None
    match = None
    for source in sources:
        body, source_id = source['body'], source['source_id']
        if (not isinstance(body, str) or not isinstance(source_id, str)
                or not 0 < len(source_id) <= 256 or _CONTROL.search(source_id)):
            return None
        start = body.find(text)
        if start < 0:
            continue
        # Even a deleted competing source leaves the provenance ambiguous.
        if (match is not None or source['deleted'] != 0
                or body.find(text, start + 1) >= 0):
            return None
        match = (source_id, body, start)
    if match is None:
        return None
    source_id, body, start = match
    result = {'state': 'registered_report', 'text': text,
              'source_ref': _source_ref(business, source_id),
              'span': {'start': start, 'end': start + len(text)},
              'limitation': RESULT_LIMITATION}
    # Changes anywhere in the selected source invalidate the pre-answer snapshot.
    packed = json.dumps([result, hashlib.sha256(body.encode()).hexdigest()],
                        ensure_ascii=False, separators=(',', ':'))
    return result, hashlib.sha256(packed.encode()).hexdigest()
