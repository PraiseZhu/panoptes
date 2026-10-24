#!/usr/bin/env python3
"""电脑运维 box-health takeover (gated by takeover/boxhealth), hourly :17. Equivalent of the paused Grok routine 'box-health':
checks mem/load/top/disk/zombies, cron + /etc/cron.d perms, mem.log freshness, earlyoom --avoid, swap + swappiness;
appends one line to /workspace/box-ops/health.log. Small fixes (sudo -n, idempotent): cron.d chown/chmod, box-ops-ensure.sh
(earlyoom/swap/swappiness), start cron, mv /node_modules -> /node_modules.bak-<ts> (never delete).
PI_BOXHEALTH_DRYRUN=1 -> report fixes without doing them. Cleanup of the whitelist is LIST-ONLY unless PI_BOXHEALTH_CLEAN=1.
box-ops cron (/etc/cron.d/box-ops) does NO cleanup, so cleanup has a single owner: this script."""
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
def cleanup_candidates():
    H = os.path.expanduser("~"); now = time.time(); c = []
    c += [p for p in [f"{H}/.npm/_cacache", f"{H}/.cache/pip", "/tmp/cindy.deb"] if os.path.exists(p)]
    c += [p for p in sh("find /tmp -maxdepth 1 -user box -type f -mtime +3 2>/dev/null").splitlines() if p]
    for p in glob.glob("/tmp/argus-run-*"):   # >24h and no lock inside
        if now - os.path.getmtime(p) > 86400 and not glob.glob(f"{p}/*.lock"): c.append(p)
    A = "/workspace/mivo-qa/argus-root"
    locks = [f"{A}/{n}" for n in ("nightly.lock", ".nightly.lock", "catch-up.lock", ".catch-up.lock") if os.path.exists(f"{A}/{n}")]
    if not locks:
        c += [p for p in glob.glob(f"{A}/cache/runs/*") if now - os.path.getmtime(p) > 14 * 86400]
    return c, locks
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
    cand, locks = cleanup_candidates(); cand_b = du(cand) if cand else 0
    clean = os.environ.get("PI_BOXHEALTH_CLEAN") == "1" and not DRY; freed = 0
    if clean and cand:
        before = int(sh("df -B1 / | awk 'NR==2{print $4}'") or 0)
        for p in cand: sh(f"rm -rf -- '{p}'")
        sh("sudo -n apt-get clean")
        freed = int(sh("df -B1 / | awk 'NR==2{print $4}'") or 0) - before
        if freed > 500 * 2**20: events.append(f"清理释放 {freed // 2**20}MB > 500MB")
    bad = [f for f in fixed if "失败" in f]
    if bad and not DRY: alerts.append(("fix-failed", "自动修复失败: " + "; ".join(bad)))
    if fixed and not DRY and len(bad) < len(fixed): events.append("已修复: " + "; ".join(f for f in fixed if f not in bad))
    line = (f"{t.strftime('%F %T')} CST [pi-line{' dry' if DRY else ''}] avail={avail}MB low_streak={low_min}min load={load} disk={disk} zombies={zomb}(ours {len(ours)}) top={top.strip()} "
            f"clean={'done freed=' + str(freed // 2**20) + 'MB' if clean else 'list-only'} candidates={len(cand)}项/{cand_b // 2**20}M"
            f"{' (mivo runs 跳过: ' + ','.join(os.path.basename(l) for l in locks) + ' 在)' if locks else ''} "
            f"issues={'; '.join(issues) or 'none'} fixed={'; '.join(fixed) or 'none'}")
    if DRY: print(line); print("candidates:", *cand, sep="\n  ")
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
        elif "/node_modules" in e and "改名" in e: items.append(PN.item("fyi", "机器根目录多出来的 node_modules 被挪开了",
            f"机器根目录（/）下出现了一个 node_modules 文件夹，会干扰测试，已经改名挪开（没删）：{e.split('已改名 ')[-1].split('，')[0]}。",
            "如果修 PR bot 正好在用它，可能要重装依赖。", todo="告诉修测试的会话一声，让修 PR bot 那边知道这件事"))
    msg = PN.text(*items)
    if not DRY: write_atomic(STATE, json.dumps(st, ensure_ascii=False, indent=1))
    if DRY: print("would send:", msg)
    elif msg: record_actions("box", d, [{"key": f"boxhealth:{d}:{t.strftime('%H%M')}", "type": "slack.dm", "to": "Praise", "text": msg}])
if __name__ == "__main__": sys.exit(main())
