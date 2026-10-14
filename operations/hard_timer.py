"""Finite CLI supervision. Dry by default; does not grant authorization."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def supervise(argv, seconds, grace=90):
    if type(seconds) not in (int, float) or not 0 < seconds <= 300:
        raise ValueError('Window must be positive and at most 300 seconds')
    if type(grace) not in (int, float) or not 0 < grace <= 90:
        raise ValueError('Grace must be positive and at most 90 seconds')
    interrupted = [False]
    signals = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
    previous = {s: signal.getsignal(s) for s in signals}
    child = None
    forced = False
    reason = 'exited'
    def stop(*_):
        interrupted[0] = True
    try:
        for s in signals:
            signal.signal(s, stop)
        deadline = time.monotonic() + seconds
        child = subprocess.Popen(argv, start_new_session=True)
        while child.poll() is None:
            remaining = deadline - time.monotonic()
            if interrupted[0] or remaining <= 0:
                reason = 'interrupted' if interrupted[0] else 'window-ended'
                try:
                    os.killpg(child.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    child.wait(timeout=grace)
                except subprocess.TimeoutExpired:
                    forced = True
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait()
                break
            try:
                child.wait(timeout=min(remaining, .2))
            except subprocess.TimeoutExpired:
                pass
        return {'pid': child.pid, 'returncode': child.returncode,
                'reason': reason, 'forced': forced}
    finally:
        if child is not None and child.poll() is None:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait()
        for s, handler in previous.items():
            signal.signal(s, handler)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--config', required=True)
    p.add_argument('--db', required=True)
    p.add_argument('--execute', action='store_true')
    args = p.parse_args()
    c = json.loads(Path(args.config).read_text())
    guard = c['controlled_intake']
    start, end = guard['started_at'], guard['ends_at']
    if type(start) is not int or type(end) is not int or not 0 < end-start <= 300:
        raise ValueError('Invalid test window')
    directory = Path('/data/tests') / str(start)
    if (Path(args.config).resolve() != directory/'config.json'
            or Path(args.db).resolve() != directory/'test.sqlite'):
        raise ValueError('Use isolated test paths')
    if any(c.get(k) is not False for k in ('live_send','sheets_write','daily_summary',
                  'allow_onboarding_replies','allow_registered_replies')):
        raise ValueError('Test sending must stay disabled')
    if not args.execute:
        print('Dry preparation only; no process started. --execute requires separate user authorization.')
        return
    if os.geteuid() == 0:
        os.setgroups([]); os.setgid(10001); os.setuid(10001)
    if os.geteuid() != 10001 or os.getegid() != 10001:
        raise ValueError('Test must run under UID/GID 10001')
    now = time.time()
    if not start <= now < end:
        raise ValueError('Test window inactive')
    seconds = end-now
    argv = [sys.executable, '-m', 'goldway.cli', '--config', args.config,
            '--db', args.db, 'run', '--run-seconds', str(int(seconds))]
    if int(seconds) <= 0:
        raise ValueError('Insufficient remaining time')
    result = supervise(argv, seconds)
    print(json.dumps(result))
    if result['forced'] or result['returncode'] != 0:
        raise SystemExit(1)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, OSError) as error:
        raise SystemExit('Test supervision failed: '+type(error).__name__) from None
