#!/usr/bin/env python3
"""Cindy 管家 takeover (gated by takeover/cindy), every 5 min via run.sh (flock per job).
Equivalent to the Grok routine: each run calls ~/.config/cindy-launch/ensure-cindy.sh with PI_CINDY_CALLER=1,
so ALL duties come from that one script (reinstall via apt from manifest + gnome-keyring/libsecret-tools/xdotool,
start only via start-cindy.sh, relaunch if not started by the script, relaunch once/hour if no window,
install newer downloaded .deb (sha256), daily manifest update, nudge mouse/xset on :2 + nudge-loop).
ensure-cindy.sh itself holds /tmp/ensure-cindy.lock (single instance) and skips non-Pi callers while takeover exists;
6 lock-SKIPs in a row (30 min) count as FAIL (10-24: the lock fd leaked into nudge-loop and silenced keep-alive).
--dry-run: read-only report of what ensure WOULD do; never calls ensure, never kills/starts anything."""
from lib import *
H = os.path.expanduser("~")
def sh(c, t=900): return subprocess.run(c, shell=True, capture_output=True, text=True, timeout=t).stdout.strip()
def main_proc(): return sh("pgrep -af '^(/usr/bin/cindy|/usr/lib/cindy/Cindy)( |$)' | grep -v -- --type= | head -1")
def would(proc, ver):
    w = []
    if not os.access("/usr/bin/cindy", os.X_OK) or not sh("command -v gnome-keyring-daemon"): w.append("reinstall")
    if sh(f"cat {H}/.config/cindy-launch/.last-update-check 2>/dev/null") != dt.date.today().isoformat(): w.append("daily-manifest-check")
    try:
        pi = json.load(open(f"{H}/.config/CindyGlobal/updates/patch-info.json"))
        if os.path.exists(f"{H}/.config/CindyGlobal/updates/{pi['fileName']}") and subprocess.run(["dpkg","--compare-versions",pi["version"],"gt",ver]).returncode == 0:
            w.append(f"install-downloaded {ver}->{pi['version']}")
    except Exception: pass
    if proc and "--password-store=gnome-libsecret" not in proc: w.append("relaunch-with-script")
    if proc and not sh("DISPLAY=:2 xdotool search --class cindy 2>/dev/null | head -1", 20): w.append("relaunch-no-window(≤1/h)")
    if not proc: w.append("start via start-cindy.sh")
    return w or ["无（只 nudge）"]
DOCK = f"{H}/.config/plank/dock1/launchers/cindy.dockitem"
DOCK_TXT = f"[PlankDockItemPreferences]\nLauncher=file://{H}/.local/share/applications/cindy.desktop\n"
def dock(dry):
    if os.path.exists(DOCK): return "✓"
    if dry: return "缺（将恢复）"
    os.makedirs(os.path.dirname(DOCK), exist_ok=True); open(DOCK, "w").write(DOCK_TXT); return "已恢复"
NSTATE = f"{BASE}/out/cindy/notify-state.json"
def notify(day, status):
    """Grok parity: tell Praise on CHANGED / login!=ok / FAIL, never repeat the same failure. File-only:
    records an actions.json entry (type grok.chat.praise has no outbound handler -> never sent)."""
    st = json.load(open(NSTATE)) if os.path.exists(NSTATE) else {}
    sig = re.sub(r"\(.*", "", status).strip()          # drop volatile detail (log tail) from the signature
    if status.startswith("STATUS=lowmem-defer"): return "lowmem-defer 静默（不记通知，下个 */5 重试）"
    if status.startswith("STATUS=OK") or status.startswith("STATUS=SKIP"): st["last"] = "OK"
    elif status.startswith("STATUS=CHANGED") and "login=ok" in status:
        record_actions("cindy", day, [{"key": f"cindy-notify:{day}:{now_cst().strftime('%H%M')}:changed", "type": "grok.chat.praise",
            "text": f"Cindy 保活: {status}", "dedupe": "one per CHANGED event"}]); st["last"] = "OK"
    elif st.get("last") != sig:
        record_actions("cindy", day, [{"key": f"cindy-notify:{day}:{sig}", "type": "grok.chat.praise",
            "text": f"Cindy 保活异常: {status}", "dedupe": "out/cindy/notify-state.json last-signature; same failure not repeated"}]); st["last"] = sig
    else: return "同一异常已记录，不重复"
    write_atomic(NSTATE, json.dumps(st, ensure_ascii=False)); return "ok"
LOCK = "/tmp/ensure-cindy.lock"
def lock_holders():
    out = []
    for p in (x for x in os.listdir("/proc") if x.isdigit()):
        try:
            if any(os.readlink(f"/proc/{p}/fd/{fd}") == LOCK for fd in os.listdir(f"/proc/{p}/fd")): out.append(p)
        except OSError: pass
    return out
def lock_skip_guard(status, n_fail=6):
    """10-24: ensure-cindy.sh's lock fd was inherited by the nudge-loop it starts in the background (fixed there with 8>&-), so every
    later run said "SKIP another ensure-cindy run holds lock" -- counted as OK, keep-alive silently off. n_fail such runs in a row
    (30 min at */5) -> FAIL, which notify() reports once."""
    st = json.load(open(NSTATE)) if os.path.exists(NSTATE) else {}
    n = st.get("lock_skips", 0) + 1 if "holds lock" in status else 0
    if n != st.get("lock_skips", 0): st["lock_skips"] = n; write_atomic(NSTATE, json.dumps(st, ensure_ascii=False))
    if n >= n_fail: return f"STATUS=FAIL ensure-cindy.sh lock held for {n} runs in a row (holders {','.join(lock_holders()) or '?'})"
    return status
def main():
    dry = "--dry-run" in sys.argv or os.environ.get("PI_CINDY_DRYRUN")
    d = today(); out = f"{BASE}/out/cindy/{d}{'.dryrun' if dry else ''}.md"; L = [f"## {now_cst().strftime('%H:%M')}{' (dry-run)' if dry else ''}"]
    if mode() != "pi": log(f"fleet-mode={mode()} -> skip (Grok routine owns it)"); return 0
    if not takeover("cindy"): log("takeover/cindy absent -> skip (Grok routine owns it)"); return 0
    proc = main_proc(); ver = sh("dpkg-query -W -f='${Version}' cindy 2>/dev/null")
    logf = sh(f"ls -t {H}/.config/CindyGlobal/logs/main-*.log 2>/dev/null | head -1")
    mode_ = sh(f"grep -A8 'stable app session committed' '{logf}' | grep -o \"mode: '[a-z-]*'\" | tail -1") if logf else "?"
    win = sh("DISPLAY=:2 xdotool search --class cindy 2>/dev/null | head -1", 20)
    L += [f"- 进程: {proc.split()[0] if proc else '未运行'}{' (start-cindy.sh flags ✓)' if 'gnome-libsecret' in proc else ''}", f"- 窗口(:2): {'✓' if win else '无'}",
          f"- 版本: {ver}", f"- 登录: {mode_}", f"- Dock: {dock(dry)}"]
    if dry: L.append(f"- ensure 将做: {', '.join(would(proc, ver))}")
    if dry and os.environ.get("PI_LOWMEM") and not proc:
        # run.sh: Cindy is down and MemAvailable < 2 GB -> check only, no relaunch. 10-24: that used to tell nobody, and Cindy is
        # how Praise reaches every session. Once per episode (notify() keeps the signature until a run says OK again).
        L.append(f"- 通知: {notify(d, 'STATUS=FAIL Cindy 没在运行；机器可用内存不到 2GB，为了不拖垮别的任务暂时不拉起，内存回来后下一个 5 分钟会自动拉起')}")
    else:
        env = dict(os.environ, PI_CINDY_CALLER="1")
        r = subprocess.run(["bash", f"{H}/.config/cindy-launch/ensure-cindy.sh"], env=env, capture_output=True, text=True, timeout=900)
        st = [l for l in (r.stdout + r.stderr).splitlines() if l.startswith("STATUS=")]
        status = lock_skip_guard(st[-1] if st else f"STATUS=FAIL rc={r.returncode} no status line")
        L.append(f"- ensure-cindy.sh rc={r.returncode}: {status}"); L.append(f"- 通知(file-only): {notify(d, status)}")
    log(L[-1])
    old = open(out).read() if os.path.exists(out) else f"# Cindy 管家 {d}（Pi line）\n"
    write_atomic(out, old + "\n".join(L) + "\n")
if __name__ == "__main__": sys.exit(main())
