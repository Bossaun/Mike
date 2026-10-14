import signal
import sys
import time
import pytest
from hard_timer import supervise


def test_clean_exit():
    result=supervise([sys.executable,'-c','pass'],1,grace=.3)
    assert result['returncode']==0 and result['reason']=='exited' and not result['forced']


def test_timeout_stops_process():
    result=supervise([sys.executable,'-c','import time;time.sleep(30)'],.2,grace=.3)
    assert result['returncode']==-signal.SIGTERM and result['reason']=='window-ended'
    assert not result['forced']


def test_ignoring_term_is_killed():
    started=time.monotonic()
    result=supervise([sys.executable,'-c','import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(30)'],.3,grace=.2)
    assert result['returncode']==-signal.SIGKILL and result['forced']
    assert time.monotonic()-started<3


@pytest.mark.parametrize('seconds',[0,-1,301,True])
def test_invalid_window_never_starts(seconds):
    with pytest.raises(ValueError):supervise(['never-run'],seconds)
