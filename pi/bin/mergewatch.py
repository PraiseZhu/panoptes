#!/usr/bin/env python3
"""mergewatch — merge -> test within ~1 minute (zero model, zero REST quota when idle).

Cron: `* * * * * box /usr/bin/python3 /workspace/panoptes/pi/bin/mergewatch.py` (own quiet log, not via run.sh:
it must run under low memory too and would otherwise write two log lines a minute).

Every tick: `git ls-remote` the main head of xindong/mivo-canvas-plugin (git protocol, no REST quota). When it moved,
mark dirty; while dirty and no catch-up holds catch-up.lock, launch panoptes' own gap filler in event mode, detached:
  run-nightly.sh --catch-up --trigger event --wait-lock 14400
It tests merged PRs without a matching reports/merged/PR-N.json, skips PRs already running / queued, retries 3x,
so reports get run.trigger=event exactly as the old Grok on-merge routine wrote them. A merge that lands while a
catch-up is running keeps dirty and is picked up on the first tick after that catch-up ends. The 2-hourly
panoptes-catch-up cron stays as the safety net (box down, network out).

Gate: runs only when /workspace/fleet-mode is `pi` (the Grok on-merge routine owns `grok`).
Alerts: none per tick. state.json carries last_check (heartbeat) and fail_streak; boxcheck/boxhealth alert on
staleness or a fail streak >= 15, once per state change.
--dry-run: print the decision, change nothing.
"""
import fcntl, json, os, subprocess, sys, time

REPO_URL = 'https://github.com/xindong/mivo-canvas-plugin'
BASE = os.environ.get('PI_BASE') or os.environ.get('PI_STATE') or '/workspace/mivo-qa/pi'   # state root (lib.PI_STATE; no lib import here)
STATE_DIR = os.path.join(BASE, 'out', 'mergewatch')
STATE = os.path.join(STATE_DIR, 'state.json')
QA_HOME = '/workspace/mivo-qa'
CATCHUP_LOCK = os.path.join(QA_HOME, 'argus-root', 'catch-up.lock')
RUN_NIGHTLY = '/workspace/panoptes/run-nightly.sh'
LAUNCH_LOG = os.path.join(QA_HOME, 'argus-root', 'logs', 'mergewatch-catch-up.log')
DRY = '--dry-run' in sys.argv


def now():
    return time.strftime('%Y-%m-%d %H:%M:%S %Z')


def log(msg):
    line = f'{now()} {msg}'
    if DRY:
        print(line)
        return
    os.makedirs(os.path.join(BASE, 'out', 'logs'), exist_ok=True)
    with open(os.path.join(BASE, 'out', 'logs', f"mergewatch-{time.strftime('%F')}.log"), 'a') as f:
        f.write(line + '\n')


def load():
    try:
        with open(STATE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save(st):
    if DRY:
        return
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = STATE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(st, f, indent=1, ensure_ascii=False)
    os.replace(tmp, STATE)


def catchup_running():
    try:
        fd = os.open(CATCHUP_LOCK, os.O_RDONLY | os.O_CREAT, 0o644)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    except BlockingIOError:
        return True
    finally:
        os.close(fd)

def heartbeat(mode):
    """outage.beat()：每分钟心跳；发现停机窗口就记日志，pause 且 pi 模式时后台补跑错过的日作业（rollback 只报告）。"""
    if DRY:
        return None
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import outage
        w = outage.beat()
    except Exception as e:  # 心跳坏了不能挡合并即测
        log(f'heartbeat error: {type(e).__name__}: {e}')
        return None
    if w:
        log(f"outage {w['kind']} {w['start']} -> {w['end']} ({w['gap_min']} min; {', '.join(w['why'])})")
        if w['kind'] == 'pause' and mode == 'pi':
            log(f'replay spawned pid {outage.spawn_replay(w)}')
    return w


def main():
    os.environ['TZ'] = 'Asia/Shanghai'
    time.tzset()
    mode = os.environ.get('PI_FLEET_MODE') or open('/workspace/fleet-mode').read().strip()
    outage_w = heartbeat(mode)
    if mode != 'pi':
        return 0
    # one tick at a time
    lk = open(f'/tmp/pi-fallback-mergewatch.lock', 'w')
    try:
        fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return 0

    st = load()
    st['last_check'] = now()
    if outage_w and outage_w['kind'] == 'pause':
        st['dirty'] = True   # 停机期间的合并也要补测：catch-up 会测所有还没报告的合并 PR
    try:
        st.pop('auth', None)   # 10-26: praiseautomation App removed (belongs to another account); default git credentials only
        out = subprocess.run(['git', 'ls-remote', REPO_URL, 'refs/heads/main'], capture_output=True, text=True,
                             timeout=25, env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'})
        sha = out.stdout.split()[0] if out.returncode == 0 and out.stdout.strip() else ''
    except subprocess.TimeoutExpired:
        sha = ''
    if not sha:
        st['fail_streak'] = st.get('fail_streak', 0) + 1
        if st['fail_streak'] in (1, 15) or st['fail_streak'] % 60 == 0:
            log(f"ls-remote failed (streak {st['fail_streak']})")
        save(st)
        return 0
    if st.get('fail_streak'):
        log(f"ls-remote recovered after {st['fail_streak']} failed ticks")
    st['fail_streak'] = 0

    if sha != st.get('main_sha'):
        if st.get('main_sha'):
            log(f"main moved {st['main_sha'][:8]} -> {sha[:8]}")
        else:
            log(f'first run, main at {sha[:8]}')
        st['main_sha'] = sha
        st['main_moved_at'] = now()
        st['dirty'] = True

    if st.get('dirty'):
        if catchup_running():
            st['waiting_since'] = st.get('waiting_since') or now()
        else:
            cmd = [RUN_NIGHTLY, '--catch-up', '--trigger', 'event', '--wait-lock', '14400']
            if DRY:
                log('DRY: would launch ' + ' '.join(cmd))
            else:
                with open(LAUNCH_LOG, 'a') as lf:
                    lf.write(f'=== {now()} mergewatch launch for main {sha[:8]}\n')
                    lf.flush()
                    p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=lf, stderr=subprocess.STDOUT,
                                         start_new_session=True, cwd='/workspace/panoptes',
                                         env={**os.environ, 'QA_HOME': QA_HOME,
                                              'ARGUS_PR_TIMEOUT_MIN': os.environ.get('ARGUS_PR_TIMEOUT_MIN', '45'),  # one stuck PR must not block the queue for hours (longest real run 10-21..23: 32 min); catch-up retries a timed-out PR
                                              'PATH': '/usr/local/bin:/usr/bin:/bin'})
                log(f'launched catch-up (event) pid {p.pid} for main {sha[:8]}')
                st['last_launch'] = {'at': now(), 'pid': p.pid, 'sha': sha}
            st['dirty'] = False
            st.pop('waiting_since', None)
    save(st)
    return 0


if __name__ == '__main__':
    sys.exit(main())
