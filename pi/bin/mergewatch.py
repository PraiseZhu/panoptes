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

Queue-jump (10-26, goal merge→test-start p95 <= 2 min): when main moved and the praiseautomation read-only App token is
available (GITHUB_TOKEN, original credentials), list recently closed PRs (REST, read only) and launch one detached
  run-nightly.sh --priority --pr N --date <merge day> --write-merged --trigger event ...
per fresh merge without a report (not already running). It does not wait for catch-up.lock or the sequential queue: it
takes the single QA slot as soon as it is free, or while the slot holder is parked between PRs (scripts/qa-slot.sh).
The catch-up launch below stays as the fallback for anything missed.

Re-enqueue (10-29, #1340 waited 328 min): a priority run that ends without reports/merged/PR-N.json (failed, timed out,
or exit 76 from the memory gate) is relaunched on the next tick instead of waiting for the 2-hourly catch-up.
state.json inflight {PR: {day, tries}} keeps it idempotent (never relaunched while running / queued, dropped once the
report exists) and bounded (MAX_TRIES launches, then left to catch-up).

Gate: runs only when /workspace/fleet-mode is `pi` (the Grok on-merge routine owns `grok`).
Alerts: none per tick. state.json carries last_check (heartbeat) and fail_streak; boxcheck/boxhealth alert on
staleness or a fail streak >= 15, once per state change.
--dry-run: print the decision, change nothing.
"""
import datetime as dt, fcntl, json, os, subprocess, sys, time, urllib.request

REPO_URL = 'https://github.com/xindong/mivo-canvas-plugin'
BASE = os.environ.get('PI_BASE') or os.environ.get('PI_STATE') or '/workspace/mivo-qa/pi'   # state root (lib.PI_STATE; no lib import here)
STATE_DIR = os.path.join(BASE, 'out', 'mergewatch')
STATE = os.path.join(STATE_DIR, 'state.json')
QA_HOME = '/workspace/mivo-qa'
CATCHUP_LOCK = os.path.join(QA_HOME, 'argus-root', 'catch-up.lock')
RUN_NIGHTLY = '/workspace/panoptes/run-nightly.sh'
LAUNCH_LOG = os.path.join(QA_HOME, 'argus-root', 'logs', 'mergewatch-catch-up.log')
REPORTS = os.path.join(QA_HOME, 'reports')
API_PULLS = 'https://api.github.com/repos/xindong/mivo-canvas-plugin/pulls?state=closed&sort=updated&direction=desc&per_page=30'
DRY = '--dry-run' in sys.argv
MAX_TRIES = 3   # same bound as catch-up (retries 3x)


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


def gh_token():
    """原凭证：GITHUB_TOKEN（环境变量，或 ~/.config/mivo-qa/secrets.env）；只用来读 PR 列表。不打印。"""
    t = os.environ.get('GITHUB_TOKEN')
    if t:
        return t
    try:
        for line in open(os.path.expanduser('~/.config/mivo-qa/secrets.env')):
            if line.startswith('GITHUB_TOKEN='):
                return line.split('=', 1)[1].strip().strip('"\'') or None
    except OSError:
        pass
    return None


def fetch_pulls(tok):
    """只读：最近更新的 30 个已关闭 PR（原 GITHUB_TOKEN，只读接口）。"""
    req = urllib.request.Request(API_PULLS, headers={'Authorization': f'Bearer {tok}', 'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def fresh_merges(pulls, since_iso, reports=REPORTS):
    """since_iso 之后合并、还没有 reports/merged/PR-N.json 的 PR：[(number, 上海合并日)]，旧的在前。"""
    since = dt.datetime.fromisoformat(since_iso.replace('Z', '+00:00'))
    out = []
    for p in pulls or []:
        m = p.get('merged_at')
        if not m or (p.get('base') or {}).get('ref', 'main') != 'main':
            continue
        t = dt.datetime.fromisoformat(m.replace('Z', '+00:00'))
        if t <= since or os.path.exists(os.path.join(reports, 'merged', f"PR-{p['number']}.json")):
            continue
        day = t.astimezone(dt.timezone(dt.timedelta(hours=8))).strftime('%Y-%m-%d')
        out.append((t, p['number'], day))
    return [(n, d) for _, n, d in sorted(out)]


def running_prs(proc='/proc'):
    """正在跑 / 排队的 run-nightly.sh --pr N（和 catchup.mjs 的 /proc 扫描同口径），避免重复起。"""
    out = set()
    for d in os.listdir(proc):
        if not d.isdigit():
            continue
        try:
            argv = open(f'{proc}/{d}/cmdline', 'rb').read().decode(errors='replace').split('\0')
        except OSError:
            continue
        if any(a.endswith('run-nightly.sh') for a in argv) and '--pr' in argv:
            i = argv.index('--pr')
            if i + 1 < len(argv) and argv[i + 1].isdigit():
                out.add(int(argv[i + 1]))
    return out


def requeue(inflight, busy, reports=REPORTS):
    """inflight {"N": {day, tries}} 里已结束但没报告的 PR → [(n, day)] 立刻重排；有报告的删掉，tries 用完的交给 catch-up。原地改 inflight。"""
    out = []
    for k in sorted(inflight, key=int):
        n, e = int(k), inflight[k]
        if os.path.exists(os.path.join(reports, 'merged', f'PR-{n}.json')) or e.get('tries', 0) >= MAX_TRIES:
            del inflight[k]
        elif n not in busy:
            out.append((n, e['day']))
    return out


def priority_cmd(number, day):
    """和补测子运行同一套参数（catchup.mjs catchUpChildArgs），trigger event，加 --priority 插队。"""
    return [RUN_NIGHTLY, '--priority', '--pr', str(number), '--date', day, '--write-merged', '--wait-lock', '14400',
            '--no-issues', '--trigger', 'event', '--out', os.path.join(REPORTS, 'catch-up', f'PR-{number}.json')]


def launch(cmd, sha):
    with open(LAUNCH_LOG, 'a') as lf:
        lf.write(f'=== {now()} mergewatch launch for main {sha[:8]}: {" ".join(cmd[1:])}\n')
        lf.flush()
        return subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=lf, stderr=subprocess.STDOUT,
                                start_new_session=True, cwd='/workspace/panoptes',
                                env={**os.environ, 'QA_HOME': QA_HOME,
                                     'ARGUS_PR_TIMEOUT_MIN': os.environ.get('ARGUS_PR_TIMEOUT_MIN', '90'),  # one stuck PR must not block the queue for hours; 10-28 Praise raised 45 -> 90 because low-memory degraded runs (#1340, #1385) exceed 45 min; mergewatch requeue() relaunches a timed-out PR
                                     'PATH': '/usr/local/bin:/usr/bin:/bin'})


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
    # 10-27：停机恢复不再单独置 dirty（同一个 main sha 每次恢复都重起一次 catch-up，10-27 03:46–09:18 起了 6 次、都无事可做）。
    # 停机期间有合并 → main sha 变了 → 下面照样置 dirty；失败的插队运行由下面 requeue 立刻重排，漏测的由 2 小时一次的 catch-up cron 兜底。
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

    inflight = st.setdefault('inflight', {})
    busy = running_prs()

    def go(n, day):
        cmd = priority_cmd(n, day)
        if DRY:
            return log('DRY: would launch ' + ' '.join(cmd))
        p = launch(cmd, sha)
        e = inflight.setdefault(str(n), {'day': day, 'tries': 0})
        e['tries'] += 1
        busy.add(n)
        log(f"launched priority run pid {p.pid} for PR #{n} (main {sha[:8]}, try {e['tries']}/{MAX_TRIES})")

    # 失败 / 超时 / 内存门跳过（exit 76）的插队运行：不等 2 小时一次的 catch-up，这一轮就重排
    for n, day in requeue(inflight, busy):
        go(n, day)

    tok = gh_token() if st.get('dirty') else None
    if st.get('dirty') and tok:
        # 合并即测插队：每个新合并的 PR 单独起一个 --priority 运行（不等 catch-up.lock / 顺序队列，槽位上限 1，见 scripts/qa-slot.sh）
        since = st.get('merged_seen') or (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2)).isoformat()
        try:
            pulls = fetch_pulls(tok)
            for n, day in fresh_merges(pulls, since):
                if n not in busy and str(n) not in inflight:
                    go(n, day)
            ms = [p['merged_at'] for p in pulls if p.get('merged_at')]
            if ms:
                st['merged_seen'] = max([since] + [m.replace('Z', '+00:00') for m in ms])
        except Exception as e:
            log(f'priority launch skipped ({type(e).__name__}); catch-up still covers it')
    if st.get('dirty'):
        # 补测兜底：漏掉的、失败的 PR（插队运行在跑的，catch-up 扫 /proc 会跳过）
        if catchup_running():
            st['waiting_since'] = st.get('waiting_since') or now()
        else:
            cmd = [RUN_NIGHTLY, '--catch-up', '--trigger', 'event', '--wait-lock', '14400']
            if DRY:
                log('DRY: would launch ' + ' '.join(cmd))
            else:
                p = launch(cmd, sha)
                log(f'launched catch-up (event) pid {p.pid} for main {sha[:8]}')
                st['last_launch'] = {'at': now(), 'pid': p.pid, 'sha': sha}
            st['dirty'] = False
            st.pop('waiting_since', None)
    save(st)
    return 0


if __name__ == '__main__':
    sys.exit(main())
