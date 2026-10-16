"""Payload-free event diagnostics. Only exact, source-owned messages get codes."""

# Never use substring matching or emit exception text: provider/parser errors may
# contain credentials or the original employee message.
_VALUE_ERRORS = {
    'Daily model budget reached': 'model_budget',
    'Model context limit exceeded; split transcript explicitly': 'model_context',
    'Model output truncated': 'model_truncated',
    'Invalid intelligence result': 'invalid_intelligence',
    'Missing dialogue state': 'dialogue_state',
    'Dialogue state must be an object': 'dialogue_state',
    'Invalid dialogue topic': 'dialogue_state',
    'Invalid dialogue task_id': 'dialogue_state',
    'Invalid dialogue entities': 'dialogue_state',
    'Invalid dialogue pending offer': 'dialogue_state',
    'Unpermitted dialogue task focus': 'dialogue_state',
    'Invalid knowledge citations': 'knowledge_citations',
    'Knowledge permission expired before reply': 'knowledge_permission',
    'Work evidence changed before reply': 'work_evidence_changed',
    'Grounded answer requires evidence': 'knowledge_citations',
    'Assumptions cannot ground corporate facts': 'knowledge_citations',
    'Ungrounded answer cannot cite evidence': 'knowledge_citations',
    'Citation budget exceeded': 'citation_budget',
    'Knowledge provenance budget exceeded': 'provenance_budget',
}
for _message in (
    'Invalid action list', 'Invalid summary', 'Invalid action',
    'Invalid update mask', 'Invalid source line reference', 'Invalid operation',
    'Unknown action fields', 'Missing literal evidence', 'Invalid text',
    'Invalid status', 'Unknown owner', 'Owner not grounded in text',
    'Invalid deadline', 'Deadline not grounded in text',
    'Use explicit ISO deadline, clarify relative dates', 'Incomplete task',
    'Empty title', 'New task must be confirmed or proposed',
    'Task outside permitted context', 'Completion requires concrete result',
    'General conversation cannot modify tasks',
    'Operator chat requires an explicit intent', 'Conversation intent cannot modify tasks',
    'Conversation requires a direct reply_text', 'Replies cannot create tasks',
    'Employee cannot reassign', 'Only admin may confirm proposal',
    'Accepting an explanation is not task consent', 'User explicitly declined task creation',
    'Reminder request cannot silently change an assignment',
):
    _VALUE_ERRORS[_message] = 'task_validation'

_HISTORY_CODE = 'ValueError: task_history_unavailable'
_SAFE_CODES = frozenset('ValueError: ' + code for code in _VALUE_ERRORS.values()) | {_HISTORY_CODE}


def event_error(exc):
    """Classify exact source-owned errors; unknown exceptions expose class only."""
    if type(exc) is ValueError and len(exc.args) == 1 and type(exc.args[0]) is str:
        code = _VALUE_ERRORS.get(exc.args[0])
        if code:
            return 'ValueError: ' + code
    # Lazy import keeps errors independent of the Engine/history import chain.
    from .task_transition_history import HistoryError
    if (type(exc) is HistoryError and len(exc.args) == 1
            and type(exc.args[0]) is str and exc.args[0] == 'task_history_unavailable'):
        return _HISTORY_CODE
    return type(exc).__name__


def event_log_error(stored, exc):
    """Use an engine code without ever forwarding legacy stored free text."""
    return stored if stored in _SAFE_CODES else event_error(exc)
