"""Local bounded history serialization, never provider-stored conversation state."""
import json


def validated_history(history):
    if not isinstance(history, list) or len(history) > 50 or len(history) % 2:
        raise ValueError('Invalid provider dialogue history')
    result = []
    size = 0
    for index, turn in enumerate(history):
        if (not isinstance(turn, dict) or set(turn) != {'role', 'content'}
                or turn['role'] != ('user' if index % 2 == 0 else 'assistant')
                or not isinstance(turn['content'], str) or not turn['content'].strip()):
            raise ValueError('Invalid provider dialogue history')
        size += len(turn['content'])
        if size > 60000:
            raise ValueError('Provider dialogue history exceeds its bound')
        result.append({'role': turn['role'], 'content': turn['content']})
    return result


def model_input(context):
    """Preserve current context, moving already authorized turns to message roles."""
    history = []
    current = context
    if context.get('intelligence') is True and 'dialogue' in context:
        dialogue = context['dialogue']
        if not isinstance(dialogue, dict):
            raise ValueError('Invalid model dialogue context')
        if 'turns' in dialogue:
            history = validated_history(dialogue['turns'])
            current = dict(context, dialogue=dict(dialogue, turns=[]))
    return json.dumps(current, ensure_ascii=False), history
