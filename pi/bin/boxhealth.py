#!/usr/bin/env python3
"""电脑运维 box-health takeover (gated by takeover/boxhealth), hourly :17. Equivalent of the paused Grok routine 'box-health':
checks mem/load/top/disk/zombies, cron + /etc/cron.d perms, mem.log freshness, earlyoom --avoid, swap + swappiness;
appends one line to /workspace/box-ops/health.log. Small fixes (sudo -n, idempotent): cron.d chown/chmod, box-ops-ensure.sh
(earlyoom/swap/swappiness), start cron, mv /node_modules -> /node_modules.bak-<ts> (never delete).
PI_BOXHEALTH_DRYRUN=1 / --dry-run -> report fixes and the cleanup list without doing them.
Cleanup (2026-10-24, Praise: "超过 7 天自动删，要真删"): really deletes, every run, what is older than 7 days in the whitelist --
npm / pip caches (per file), apt's downloaded .debs, this user's top-level /tmp entries that nothing touched for 7 days and no
process holds (cwd / root / open fd, as panoptes scripts/sweep-tmp.sh), /tmp/cindy.deb, log files in LOG_DIRS older than 7 days
(this script is the only one that deletes logs: age = the date in the file name, else its mtime) -- and cuts a log over 200 MB in
LOGS_CAP to its last 20 MB. Never the blacklist (Cindy / cindy-launch / CindyGlobal, keyrings, restic, snapshots, box-restore,
agent-data), never /tmp/argus-* (panoptes' sweep-tmp.sh) and never anything a process holds -- so a running QA run (flock -n on
its lock; only reported) does not stop the rest. QA evidence, step-results and bot work dirs are janitor's.
Time zone: the sand platform's own setting decides (Praise 10-24: "时区就按 grok bot 官方的走 不去动他"); nothing here reads or
changes it. box-ops cron (/etc/cron.d/box-ops) does NO cleanup, so cleanup has a single owner: this script."""
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
MEM_LIMIT = 8192   # 10-28: 3G -> 8G, aligned with the QA full-run gate (an orphan grep held 4.8G for 4h, avail 4.6G, no alert)
MEM_TO = ["Mivo 测试项目经理", "Mivo QA 运维"]          # names, routed by outbound's SendToAgent relay
MEM_CRIT = 3072   # 10-28 (PM): tiered — < 8G for 5 min -> MEM_TO bots only; < 3G for 5 min -> also DM Praise
# 10-28 hourly long-lived big-process scan: report only, never kill. Whitelist by a process's OWN argv (first two words) or by
# ancestry from a QA root; a child of sand-* is NOT whitelisted (the 10-28 grep's parent was sand-exit-watch).
BIG_RSS_MB, BIG_AGE_S = 1024, 1800
QA_ROOT = re.compile(r"panoptes|nightly|catch-up")
SELF_WL = re.compile(r"panoptes|nightly|catch-up|/usr/lib/cindy|exec-daemon|earlyoom|^sand-|/sand-|/opt/sand/")
def _head(p): return " ".join(p["args"].split()[:2])
def big_procs(rows):
    """rows: [{pid, ppid, rss_mb, etimes, comm, args}] -> the ones to report (elapsed > 30 min, RSS > 1G, not whitelisted)"""
    by = {r["pid"]: r for r in rows}
    def wl(r):
        h = _head(r)
        if SELF_WL.search(h) or r["comm"].startswith("sand-") or PLATFORM.search(r["args"]): return True
        seen = set(); q = by.get(r["ppid"])
        while q and q["pid"] not in seen and q["pid"] > 1:
            if QA_ROOT.search(_head(q)): return True
            seen.add(q["pid"]); q = by.get(q["ppid"])
        return False
    out = []
    for r in rows:
        if r["etimes"] > BIG_AGE_S and r["rss_mb"] > BIG_RSS_MB and not wl(r):
            pr = by.get(r["ppid"], {})
            out.append({**r, "args": r["args"][:120], "parent": f'{r["ppid"]}:{pr.get("comm", "?")}'})
    return out
def ps_rows():
    rows = []
    for line in subprocess.run(["ps", "-eo", "pid=,ppid=,rss=,etimes=,comm=,args="], capture_output=True, text=True).stdout.splitlines():
        f = line.split(None, 5)
        if len(f) >= 5 and f[0].isdigit():
            rows.append({"pid": int(f[0]), "ppid": int(f[1]), "rss_mb": int(f[2]) // 1024, "etimes": int(f[3]), "comm": f[4], "args": f[5] if len(f) > 5 else f[4]})
    return rows
def low_streak(limit=MEM_LIMIT, n=30):
    """consecutive most-recent minutes in box-ops mem.log with MemAvailable < limit (one sample per minute)"""
    try: lines = subprocess.run(["tail", "-n", str(n * 10), "/workspace/box-ops/mem.log"], capture_output=True, text=True).stdout.splitlines()
    except Exception: return 0
    av = [int(l.split()[6]) for l in lines if l.startswith("Mem:") and len(l.split()) >= 7]
    k = 0
    for a in reversed(av[-n:]):
        if a >= limit: break
        k += 1
    return k
def mem_tier(avail, low8, low3):
    """'crit' (< 3G for 5 min: bots + Praise DM), 'warn' (< 8G for 5 min: bots only) or ''."""
    if avail < MEM_CRIT and low3 >= 5: return "crit"
    return "warn" if avail < MEM_LIMIT and low8 >= 5 else ""
def mem_bots_due(tier, st, now):
    """Bot-notice dedupe per tier: fire on first entry or escalation warn->crit (even if warn already fired), 24h reminder;
    no new notice on de-escalation; state cleared on recovery."""
    rank = {"": 0, "warn": 1, "crit": 2}; prev = st.get("mem_tier", "")
    if not tier:
        st.update(mem_tier="", mem_sent=0); return False
    due = rank[tier] > rank.get(prev, 0) or now - st.get("mem_sent", 0) > 86400
    if rank[tier] > rank.get(prev, 0): st["mem_tier"] = tier   # keep the highest tier fired until recovery (no flapping re-fire)
    if due: st["mem_sent"] = now
    return due
def balloon_mb():
    """Host virtio_balloon size: pages held by the host (inflate - deflate). With DEFLATE_ON_OOM negotiated the
    kernel keeps these pages inside MemTotal and counts them as used, so they lower MemAvailable without any process RSS."""
    try:
        v = dict(l.split() for l in open("/proc/vmstat") if l.startswith("balloon_"))
        return max(0, int(v.get("balloon_inflate", 0)) - int(v.get("balloon_deflate", 0))) * os.sysconf("SC_PAGE_SIZE") // 2**20
    except Exception: return -1
def mem_cause(bal, rss_top):
    """cause=balloon: host holds >= 2G and the top-5 RSS is smaller than the balloon (processes don't explain the drop)"""
    return "balloon" if bal >= 2048 and rss_top < bal else "process"
def sh(c): return subprocess.run(c, shell=True, capture_output=True, text=True, timeout=300).stdout.strip()
def fix(desc, cmd, fixed):
    if DRY: fixed.append("[dry] " + desc); return
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=300)
    fixed.append(desc + ("" if r.returncode == 0 else f" (失败 rc={r.returncode})"))
def du(paths):
    return sum(int(sh(f"du -sb {p} 2>/dev/null | cut -f1") or 0) for p in paths)
KEEP_DAYS = 7
TMP_ROOT = os.environ.get("BOXHEALTH_TMP_ROOT") or "/tmp"          # tests only
LOGS_CAP = ["/var/log/earlyoom.log", "/workspace/box-ops/*.log", "/workspace/grokbot-box-check/*.log"]
LOG_MAX, LOG_KEEP = 200 << 20, 20 << 20
BLACK = re.compile(r"(?i)cindy|keyring|restic|snapshot|box-restore|agent-data|^\.x11|^\.ice|^\.font|^tmux|^ssh-|^systemd|^sand|^xdg")
LOG_DIRS = (os.environ.get("BOXHEALTH_LOG_DIRS") or "/workspace/mivo-qa/argus-root/logs:/workspace/mivo-qa/pi/out/logs:/workspace/box-ops:"
            "/workspace/grokbot-box-check").split(":")                 # env: tests only
LOG_NAME = re.compile(r"\.log($|[.-])|\.out$")              # x.log, x.log.1, x.log.gz, mem.log.20261016-022110.gz, catch-up-guard-*.out
NAME_DAY = re.compile(r"(20\d\d)-?(\d\d)-?(\d\d)")
def log_day(name):
    """the day a log file is about: the date in its name (nightly-20261016T..., prwatch-2026-10-16.log), None when it has none"""
    m = NAME_DAY.search(name)
    try: return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None
    except ValueError: return None
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
    held = held_paths(); me = os.getuid()
    cutoff = now_cst().date() - dt.timedelta(days=KEEP_DAYS)          # a full 7-day window stays (infrafix reads 7 days)
    for d in LOG_DIRS:
        try: names = sorted(os.listdir(d))
        except OSError: continue
        for name in names:
            p = f"{d}/{name}"
            if not LOG_NAME.search(name) or os.path.islink(p) or not os.path.isfile(p) or p in held: continue
            day = log_day(name)
            old = day < cutoff if day else time.time() - os.path.getmtime(p) > KEEP_DAYS * 86400
            if old: c.append((p, "file"))
    # 10-24 review: a QA run holding its lock no longer stops /tmp -- argus-* are never ours and held paths are skipped anyway
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
    bal = balloon_mb()
    rss_top = int(sh("ps -eo rss --sort=-rss --no-headers | head -5 | awk '{s+=$1} END{print int(s/1024)}'") or 0)
    top = sh("ps -eo comm,rss --sort=-rss --no-headers | head -5 | awk '{printf \"%s:%dM \", $1, $2/1024}'")
    zl = zombies(); ours = [z for z in zl if not z["platform"]]; zomb = len(zl)
    st = jload(STATE, {}); hist = (st.get("zombie_hist") or [])[-5:] + [len(ours)]; st["zombie_hist"] = hist
    if ours: issues.append(f"非平台僵尸 {len(ours)} 个: " + ", ".join(f'{z["pid"]}<-{z["ppid"]}:{z["parent"][:40]}' for z in ours[:5]))
    if len(hist) >= 2 and min(hist[-2:]) >= 10: alerts.append(("zombies", f"非平台僵尸进程连续 2 次 ≥10 个（现 {len(ours)} 个，父进程见 health.log）"))
    if float(la[2]) > ncpu: alerts.append(("load", f"15 分钟负载 {la[2]} > {ncpu} 核（持续高）"))
    low_min = low_streak()
    cause = mem_cause(bal, rss_top)
    low3 = low_streak(MEM_CRIT) if avail < MEM_CRIT else 0
    tier = mem_tier(avail, low_min, low3); mem_raw = None
    if tier:
        lim, n = ("3G", low3) if tier == "crit" else ("8G", low_min)
        mem_raw = (f"可用内存持续 {n} 分钟 < {lim}（现 {avail}MB，cause={cause}，balloon={bal}MB，top5 RSS={rss_top}MB"
                   + ("；宿主机 balloon 占用，guest 内无法释放，需平台侧处理）" if cause == "balloon" else "）"))
        if tier == "crit": alerts.append(("mem", mem_raw))     # only the 3G tier reaches Praise's DM
    elif avail < MEM_LIMIT or low_min: issues.append(f"可用内存瞬时低（现 {avail}MB，连续 {low_min} 分钟；多为平台快照回收，不告警）")
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
    big = big_procs(ps_rows()); seen = st.get("bigproc_seen") or {}
    keyof = lambda b: f'{b["pid"]}@{int(time.time() - b["etimes"]) // 60}'      # pid + start minute: pid reuse is a new process
    new_big = [b for b in big if keyof(b) not in seen]; st["bigproc_seen"] = {keyof(b): 1 for b in big}
    if big: issues.append("长驻大进程 " + ", ".join(f'{b["pid"]}({b["rss_mb"]}M,{b["etimes"] // 60}min)' for b in big[:5]))
    cand, locks = cleanup_candidates(); cand_b = du([p for p, k in cand if k != "trunc"]) if cand else 0
    clean = not DRY; freed = 0
    if clean and cand:
        freed = really_clean(cand)
        if freed > 500 * 2**20: events.append(f"清理释放 {freed // 2**20}MB > 500MB")
    bad = [f for f in fixed if "失败" in f]
    if bad and not DRY: alerts.append(("fix-failed", "自动修复失败: " + "; ".join(bad)))
    if fixed and not DRY and len(bad) < len(fixed): events.append("已修复: " + "; ".join(f for f in fixed if f not in bad))
    line = (f"{t.strftime('%F %T')} CST [pi-line{' dry' if DRY else ''}] avail={avail}MB balloon={bal}MB low_streak={low_min}min load={load} disk={disk} zombies={zomb}(ours {len(ours)}) top={top.strip()} "
            f"clean={'done freed=' + str(freed // 2**20) + 'MB' if clean else 'dry'} candidates={len(cand)}项/{cand_b // 2**20}M"
            f"{' (测试在跑: ' + ','.join(os.path.basename(l) for l in locks) + ' 被占；只清它没在用的)' if locks else ''} "
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
        if code == "mem": return PN.item("auto", "机器内存一直不够", f"可用内存已经连续 {low3} 分钟低于 3G（现在 {avail}MB，cause={cause}）。",
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
        elif "/node_modules" in e and "改名" in e: items.append(PN.item("fyi", "机器根目录多出来的 node_modules 被挪开了",
            f"机器根目录（/）下出现了一个 node_modules 文件夹，会干扰测试，已经改名挪开（没删）：{e.split('已改名 ')[-1].split('，')[0]}。",
            "如果修 PR bot 正好在用它，可能要重装依赖。", todo="告诉修测试的会话一声，让修 PR bot 那边知道这件事"))
    msg = PN.text(*items); acts = []
    if mem_bots_due(tier, st, t.timestamp()):
        acts += [{"key": f"boxhealth-mem:{d}:{t.strftime('%H%M')}:{n}", "type": "SendToAgent", "to": n, "priority": True, "text": mem_raw} for n in MEM_TO]
    if new_big:
        acts.append({"key": f"boxhealth-bigproc:{d}:{t.strftime('%H%M')}", "type": "SendToAgent", "to": MEM_TO[0], "priority": False,
                     "text": "长驻大进程（>30 分钟且 RSS>1G，非白名单；只报告，未杀）：\n" + "\n".join(
                         f'pid={b["pid"]} parent={b["parent"]} rss={b["rss_mb"]}MB elapsed={b["etimes"] // 60}min cmd={b["args"]}' for b in new_big)})
    if not DRY: write_atomic(STATE, json.dumps(st, ensure_ascii=False, indent=1))
    if msg: acts.insert(0, {"key": f"boxhealth:{d}:{t.strftime('%H%M')}", "type": "slack.dm", "to": "Praise", "text": msg})
    if DRY: print("would send:", *(f'{a["to"]}: {a["text"]}' for a in acts), sep="\n  ")
    elif acts: record_actions("box", d, acts)
if __name__ == "__main__": sys.exit(main())
