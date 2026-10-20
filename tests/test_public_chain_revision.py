"""A new observed-defect revision cannot replay old activation or old marker."""
import goldway.public_chain_execution as launch


def test_old_activation_is_inert_before_any_profile_or_marker_io(monkeypatch):
    monkeypatch.setenv('GOLDWAY_PUBLIC_CHAIN_RUN','20261020-a')
    def forbidden(*args):raise AssertionError('old activation must be inert')
    monkeypatch.setattr(launch,'_profile',forbidden)
    monkeypatch.setattr(launch,'_consume',forbidden)
    assert launch.maybe_start({}) is False


def test_distinct_marker_retains_consumed_previous_attempt(monkeypatch,tmp_path):
    assert launch.RUN=='20261020-b'
    old=tmp_path/'20261020-a.started';old.write_text('retained')
    marker=tmp_path/(launch.RUN+'.started')
    monkeypatch.setattr(launch,'RUN_DIRECTORY',tmp_path)
    monkeypatch.setattr(launch,'MARKER',marker)
    launch._consume()
    assert old.read_text()=='retained'
    assert marker.read_text()=='goldway-public-chain-v1 20261020-b started\n'
    assert marker.stat().st_mode & 0o777==0o600
