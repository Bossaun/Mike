"""Stateless, text-only OpenAI Responses payloads and bounded JSON parsing.

No credentials, network requests, provider history, tools or database access live
here. Provider errors, refusals and reasoning text never enter error messages.
"""

import copy
import json
import math


MAX_RESPONSE_CHARS = 1_000_000
MAX_OUTPUT_ITEMS = 64
MAX_JSON_DEPTH = 64
_EFFORTS = {'low', 'medium', 'high', 'xhigh', 'max'}
_MAX_TOKENS = 2**63 - 1


def responses_payload(config, system, content, schema) -> dict:
    if not isinstance(config, dict):
        raise ValueError('Invalid Responses configuration')
    model = config.get('model')
    maximum = config.get('max_output_tokens', 3000)
    json_mode = config.get('json_mode', False)
    if (not isinstance(model, str) or not model.strip() or len(model) > 200
            or type(maximum) is not int or not 1 <= maximum <= 128000
            or type(json_mode) is not bool):
        raise ValueError('Invalid Responses configuration')
    if (not isinstance(system, str) or not system.strip()
            or not isinstance(content, str) or not content.strip()):
        raise ValueError('Invalid Responses input')
    if not json_mode and not isinstance(schema, dict):
        raise ValueError('Invalid Responses schema')
    format = ({'type': 'json_object'} if json_mode else
              {'type': 'json_schema', 'name': 'goldway_actions', 'strict': True,
               'schema': copy.deepcopy(schema)})
    payload = {'model': model, 'instructions': system,
               'input': [{'role': 'user', 'content': content}],
               'text': {'format': format}, 'max_output_tokens': maximum, 'store': False}
    if 'reasoning_effort' in config:
        effort = config['reasoning_effort']
        if not isinstance(effort, str) or effort not in _EFFORTS:
            raise ValueError('Invalid Responses reasoning effort')
        payload['reasoning'] = {'effort': effort}
    if 'service_tier' in config:
        if config['service_tier'] != 'default':
            raise ValueError('Unsupported Responses service tier')
        payload['service_tier'] = 'default'
    return payload


def _count(value):
    if type(value) is not int or not 0 <= value <= _MAX_TOKENS:
        raise ValueError('Invalid Responses token usage')
    return value


def _usage(data):
    usage = data.get('usage')
    if not isinstance(usage, dict):
        raise ValueError('Invalid Responses token usage')
    inp = _count(usage.get('input_tokens'))
    out = _count(usage.get('output_tokens'))
    if inp + out > _MAX_TOKENS:
        raise ValueError('Invalid Responses token usage')
    if 'total_tokens' in usage and _count(usage['total_tokens']) != inp + out:
        raise ValueError('Invalid Responses token usage')
    for field, count, detail in (('input_tokens_details', inp, 'cached_tokens'),
                                 ('output_tokens_details', out, 'reasoning_tokens')):
        if field in usage:
            details = usage[field]
            if not isinstance(details, dict) or (detail in details and _count(details[detail]) > count):
                raise ValueError('Invalid Responses token usage')
    return inp, out


def _reasoning(item):
    allowed = {'type', 'id', 'status', 'summary', 'content', 'encrypted_content'}
    if set(item) - allowed or item.get('status') not in (None, 'completed'):
        raise ValueError('Invalid Responses reasoning output')
    size = 0
    for field, kind in (('summary', 'summary_text'), ('content', 'reasoning_text')):
        parts = item.get(field, [] if field == 'summary' else None)
        if parts is None and field == 'content':
            continue
        if not isinstance(parts, list) or len(parts) > MAX_OUTPUT_ITEMS:
            raise ValueError('Invalid Responses reasoning output')
        for part in parts:
            if (not isinstance(part, dict) or set(part) != {'type', 'text'}
                    or part['type'] != kind or not isinstance(part['text'], str)):
                raise ValueError('Invalid Responses reasoning output')
            size += len(part['text'])
    encrypted = item.get('encrypted_content')
    if encrypted is not None:
        if not isinstance(encrypted, str):
            raise ValueError('Invalid Responses reasoning output')
        size += len(encrypted)
    return size


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError('Nonfinite JSON constant')


def _float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError('Nonfinite JSON number')
    return number


def _check_depth(raw):
    depth, quoted, escaped = 0, False, False
    for character in raw:
        if quoted:
            if escaped:
                escaped = False
            elif character == '\\':
                escaped = True
            elif character == '"':
                quoted = False
        elif character == '"':
            quoted = True
        elif character in '[{':
            depth += 1
            if depth > MAX_JSON_DEPTH:
                raise ValueError('JSON nesting exceeds limit')
        elif character in ']}':
            depth -= 1


def parse_response(data) -> tuple[str, int, int]:
    if (not isinstance(data, dict) or data.get('status') != 'completed'
            or data.get('error') is not None or data.get('incomplete_details') is not None
            or data.get('refusal') is not None):
        raise ValueError('Responses request did not complete successfully')
    inp, out = _usage(data)
    output = data.get('output')
    if not isinstance(output, list) or not 1 <= len(output) <= MAX_OUTPUT_ITEMS:
        raise ValueError('Invalid Responses output')
    raw, reasoning_chars = None, 0
    for item in output:
        if not isinstance(item, dict):
            raise ValueError('Invalid Responses output')
        if 'id' in item and (not isinstance(item['id'], str) or not 1 <= len(item['id']) <= 256):
            raise ValueError('Invalid Responses output identifier')
        if item.get('type') == 'reasoning':
            reasoning_chars += _reasoning(item)
            if reasoning_chars > MAX_RESPONSE_CHARS:
                raise ValueError('Responses reasoning output exceeds limit')
            continue
        if (item.get('type') != 'message' or item.get('role') != 'assistant'
                or item.get('status') != 'completed' or raw is not None
                or set(item) - {'id', 'type', 'role', 'status', 'content', 'phase'}
                or item.get('phase') not in (None, 'final_answer')):
            raise ValueError('Unexpected Responses output item')
        parts = item.get('content')
        if not isinstance(parts, list) or len(parts) != 1 or not isinstance(parts[0], dict):
            raise ValueError('Invalid Responses final message')
        part = parts[0]
        if (part.get('type') != 'output_text' or not isinstance(part.get('text'), str)
                or set(part) - {'type', 'text', 'annotations', 'logprobs'}):
            raise ValueError('Unexpected Responses final content')
        if 'annotations' in part:
            annotations = part['annotations']
            if (not isinstance(annotations, list) or len(annotations) > 128
                    or any(not isinstance(annotation, dict) or annotation.get('type') not in
                           ('url_citation', 'file_citation', 'container_file_citation', 'file_path')
                           for annotation in annotations)):
                raise ValueError('Invalid Responses annotations')
        if part.get('logprobs') is not None and not isinstance(part['logprobs'], list):
            raise ValueError('Invalid Responses text metadata')
        raw = part['text']
        if len(raw) > MAX_RESPONSE_CHARS or not raw.strip():
            raise ValueError('Responses final JSON exceeds bounds')
    if raw is None:
        raise ValueError('Responses final JSON is missing')
    try:
        _check_depth(raw)
        parsed = json.loads(raw, object_pairs_hook=_json_object, parse_constant=_invalid_constant,
                            parse_float=_float)
        if not isinstance(parsed, dict):
            raise ValueError('JSON object required')
    except (ValueError, TypeError, RecursionError, OverflowError):
        raise ValueError('Invalid Responses final JSON') from None
    return raw, inp, out
