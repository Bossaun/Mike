"""Pure Responses API contracts with synthetic payloads and provider output."""

import copy
import json

import pytest

from goldway.responses import MAX_OUTPUT_ITEMS, MAX_RESPONSE_CHARS, parse_response, responses_payload


SCHEMA = {'type': 'object', 'additionalProperties': False,
          'properties': {'actions': {'type': 'array', 'items': {'type': 'string'}},
                         'summary': {'type': 'string'}},
          'required': ['actions', 'summary']}
RAW = json.dumps({'actions': [], 'summary': 'Синтетический ответ'}, ensure_ascii=False)


def message(text=RAW, **extra):
    return {'id': 'msg_synthetic', 'type': 'message', 'role': 'assistant',
            'status': 'completed', 'content': [{'type': 'output_text', 'text': text,
                                                'annotations': []}], **extra}


def response(**extra):
    return {'status': 'completed', 'output': [message()], 'error': None,
            'incomplete_details': None,
            'usage': {'input_tokens': 120, 'output_tokens': 30, 'total_tokens': 150,
                      'input_tokens_details': {'cached_tokens': 20},
                      'output_tokens_details': {'reasoning_tokens': 10}}, **extra}


def test_stateless_structured_payload_uses_responses_fields_without_enabling_tools_or_history():
    config = {'model': 'gpt-6.1-sol', 'max_output_tokens': 12000, 'reasoning_effort': 'medium',
              'previous_response_id': 'must-not-propagate', 'conversation': 'must-not-propagate',
              'store': True, 'tools': [{'type': 'computer_use_preview'}],
              'messages': [{'role': 'assistant', 'content': 'must-not-propagate'}]}
    before, original_schema = copy.deepcopy(config), copy.deepcopy(SCHEMA)
    payload = responses_payload(config, 'SYSTEM', '{"user":"текст"}', SCHEMA)
    assert payload == {
        'model': 'gpt-6.1-sol', 'instructions': 'SYSTEM',
        'input': [{'role': 'user', 'content': '{"user":"текст"}'}],
        'text': {'format': {'type': 'json_schema', 'name': 'goldway_actions',
                            'strict': True, 'schema': SCHEMA}},
        'max_output_tokens': 12000, 'store': False, 'reasoning': {'effort': 'medium'},
    }
    assert config == before and SCHEMA == original_schema
    payload['text']['format']['schema']['required'].append('synthetic-field')
    assert SCHEMA == original_schema


def test_json_object_mode_omits_schema_and_optional_reasoning():
    payload = responses_payload({'model': 'gpt-6.1-sol', 'json_mode': True}, 'SYSTEM', '{}', None)
    assert payload['text'] == {'format': {'type': 'json_object'}}
    assert payload['max_output_tokens'] == 3000 and payload['store'] is False
    assert 'reasoning' not in payload
    assert not {'tools', 'tool_choice', 'previous_response_id', 'conversation'} & payload.keys()
    assert 'service_tier' not in payload


def test_explicit_standard_service_tier_is_preserved():
    payload = responses_payload({'model': 'gpt-6.1-sol', 'service_tier': 'default'}, 'S', '{}', SCHEMA)
    assert payload['service_tier'] == 'default'


@pytest.mark.parametrize('tier', ['auto', 'fast', 'ultrafast', 'priority', 'flex', None, True, {}])
def test_service_tier_cannot_silently_select_a_more_expensive_mode(tier):
    with pytest.raises(ValueError, match='service tier'):
        responses_payload({'model': 'gpt-6.1-sol', 'service_tier': tier}, 'S', '{}', SCHEMA)


@pytest.mark.parametrize('effort', ['low', 'medium', 'high', 'xhigh', 'max'])
def test_only_supported_reasoning_efforts_are_forwarded(effort):
    payload = responses_payload({'model': 'gpt-6.1-sol', 'reasoning_effort': effort}, 'S', '{}', SCHEMA)
    assert payload['reasoning'] == {'effort': effort}


@pytest.mark.parametrize('effort', ['none', 'minimal', 'ultra', 'HIGH', '', None, 1, [], {}])
def test_unsupported_reasoning_efforts_fail_locally(effort):
    with pytest.raises(ValueError, match='reasoning effort'):
        responses_payload({'model': 'gpt-6.1-sol', 'reasoning_effort': effort}, 'S', '{}', SCHEMA)


@pytest.mark.parametrize('config', [
    {}, {'model': ''}, {'model': 'gpt-6.1-sol', 'max_output_tokens': True},
    {'model': 'gpt-6.1-sol', 'max_output_tokens': 0},
    {'model': 'gpt-6.1-sol', 'max_output_tokens': 128001},
    {'model': 'gpt-6.1-sol', 'max_output_tokens': 3000.5},
    {'model': 'gpt-6.1-sol', 'json_mode': 'true'},
])
def test_invalid_request_configuration_fails_before_transport(config):
    with pytest.raises(ValueError):
        responses_payload(config, 'S', '{}', SCHEMA)


def test_one_completed_final_json_message_and_valid_reasoning_are_parsed_without_exposing_reasoning():
    reasoning = {'id': 'rs_synthetic', 'type': 'reasoning', 'status': 'completed',
                 'summary': [{'type': 'summary_text', 'text': 'PRIVATE-REASONING'}],
                 'content': [{'type': 'reasoning_text', 'text': 'MORE-PRIVATE-REASONING'}],
                 'encrypted_content': 'OPAQUE-SYNTHETIC'}
    data = response(output=[reasoning, message(phase='final_answer')])
    before = copy.deepcopy(data)
    assert parse_response(data) == (RAW, 120, 30)
    assert data == before and 'PRIVATE-REASONING' not in parse_response(data)[0]


def test_usage_counts_include_reasoning_output_and_allow_zero_counts():
    data = response()
    assert parse_response(data)[1:] == (120, 30)
    assert parse_response(response(usage={'input_tokens': 0, 'output_tokens': 0}))[1:] == (0, 0)


@pytest.mark.parametrize('status', [None, 'incomplete', 'failed', 'cancelled', 'queued', 'in_progress'])
def test_noncompleted_response_is_rejected_without_remote_diagnostics(status):
    data = response(status=status, error={'message': 'PRIVATE-REMOTE-ERROR'})
    with pytest.raises(ValueError) as error:
        parse_response(data)
    assert 'PRIVATE' not in str(error.value)


@pytest.mark.parametrize('field, value', [
    ('error', {'message': 'PRIVATE-REMOTE-ERROR'}),
    ('incomplete_details', {'reason': 'PRIVATE-INCOMPLETE-DETAIL'}),
    ('refusal', 'PRIVATE-REFUSAL'),
])
def test_completed_status_cannot_mask_errors_refusal_or_incompleteness(field, value):
    with pytest.raises(ValueError) as error:
        parse_response(response(**{field: value}))
    assert 'PRIVATE' not in str(error.value)


@pytest.mark.parametrize('kind', [
    'function_call', 'custom_tool_call', 'computer_call', 'mcp_call', 'mcp_list_tools',
    'mcp_approval_request', 'web_search_call', 'file_search_call', 'code_interpreter_call',
    'image_generation_call', 'shell_call', 'local_shell_call', 'apply_patch_call',
    'tool_search_call', 'function_call_output', 'unknown', None,
])
def test_any_tool_or_unknown_output_is_rejected_even_beside_valid_json(kind):
    data = response(output=[message(), {'type': kind, 'arguments': 'PRIVATE-TOOL-ARGUMENTS'}])
    with pytest.raises(ValueError) as error:
        parse_response(data)
    assert 'PRIVATE' not in str(error.value)


@pytest.mark.parametrize('output', [
    [], None, {}, ['PRIVATE-MALFORMED'],
    [{'type': 'reasoning', 'summary': []}],
    [message(), message()],
    [message(role='user')], [message(status='incomplete')], [message(status=None)],
    [message(phase='commentary')],
    [message(id={})], [message(id='')],
    [message(tool_calls=[{'name': 'forbidden'}])],
])
def test_single_completed_assistant_message_is_required(output):
    with pytest.raises(ValueError):
        parse_response(response(output=output))


@pytest.mark.parametrize('parts', [
    [], None, {}, [None],
    [{'type': 'refusal', 'refusal': 'PRIVATE-REFUSAL'}],
    [{'type': 'output_text', 'text': RAW}, {'type': 'refusal', 'refusal': 'PRIVATE-REFUSAL'}],
    [{'type': 'output_text', 'text': RAW}, {'type': 'output_text', 'text': RAW}],
    [{'type': 'output_text', 'text': None}],
    [{'type': 'output_text', 'text': RAW, 'function_call': {'name': 'forbidden'}}],
])
def test_refusal_fragments_and_multiple_text_parts_cannot_be_hidden(parts):
    with pytest.raises(ValueError) as error:
        parse_response(response(output=[message(content=parts)]))
    assert 'PRIVATE' not in str(error.value)


@pytest.mark.parametrize('usage', [
    None, [], {}, {'input_tokens': 1}, {'output_tokens': 1},
    {'input_tokens': True, 'output_tokens': 1},
    {'input_tokens': 1, 'output_tokens': '2'},
    {'input_tokens': -1, 'output_tokens': 1},
    {'input_tokens': 1.5, 'output_tokens': 1},
    {'input_tokens': 1, 'output_tokens': float('nan')},
    {'input_tokens': 2**63, 'output_tokens': 1},
    {'input_tokens': 1, 'output_tokens': 2, 'total_tokens': 4},
    {'input_tokens': 1, 'output_tokens': 2, 'total_tokens': True},
    {'input_tokens': 1, 'output_tokens': 2, 'input_tokens_details': None},
    {'input_tokens': 1, 'output_tokens': 2, 'input_tokens_details': {'cached_tokens': 2}},
    {'input_tokens': 1, 'output_tokens': 2, 'output_tokens_details': {'reasoning_tokens': 3}},
])
def test_missing_or_malformed_usage_is_never_silently_counted_as_zero(usage):
    with pytest.raises(ValueError, match='token usage'):
        parse_response(response(usage=usage))


@pytest.mark.parametrize('text', [
    '', ' ', 'PRIVATE-NOT-JSON', '```json\n{}\n```', '{} {}',
    '[]', 'null', 'false', '123', '"PRIVATE-SCALAR"',
    '{"summary":"first","summary":"PRIVATE-SECOND"}',
    '{"nested":{"value":1,"value":2}}', '{"value":NaN}', '{"value":Infinity}',
    '{"value":1e10000}',
])
def test_final_output_must_be_one_unambiguous_strict_json_object(text):
    with pytest.raises(ValueError) as error:
        parse_response(response(output=[message(text)]))
    assert 'PRIVATE' not in str(error.value)


@pytest.mark.parametrize('reasoning', [
    {'type': 'reasoning', 'summary': None},
    {'type': 'reasoning', 'summary': [{'type': 'function_call', 'text': 'PRIVATE'}]},
    {'type': 'reasoning', 'summary': [], 'tool_calls': []},
    {'type': 'reasoning', 'summary': [], 'status': 'incomplete'},
    {'type': 'reasoning', 'summary': [], 'encrypted_content': {}},
])
def test_only_valid_reasoning_items_are_ignored(reasoning):
    with pytest.raises(ValueError) as error:
        parse_response(response(output=[reasoning, message()]))
    assert 'PRIVATE' not in str(error.value)


def test_output_and_reasoning_size_limits_are_enforced():
    with pytest.raises(ValueError):
        parse_response(response(output=[message('{"summary":"' + 'x' * MAX_RESPONSE_CHARS + '"}')]))
    with pytest.raises(ValueError):
        parse_response(response(output=[{'type': 'reasoning', 'summary': [],
                                         'encrypted_content': 'x' * (MAX_RESPONSE_CHARS + 1)}, message()]))
    with pytest.raises(ValueError):
        parse_response(response(output=[{'type': 'reasoning', 'summary': []}] * MAX_OUTPUT_ITEMS + [message()]))
    assert parse_response(response(output=[message(' \n' + RAW + '\n ')]))[0] == ' \n' + RAW + '\n '


def test_deeply_nested_or_malformed_optional_metadata_fails_with_static_errors():
    with pytest.raises(ValueError, match='final JSON'):
        parse_response(response(output=[message('{"nested":' + '[' * 2000 + '0' + ']' * 2000 + '}')]))
    for changes in ({'annotations': [{'type': {}}]}, {'logprobs': 'PRIVATE-METADATA'}):
        part = {'type': 'output_text', 'text': RAW, **changes}
        with pytest.raises(ValueError) as error:
            parse_response(response(output=[message(content=[part])]))
        assert 'PRIVATE' not in str(error.value)


def test_json_string_that_mentions_tools_stays_data():
    raw = json.dumps({'summary': 'Пример function_call и mcp_call — текст, не вызов.', 'actions': []},
                     ensure_ascii=False)
    assert parse_response(response(output=[message(raw)]))[0] == raw
