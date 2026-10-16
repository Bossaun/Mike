"""Event diagnostics stay actionable without copying messages or credentials."""
import pytest

from goldway.cli import pending
from goldway.errors import event_error


@pytest.mark.parametrize(('message', 'code'), [
    ('Daily model budget reached', 'model_budget'),
    ('Model context limit exceeded; split transcript explicitly', 'model_context'),
    ('Model output truncated', 'model_truncated'),
    ('Invalid intelligence result', 'invalid_intelligence'),
    ('Missing dialogue state', 'dialogue_state'),
    ('Invalid dialogue pending offer', 'dialogue_state'),
    ('Invalid knowledge citations', 'knowledge_citations'),
    ('Knowledge permission expired before reply', 'knowledge_permission'),
    ('Citation budget exceeded', 'citation_budget'),
    ('Invalid action list', 'task_validation'),
    ('Missing literal evidence', 'task_validation'),
])
def test_pending_retains_safe_engine_category(env, monkeypatch, capsys, message, code):
    e, s, *_ = env
    event = e.transcript('Synthetic employee transcript')

    def fail(*args, **kwargs):
        raise ValueError(message)

    monkeypatch.setattr(e.model, 'propose', fail)
    pending(e)
    row = s.execute('SELECT state,error FROM events WHERE id=?', (event,)).fetchone()
    assert dict(row) == {'state': 'failed', 'error': 'ValueError: ' + code}
    assert 'ValueError: ' + code in capsys.readouterr().err
    assert not e.tasks() and not s.rows('SELECT * FROM outbox')


@pytest.mark.parametrize('exception_type', [ValueError, RuntimeError])
def test_unknown_exception_secret_never_persisted_or_logged(env, monkeypatch, capsys, exception_type):
    e, s, *_ = env
    event = e.transcript('Synthetic employee transcript')
    secret = 'SYNTHETIC_KEY_must_never_be_in_diagnostics'

    def fail(*args, **kwargs):
        raise exception_type('Invalid action list ' + secret)

    monkeypatch.setattr(e.model, 'propose', fail)
    pending(e)
    row = s.execute('SELECT state,error FROM events WHERE id=?', (event,)).fetchone()
    assert dict(row) == {'state': 'failed', 'error': exception_type.__name__}
    logs = capsys.readouterr()
    assert secret not in logs.err + logs.out
    assert exception_type.__name__ in logs.err
    # Failed events are not automatically retried by the pending worker.
    monkeypatch.setattr(e.model, 'propose', lambda *a, **k: pytest.fail('automatic replay'))
    pending(e)


def test_cli_preserves_engine_error_after_secondary_failure(env, monkeypatch, capsys):
    e, s, *_ = env
    event = e.transcript('Synthetic employee transcript')

    def fail(event_id):
        s.execute("UPDATE events SET state='failed',error=? WHERE id=?",
                  ('ValueError: model_budget', event_id))
        raise RuntimeError('SYNTHETIC_SECONDARY_SECRET')

    monkeypatch.setattr(e, 'process', fail)
    pending(e)
    assert s.execute('SELECT error FROM events WHERE id=?', (event,)).fetchone()['error'] == 'ValueError: model_budget'
    logs = capsys.readouterr().err
    assert 'ValueError: model_budget' in logs and 'SYNTHETIC_SECONDARY_SECRET' not in logs


def test_cli_records_safe_error_before_engine_handler(env, monkeypatch, capsys):
    e, s, *_ = env
    event = e.transcript('Synthetic employee transcript')

    def fail(event_id):
        raise RuntimeError('SYNTHETIC_EARLY_SECRET')

    monkeypatch.setattr(e, 'process', fail)
    pending(e)
    assert s.execute('SELECT error FROM events WHERE id=?', (event,)).fetchone()['error'] == 'RuntimeError'
    assert 'SYNTHETIC_EARLY_SECRET' not in capsys.readouterr().err


def test_classifier_does_not_call_arbitrary_exception_string():
    class UnsafeError(Exception):
        def __str__(self):
            pytest.fail('exception string evaluated')

    assert event_error(UnsafeError('SYNTHETIC_SECRET')) == 'UnsafeError'


def test_actual_task_validation_has_safe_category(env, capsys):
    e, s, *_ = env
    event = e.transcript('Synthetic transcript')
    e.model.result = {'actions': 'SYNTHETIC_INVALID_ACTION_SECRET'}
    pending(e)
    assert s.execute('SELECT error FROM events WHERE id=?', (event,)).fetchone()['error'] == 'ValueError: task_validation'
    logs = capsys.readouterr().err
    assert 'ValueError: task_validation' in logs
    assert 'SYNTHETIC_INVALID_ACTION_SECRET' not in logs


def test_cli_never_logs_legacy_free_text_error(env, monkeypatch, capsys):
    e, s, *_ = env
    e.transcript('Synthetic transcript')

    def fail(event_id):
        s.execute("UPDATE events SET state='failed',error=? WHERE id=?",
                  ('ValueError: SYNTHETIC_LEGACY_SECRET', event_id))
        raise RuntimeError('SYNTHETIC_SECONDARY_SECRET')

    monkeypatch.setattr(e, 'process', fail)
    pending(e)
    logs = capsys.readouterr().err
    assert 'RuntimeError' in logs
    assert 'SYNTHETIC_LEGACY_SECRET' not in logs
    assert 'SYNTHETIC_SECONDARY_SECRET' not in logs
