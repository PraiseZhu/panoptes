#!/usr/bin/env python3
"""电脑运维 box-health takeover (gated by takeover/boxhealth), hourly :17. Equivalent of the paused Grok routine 'box-health':
checks mem/load/top/disk/zombies, cron + /etc/cron.d perms, mem.log freshness, earlyoom --avoid, swap + swappiness;
appends one line to /workspace/box-ops/health.log. Small fixes (sudo -n, idempotent): cron.d chown/chmod, box-ops-ensure.sh
(earlyoom/swap/swappiness), start cron, mv /node_modules -> /node_modules.bak-<ts> (never delete).
PI_BOXHEALTH_DRYRUN=1 / --dry-run -> report fixes and the cleanup list without doing them.
Cleanup (2026-10-24, Praise: "超过 7 天自动删，要真删"): really deletes, every run, what is older than 7 days in the whitelist --
npm / pip caches (per file), apt's downloaded .debs, this user's top-level /tmp entries that nothing touched for 7 days and no
process holds (cwd / root / open fd, as panoptes scripts/sweep-tmp.sh), /tmp/cindy.deb -- and cuts a log over 200 MB in LOGS_CAP to
its last 20 MB. Never the blacklist (Cindy / cindy-launch / CindyGlobal, keyrings, restic, snapshots, box-restore, agent-data). Not
while a QA run holds its lock (flock -n, not "the lock file exists": those files stay for good). QA artifacts (evidence,
step-results, run caches, /tmp/argus-*) are janitor's and panoptes' sweep-tmp.sh's, never this script's.
Time zone (10-24): box-ops-ensure.sh pins America/Los_Angeles every minute and logs a drift to /workspace/box-ops/tz-drift.log;
a new line there -> one DM. box-ops cron (/etc/cron.d/box-ops) does NO cleanup, so cleanup has a single owner: this script."""
from lib import *
import praise_notice as PN
import glob, time
DRY = os.environ.get("PI_BOXHEALTH_DRYRUN") == "1" or "--dry-run" in sys.argv   # 10-24: --dry-run used to be ignored (a "dry" run wrote health.log)
STATE = f"{BASE}/out/box/alert-state.json"
def jload(f, d):
    try: return json.load(open(f))
    except Exception: return d
# zombies whose parent is sandbox desktop/platform infrastructure: Xvfb never reaps its xkbcomp shells; harmless, permanent
PLATFORM = re.compile(r"(^|/)(Xvfb|x11vnc|xfwm4|websockify|picom|plank|bamfdaemon|dbus-daemon|xkbcomp|tini|pod-daemon)( |$)|/exec-daemon/|/usr/local/bin/sand-|/opt/sand/")
def zombies():
    out = []
    for line in subprocess.run(["ps", "-eo", "pid=,ppid=,stat=,etimes=,comm="], capture_output=True, text=True).stdout.splitlines():
        f = line.split(None, 4)
        if len(f) < 5 or not f[2].startswith("Z"): continue
        try: pc = open(f"/proc/{f[1]}/cmdline", "rb").read().replace(b"\0", b" ").decode(errors="replace").strip()
        except OSError: pc = "?"
        out.append({"pid": int(f[0]), "ppid": int(f[1]), "age_s": int(f[3]), "comm": f[4], "parent": pc, "platform": bool(PLATFORM.search(pc))})
    return out
def low_streak(limit=3072, n=30):
    """consecutive most-recent minutes in box-ops mem.log with MemAvailable < limit (one sample per minute)"""
    try: lines = subprocess.run(["tail", "-n", str(n * 10), "/workspace/box-ops/mem.log"], capture_output=True, text=True).stdout.splitlines()
    except Exception: return 0
    av = [int(l.split()[6]) for l in lines if l.startswith("Mem:") and len(l.split()) >= 7]
    k = 0
    for a in reversed(av[-n:]):
        if a >= limit: break
        k += 1
    return k
def sh(c): return subprocess.run(c, shell=True, capture_output=True, text=True, timeout=300).stdout.strip()
def fix(desc, cmd, fixed):
    if DRY: fixed.append("[dry] " + desc); return
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=300)
    fixed.append(desc + ("" if r.returncode == 0 else f" (失败 rc={r.returncode})"))
def du(paths):
    return sum(int(sh(f"du -sb {p} 2>/dev/null | cut -f1") or 0) for p in paths)
KEEP_DAYS = 7
TMP_ROOT = os.environ.get("BOXHEALTH_TMP_ROOT") or "/tmp"          # tests only
TZ_DRIFT_LOG = os.environ.get("BOXHEALTH_TZ_LOG") or "/workspace/box-ops/tz-drift.log"
LOGS_CAP = ["/var/log/earlyoom.log", "/workspace/box-ops/*.log", "/workspace/grokbot-box-check/*.log"]
LOG_MAX, LOG_KEEP = 200 << 20, 20 << 20
BLACK = re.compile(r"(?i)cindy|keyring|restic|snapshot|box-restore|agent-data|^\.x11|^\.ice|^\.font|^tmux|^ssh-|^systemd|^sand|^xdg")
QA_LOCKS = [f"/workspace/mivo-qa/argus-root/{n}" for n in ("nightly.lock", ".nightly.lock", "catch-up.lock", ".catch-up.lock")]
def lock_held(p):
    """a QA lock someone holds right now (flock -n fails) -- the files themselves never go away"""
    import fcntl
    try: f = open(p, "a")
    except OSError: return False
    try: fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB); fcntl.flock(f, fcntl.LOCK_UN); return False
    except BlockingIOError: return True
    finally: f.close()
def held_paths():
    out = set()
    for p in glob.glob("/proc/[0-9]*"):
        for x in [f"{p}/cwd", f"{p}/root", *glob.glob(f"{p}/fd/*")]:
            try: out.add(os.readlink(x))
            except OSError: pass
    return out
def cleanup_candidates():
    """-> ([(path, kind)], running QA locks). kind: file (unlink) / tree (rm -rf) / trunc (cut a log to its tail)"""
    H = os.path.expanduser("~"); c = []
    locks = [l for l in QA_LOCKS if os.path.exists(l) and lock_held(l)]
    for root in (f"{H}/.npm/_cacache", f"{H}/.npm/_npx", f"{H}/.cache/pip"):   # cache entries, one by one: what was used stays
        c += [(p, "file") for p in sh(f"find {root} -type f -mtime +{KEEP_DAYS} 2>/dev/null").splitlines() if p]
    c += [(p, "sudo") for p in sh(f"find /var/cache/apt/archives -maxdepth 1 -name '*.deb' -mtime +{KEEP_DAYS} 2>/dev/null").splitlines() if p]
    if os.path.exists(f"{TMP_ROOT}/cindy.deb"): c.append((f"{TMP_ROOT}/cindy.deb", "file"))
    if not locks:
        held = held_paths(); me = os.getuid()
        for name in sorted(os.listdir(TMP_ROOT)):
            p = f"{TMP_ROOT}/{name}"
            try: stt = os.lstat(p)
            except OSError: continue
            if stt.st_uid != me or BLACK.search(name) or name.startswith("argus-") or name == "cindy.deb": continue
            if any(h == p or h.startswith(p + "/") for h in held): continue
            if sh(f"find '{p}' -mmin -{KEEP_DAYS * 1440} -print -quit 2>/dev/null"): continue    # anything inside touched within 7 days
            c.append((p, "tree" if os.path.isdir(p) and not os.path.islink(p) else "file"))
    for pat in LOGS_CAP:
        for p in glob.glob(pat):
            try:
                if os.path.isfile(p) and os.path.getsize(p) > LOG_MAX: c.append((p, "trunc"))
            except OSError: pass
    return c, locks
def really_clean(cand):
    """really delete / truncate; -> bytes freed (by the disk, not by the list)"""
    before = shutil_free()
    for p, kind in cand:
        if kind == "file": sh(f"rm -f -- '{p}'")
        elif kind == "tree": sh(f"rm -rf --one-file-system -- '{p}'")
        elif kind == "sudo": sh(f"sudo -n rm -f -- '{p}'")
        elif kind == "trunc": sh(f"tail -c {LOG_KEEP} '{p}' > '{p}.cut' && cat '{p}.cut' > '{p}' && rm -f '{p}.cut'")
    for root in (os.path.expanduser("~/.npm/_cacache"), os.path.expanduser("~/.cache/pip")):
        sh(f"find {root} -mindepth 1 -type d -empty -delete 2>/dev/null")
    return max(0, shutil_free() - before)
def shutil_free():
    import shutil
    return shutil.disk_usage("/").free
def tz_drift_event(st, drifts, day):
    """10-24: the platform keeps pushing the device's zone -> at most one DM a day; drifts in between are counted, not lost"""
    st["tz_drift_pending"] = st.get("tz_drift_pending", 0) + len(drifts)
    if drifts: st["tz_drift_last"] = drifts[-1]
    if not st["tz_drift_pending"] or st.get("tz_drift_dm_day") == day: return None
    e = f"时区漂移: {st['tz_drift_pending']} 次，最近一次 " + (st.get("tz_drift_last") or "")
    st["tz_drift_dm_day"] = day; st["tz_drift_pending"] = 0
    return e
def tz_drifts(st):
    """new lines in box-ops-ensure.sh's /workspace/box-ops/tz-drift.log since the last run"""
    try: lines = open(TZ_DRIFT_LOG).read().splitlines()
    except OSError: return []
    seen = st.get("tz_drift_lines", 0); st["tz_drift_lines"] = len(lines)
    return lines[seen:] if len(lines) >= seen else lines
def janitor_summary():
    """10-24: bot work dirs / scratch / old evidence are janitor's (pi/bin/janitor.py); this line only shows its last report"""
    r = jload(f"{BASE}/out/janitor/last.json", None)
    if not r: return "none"
    return (f"{r.get('mode')}@{str(r.get('ran_at', ''))[11:16]} root={(r.get('root_bytes') or 0) >> 20}M trash={r.get('trash_entries', 0)} "
            f"unmarked={len(r.get('unmarked') or [])} errors={len(r.get('errors') or [])}")
def main():
    m = mode(); tk = takeover("boxhealth")   # parity gate: Grok routine runs iff fleet-mode!=pi; Pi runs iff fleet-mode==pi AND marker
    if m != "pi" or not tk:
        if (m == "pi") != tk:
            log(f"WARN gate mismatch: fleet-mode={m} takeover/boxhealth={'present' if tk else 'absent'} -> skip"
                + ("（两线都不跑！请 switch.sh pi）" if m == "pi" else "（Grok 线在管，marker 多余；switch.sh grok 会清掉）"))
        else: log(f"fleet-mode={m}, no marker -> skip (Grok line owns box-health)")
        return 0
    d = today(); t = now_cst(); issues = []; fixed = []; alerts = []; events = []
    avail = int(sh("awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo") or 0)
    la = open("/proc/loadavg").read().split()[:3]; load = " ".join(la); ncpu = os.cpu_count() or 1
    disk = sh("df -h / | awk 'NR==2{print $5\" used, \"$4\" free\"}'")
    top = sh("ps -eo comm,rss --sort=-rss --no-headers | head -5 | awk '{printf \"%s:%dM \", $1, $2/1024}'")
    zl = zombies(); ours = [z for z in zl if not z["platform"]]; zomb = len(zl)
    st = jload(STATE, {}); hist = (st.get("zombie_hist") or [])[-5:] + [len(ours)]; st["zombie_hist"] = hist
    if ours: issues.append(f"非平台僵尸 {len(ours)} 个: " + ", ".join(f'{z["pid"]}<-{z["ppid"]}:{z["parent"][:40]}' for z in ours[:5]))
    if len(hist) >= 2 and min(hist[-2:]) >= 10: alerts.append(("zombies", f"非平台僵尸进程连续 2 次 ≥10 个（现 {len(ours)} 个，父进程见 health.log）"))
    if float(la[2]) > ncpu: alerts.append(("load", f"15 分钟负载 {la[2]} > {ncpu} 核（持续高）"))
    low_min = low_streak()
    if avail < 3072 and low_min >= 5: alerts.append(("mem", f"可用内存持续 {low_min} 分钟 < 3G（现 {avail}MB）"))
    elif avail < 3072 or low_min: issues.append(f"可用内存瞬时低（现 {avail}MB，连续 {low_min} 分钟；多为平台快照回收，不告警）")
    swap_used = int(sh("free -m | awk '/^Swap:/{print $3}'") or 0)
    if swap_used > 4096: alerts.append(("swap", f"swap 已用 {swap_used}MB > 4G（真实内存压力）"))
    for pkg in ("cron", "earlyoom", "logrotate"):   # after a reset: reinstall is in grokbot-box restore.d/10-packages (apt https)
        if sh(f"dpkg-query -W -f='${{Status}}' {pkg} 2>/dev/null").find("installed") < 0: issues.append(f"{pkg} 未安装（需跑 grokbot-box restore.sh）")
    if not sh("pgrep -x cron"): issues.append("cron 未运行"); fix("启动 cron", "sudo -n /usr/sbin/cron", fixed)
    for f in ["box-ops", "panoptes-catch-up", "pi-fallback", "grokbot-box-sync"]:
        p = f"/etc/cron.d/{f}"; s = sh(f"stat -c '%U:%G %a' {p} 2>/dev/null")
        if not s: issues.append(f"{p} 缺")
        elif s != "root:root 644": issues.append(f"{p} {s}"); fix(f"{p} 改回 root:root 644", f"sudo -n chown root:root {p} && sudo -n chmod 644 {p}", fixed)
    age = int(time.time() - os.path.getmtime("/workspace/box-ops/mem.log")) if os.path.exists("/workspace/box-ops/mem.log") else 99999
    if age > 120: issues.append(f"mem.log 已 {age}s 未更新")
    eo = sh("pgrep -a earlyoom"); want = sh(". /etc/default/earlyoom 2>/dev/null; echo \"$EARLYOOM_ARGS\"")
    need_ensure = False
    if not eo or "Cindy" not in eo or "electron" not in eo: issues.append("earlyoom 未运行或 --avoid 未含 Cindy/electron"); need_ensure = not eo
    if "/swapfile" not in open("/proc/swaps").read(): issues.append("swap 缺失"); need_ensure = True
    if sh("cat /proc/sys/vm/swappiness") != "10": issues.append("swappiness≠10"); need_ensure = True
    if need_ensure: fix("运行 box-ops-ensure.sh", "sudo -n /usr/local/sbin/box-ops-ensure.sh", fixed)
    if os.path.isdir("/node_modules") and not os.path.islink("/node_modules"):
        dst = "/node_modules.bak-" + t.strftime("%Y%m%d%H%M")
        issues.append(f"根目录有 /node_modules（{', '.join(sorted(os.listdir('/node_modules')))[:120]}）")
        fix(f"sudo mv /node_modules {dst}（未删除；需通知 Praise PR bot）", f"sudo -n mv /node_modules {dst}", fixed)
        events.append(f"/node_modules 已改名 {dst}，请转告 Praise PR bot" if not DRY else "/node_modules 待改名")
    cand, locks = cleanup_candidates(); cand_b = du([p for p, k in cand if k != "trunc"]) if cand else 0
    clean = not DRY; freed = 0
    if clean and cand:
        freed = really_clean(cand)
        if freed > 500 * 2**20: events.append(f"清理释放 {freed // 2**20}MB > 500MB")
    e = tz_drift_event(st, tz_drifts(st), d)
    if e: events.append(e)
    bad = [f for f in fixed if "失败" in f]
    if bad and not DRY: alerts.append(("fix-failed", "自动修复失败: " + "; ".join(bad)))
    if fixed and not DRY and len(bad) < len(fixed): events.append("已修复: " + "; ".join(f for f in fixed if f not in bad))
    line = (f"{t.strftime('%F %T')} CST [pi-line{' dry' if DRY else ''}] avail={avail}MB low_streak={low_min}min load={load} disk={disk} zombies={zomb}(ours {len(ours)}) top={top.strip()} "
            f"clean={'done freed=' + str(freed // 2**20) + 'MB' if clean else 'dry'} candidates={len(cand)}项/{cand_b // 2**20}M"
            f"{' (测试在跑，/tmp 不清: ' + ','.join(os.path.basename(l) for l in locks) + ' 被占)' if locks else ''} "
            f"janitor={janitor_summary()} "
            f"issues={'; '.join(issues) or 'none'} fixed={'; '.join(fixed) or 'none'}")
    if DRY: print(line); print("candidates:", *(f"{k} {p}" for p, k in cand), sep="\n  ")
    else:
        open("/workspace/box-ops/health.log", "a").write(line + "\n")
        out = f"{BASE}/out/box/{d}.health.md"; old = open(out).read() if os.path.exists(out) else f"# box-health {d}（Pi line）\n"
        write_atomic(out, old + "- " + line + "\n")
    # 2026-10-23: alert only on real problems, once per state change (+ one "recovered"), 24h reminder; issues stay in health.log.
    # Old rule (alerts + issues, new key every hour) DM'd platform noise hourly: Xvfb zombies, snapshot-time memory reclaim.
    # 2026-10-24（Praise：私信看不懂）：按 praise_notice 的骨架写；恢复只告诉他之前要他动手的（自动修复失败）；释放磁盘这类不发
    def alert_item(code, raw):
        if code == "mem": return PN.item("auto", "机器内存一直不够", f"可用内存已经连续 {low_min} 分钟低于 3G（现在 {avail}MB）。",
            "测试和修 PR 会排队等内存，变慢；内存太低时系统可能杀掉占内存最多的进程。", auto="重任务自动排队等内存；24 小时内还没好会再提醒你")
        if code == "swap": return PN.item("auto", "机器内存压力很大", f"机器已经把 {raw.split('已用 ')[-1].split('MB')[0]}MB 内容挪到硬盘上的交换区（超过 4G），说明内存真的不够用了。",
            "所有任务都会变慢。", auto="重任务自动排队等内存；24 小时内还没好会再提醒你")
        if code == "load": return PN.item("auto", "机器一直很忙", f"最近 15 分钟机器的负载（{la[2]}）超过了 CPU 核数（{ncpu}），一直满负荷。",
            "测试会变慢。", auto="任务会自然排队；24 小时内还没好会再提醒你")
        if code == "zombies": return PN.item("fyi", "机器上残留的死进程变多了", f"我们自己的脚本留下了 {len(ours)} 个已经结束但没被回收的进程（连续两次都超过 10 个）。",
            "暂时不影响使用，但说明有脚本没收拾干净，越积越多会占资源。", todo="不急，有空时" + PN.ask_session("box-health 报非平台僵尸进程 ≥10 个，看 /workspace/box-ops/health.log 里的父进程，找出是哪个脚本没回收子进程"))
        return PN.item("action", "机器体检的自动修复失败了", f"机器体检发现了问题、自己去修，但修没成功：{raw.split(': ', 1)[-1][:120]}。",
            "这个问题会一直在。", todo=PN.ask_session(f"box-health {raw[:150]}，看 /workspace/box-ops/health.log，手动修好"))
    sig = ",".join(sorted(c for c, _ in alerts)); prev = st.get("sig", ""); items = []
    if sig and (sig != prev or t.timestamp() - st.get("sent_at", 0) > 86400):
        items = [alert_item(c, x) for c, x in alerts]
        st.update(sig=sig, since=st.get("since") if sig == prev else t.isoformat(timespec="seconds"), sent_at=t.timestamp())
    elif not sig and prev:
        if "fix-failed" in prev.split(","):
            items = [PN.item("resolved", "机器体检的自动修复失败了", f"之前告诉你的自动修复失败（从 {str(st.get('since') or '?')[11:16]} 开始），现在好了。")]
        st.update(sig="", since=None, sent_at=0)
    for e in events:
        if e.startswith("已修复: "): items.append(PN.item("fyi", "机器体检自己修好了一处问题", f"发现并修好了：{e[len('已修复: '):][:150]}。"))
        elif e.startswith("清理释放 "): items.append(PN.item("fyi", "机器清理释放了不少空间", f"按「超过 7 天自动删」{e[len('清理释放 '):].split(' >')[0]}。",
            "不影响使用。"))
        elif e.startswith("时区漂移: "): items.append(PN.item("auto", "系统时区被改了，已经改回洛杉矶", f"自上次告诉你以来，有程序把机器时区改了 {e.split('时区漂移: ')[1].split(' 次')[0]} 次（最近一次改成 {e.split('localtime=')[-1].split(' ')[0]}），每分钟的自检都已经改回 America/Los_Angeles。",
            "不影响 Pi 线（它按北京时间出日报，不看系统时区）。", auto="每分钟自动检查并改回，这条私信一天最多一次；记录在 /workspace/box-ops/tz-drift.log。要根治：在 Cindy / 平台设置里把这台机器的时区设成洛杉矶"))
        elif "/node_modules" in e and "改名" in e: items.append(PN.item("fyi", "机器根目录多出来的 node_modules 被挪开了",
            f"机器根目录（/）下出现了一个 node_modules 文件夹，会干扰测试，已经改名挪开（没删）：{e.split('已改名 ')[-1].split('，')[0]}。",
            "如果修 PR bot 正好在用它，可能要重装依赖。", todo="告诉修测试的会话一声，让修 PR bot 那边知道这件事"))
    msg = PN.text(*items)
    if not DRY: write_atomic(STATE, json.dumps(st, ensure_ascii=False, indent=1))
    if DRY: print("would send:", msg)
    elif msg: record_actions("box", d, [{"key": f"boxhealth:{d}:{t.strftime('%H%M')}", "type": "slack.dm", "to": "Praise", "text": msg}])
if __name__ == "__main__": sys.exit(main())
