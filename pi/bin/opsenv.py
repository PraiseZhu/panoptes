#!/usr/bin/env python3
"""运维 hourly env guard (port of Grok routine panoptes-env-guard).
Checks: bootstrap-box.sh --check (chromium, node/npm, venv, catch-up cron file + cron daemon, apt deps), explicit
spot checks, repo intact at /workspace/panoptes (HEAD vs origin via ls-remote, dirty files), gh auth, secret perms.
Repair: runs /workspace/panoptes/bootstrap-box.sh ONLY when the flag file ops-repair-enabled exists AND fleet-mode=pi
AND no panoptes run holds the lock (else check-only, like the Grok routine's 'report' half). Report: out/ops/env-<day>.md;
failures also land in out/ops/env-<day>.alerts.json (file only; the Grok routine reported to the QA group)."""
import shutil
from lib import *
import praise_notice as PN
REPAIR_FLAG = f"{BASE}/ops-repair-enabled"
P = "/workspace/panoptes"
def sh(c, t=120):
    r = subprocess.run(c, shell=True, capture_output=True, text=True, timeout=t); return r.returncode, (r.stdout + r.stderr).strip()
def busy():
    _, o = sh("pgrep -f '/workspace/panoptes/run-nightly.sh' | grep -v $$ || true")
    pids = [x for x in o.split() if x.isdigit() and int(x) != os.getpid()]
    return pids
def checks():
    fails = []; L = []
    rc, o = sh(f"{P}/scripts/bootstrap-box.sh --check 2>&1 | tail -20", 300)
    miss = [l for l in o.splitlines() if l.startswith("missing:")]
    L.append(f"- bootstrap --check rc={rc}" + (" 全部就绪" if rc == 0 and not miss else ""))
    fails += miss or ([f"bootstrap --check rc={rc}: {o[-200:]}"] if rc else [])
    spot = {"chromium": os.access("/usr/local/bin/chromium", os.X_OK), "node": bool(shutil.which("node", path="/usr/local/bin")),
            "npm": bool(shutil.which("npm", path="/usr/local/bin")), "venv": os.access(f"{QA}/.venv/bin/python3", os.X_OK),
            "catch-up cron": os.path.exists("/etc/cron.d/panoptes-catch-up"), "cron daemon": sh("pgrep -x cron")[0] == 0,
            "repo": os.path.isdir(f"{P}/.git") and os.access(f"{P}/run-nightly.sh", os.X_OK)}
    L.append("- 抽查: " + ", ".join(f"{k}={'ok' if v else 'MISSING'}" for k, v in spot.items()))
    fails += [f"missing: {k}" for k, v in spot.items() if not v and not any(k.split()[0] in m for m in miss)]
    rc, o = sh("gh auth status 2>&1 | grep -E 'Logged in' | head -1"); L.append(f"- gh: {'ok' if 'Logged in' in o else 'NOT OK'}")
    if 'Logged in' not in o: fails.append("gh auth not logged in")
    _, head = sh(f"git -C {P} rev-parse HEAD"); _, br = sh(f"git -C {P} rev-parse --abbrev-ref HEAD")
    _, rem = sh(f"git -C {P} ls-remote origin refs/heads/{br}", 60)
    same = bool(rem) and rem.startswith(head)
    L.append(f"- panoptes {br} {head[:8]} vs origin {rem[:8] or '?'} → {'一致' if same else '不一致'}")
    if not same: fails.append(f"panoptes HEAD {head[:8]} != origin/{br} {rem[:8]}")
    _, dirty = sh(f"git -C {P} status --porcelain --untracked-files=no | wc -l"); L.append(f"- panoptes 未提交改动 {dirty} 个文件")
    if dirty.strip() != "0": fails.append(f"panoptes has {dirty} modified tracked files")
    _, perm = sh("stat -c '%a %n' /home/box/.config/mivo-qa/secrets.env 2>/dev/null")
    L.append(f"- 密钥文件权限: {perm or '未找到'}")
    if not perm.startswith("600"): fails.append("secrets.env missing or not 600")
    return L, fails
def deploy(L):
    """10-23: the Pi line had no panoptes deployer (#18 merged 14:17, still undeployed at 21:00). Fast-forward only when ALL hold:
    fleet-mode=pi, on main, clean tracked tree, origin/main strictly ahead, every GitHub Actions run on the target commit
    completed+success, and both panoptes locks are free (held during the update so no run starts on half-updated code)."""
    import fcntl
    if mode() != "pi": return None
    _, br = sh(f"git -C {P} rev-parse --abbrev-ref HEAD")
    if br != "main": L.append(f"- 部署: 跳过（工作树不在 main：{br}）"); return None
    if sh(f"git -C {P} fetch -q origin main", 120)[0]: L.append("- 部署: fetch 失败"); return "panoptes fetch failed"
    _, head = sh(f"git -C {P} rev-parse HEAD"); _, tgt = sh(f"git -C {P} rev-parse origin/main")
    if head == tgt: return None
    if sh(f"git -C {P} merge-base --is-ancestor HEAD origin/main")[0]: L.append("- 部署: 跳过（本地有 origin 没有的提交，不能快进）"); return "panoptes diverged from origin/main"
    if sh(f"git -C {P} status --porcelain --untracked-files=no | wc -l")[1].strip() != "0": L.append("- 部署: 跳过（工作区有未提交改动）"); return None
    try: runs = gh(["run", "list", "-R", "PraiseZhu/panoptes", "--commit", tgt, "--json", "name,status,conclusion"])
    except Exception as e: L.append(f"- 部署: 查 CI 失败 {str(e)[:80]}"); return None
    if not runs or any(r.get("status") != "completed" or r.get("conclusion") != "success" for r in runs):
        L.append(f"- 部署: 等 {tgt[:7]} 的 CI（{[(r.get('name'), r.get('conclusion') or r.get('status')) for r in runs] or '无记录'}）"); return None
    held = []
    try:
        for n in ("catch-up.lock", "nightly.lock"):
            f = open(f"{QA}/argus-root/{n}", "a"); fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB); held.append(f)
        rc, o = sh(f"git -C {P} merge -q --ff-only origin/main", 120)
    except BlockingIOError:
        L.append("- 部署: 等 panoptes 空闲（有运行持锁）"); return None
    finally:
        for f in held: f.close()
    if rc: L.append(f"- 部署: ff-only 失败 {o[-120:]}"); return f"panoptes ff-only failed: {o[-120:]}"
    L.append(f"- 部署: {head[:7]} → {tgt[:7]}（ff-only，CI 全部成功，锁空闲）"); log("deployed panoptes", head[:7], "->", tgt[:7])
    return None
FIX_ENV = "测试环境自检报：{x}。看 /workspace/mivo-qa/pi/out/ops/env-*.md，没有测试在跑时跑 /workspace/panoptes/scripts/bootstrap-box.sh 补齐，再跑一次 opsenv 确认"
def env_item(x):
    """opsenv 的一条失败 → 给 Praise 的一件事（None = 不单独打扰）"""
    if x.startswith("panoptes HEAD "):
        # 在 main 上、只是落后：同一轮就部署，卡住 3 小时以上看门狗会报 —— 不单独打扰。不在 main 上（别的分支 / detached）或分叉了：
        # deploy() 会一直跳过、看门狗也不报（它只看「落后」），这里必须报（10-24 复查）
        br = (re.search(r"!= origin/(\S+)", x) or [None, ""])[1]
        if br == "main" and sh(f"git -C {P} merge-base --is-ancestor HEAD origin/main")[0] == 0: return None
        return PN.item("action", "测试系统的代码不在正常的 main 上，自动部署停了",
                       f"机器上的测试系统（/workspace/panoptes）现在{'切在 ' + br + ' 分支上' if br and br not in ('main', 'HEAD') else '停在一个不在 main 上的提交上'}，自动部署只更新 main，所以会一直跳过。",
                       "测试用的不是线上该用的代码，新修的东西也上不去。",
                       todo=PN.ask_session(f"/workspace/panoptes 不在 main 上（{x[:100]}），看是谁切的，确认没用后切回 main 并快进到 origin/main"))
    if x.startswith("missing:") or x.startswith("bootstrap --check"):
        what = x.split(":", 1)[-1].strip()[:80]
        return PN.item("action", "测试环境缺东西", f"每小时的测试环境自检发现缺了「{what}」（测试要用的浏览器、Node、Python 环境或定时任务之一）。",
                       "缺的那部分检查跑不了，严重时整个测试都跑不起来。", todo=PN.ask_session(FIX_ENV.format(x=x)))
    if x.startswith("gh auth"):
        return PN.item("action", "测试系统的 GitHub 登录失效了", "机器上 gh 命令行的 GitHub 登录没了。",
                       "测试读不到 PR、发不了评论，合并即测和修 PR 都会停。", todo=PN.ask_session("机器上 gh auth status 显示没登录，用 secrets.env 里的 GITHUB_TOKEN 重新登录"))
    if "modified tracked files" in x:
        return PN.item("action", "测试系统的代码在机器上被直接改了", f"测试系统（/workspace/panoptes）里有文件被直接改动、没提交（{x.split('has ')[-1]}）。",
                       "自动部署会因此停下，新代码上不了线。", todo=PN.ask_session("/workspace/panoptes 有未提交的改动，看是谁改的：有用就提 PR，没用就还原，让自动部署恢复"))
    if x.startswith("secrets.env"):
        return PN.item("action", "测试系统的密钥文件不见了或权限不对", "存 GitHub、模型、飞书、Slack 密钥的文件 secrets.env 找不到，或者权限不是只有自己能读（600）。",
                       "测试和所有通知都可能停。", todo=PN.ask_session("/home/box/.config/mivo-qa/secrets.env 缺失或权限不是 600，查一下恢复"))
    if "diverged" in x or "ff-only failed" in x:
        return PN.item("action", "测试系统的代码和 GitHub 对不上，自动部署停了", "机器上的测试系统代码里有 GitHub 上没有的提交，没法直接更新到最新版。",
                       "新代码上不了线。", todo=PN.ask_session(f"panoptes 自动部署失败（{x[:100]}），看 /workspace/panoptes 多出来的提交，处理后让它能快进到 origin/main"))
    if "fetch failed" in x:
        return PN.item("auto", "测试系统拿不到 GitHub 上的新代码", "自动部署时从 GitHub 拉代码失败了，多半是网络临时问题。",
                       "这一轮没部署。", auto="下个整点 25 分再试；24 小时后还没好会再提醒你")
    return PN.item("action", "测试环境自检发现问题", f"每小时的测试环境自检报：{x[:120]}。", todo=PN.ask_session(FIX_ENV.format(x=x)))


def alert_on_change(d, fails):
    """10-23: tell Praise once per state change (was: alerts.json only, nobody read it)"""
    sp = f"{BASE}/out/ops/env-state.json"
    try: prev = json.load(open(sp)).get("fails", [])
    except Exception: prev = []
    key = lambda x: re.sub(r"[0-9a-f]{7,40}|\d+", "#", x)   # SHAs/counts change every hour; compare the kind of failure
    if sorted(map(key, fails)) == sorted(map(key, prev)): return
    # 2026-10-24（Praise：私信看不懂）：按 praise_notice 的骨架写。「有新代码待部署」不单独发（同一轮就会自动部署；
    # 卡住 3 小时以上由看门狗报）；恢复只报之前要他动手的
    old = {key(x) for x in prev}
    new_items = [env_item(x) for x in fails if key(x) not in old]
    gone = [env_item(x) for x in prev if key(x) not in {key(y) for y in fails}]
    items = [i for i in new_items if i] + [PN.item("resolved", i["title"], f"之前告诉你的「{i['title']}」，现在好了。") for i in gone if i and i["kind"] == "action"]
    write_atomic(sp, json.dumps({"fails": fails, "at": now_cst().isoformat(timespec="seconds")}, ensure_ascii=False))
    msg = PN.text(*items)
    if not msg: return
    record_actions("ops", d, [{"key": f"opsenv:{d}:{now_cst().strftime('%H%M')}", "type": "slack.dm", "to": "Praise", "text": msg}])
def main():
    d = today(); L = [f"## {now_cst().strftime('%H:%M')}（mode={mode()}）"]
    dep = deploy(L)
    b = busy(); L0, fails = checks(); L += L0
    if dep: fails.append(dep)
    L.append(f"- panoptes 运行中: {'pid ' + ' '.join(b) if b else '无'}")
    if fails:
        can = os.path.exists(REPAIR_FLAG) and mode() == "pi" and not b
        if can:
            rc, o = sh(f"QA_HOME={QA} {P}/scripts/bootstrap-box.sh 2>&1 | tail -15", 1800)
            L.append(f"- 修复: bootstrap-box.sh rc={rc} / {o.replace(chr(10),' / ')[-400:]}")
            L1, fails = checks(); L.append("- 修复后复查: " + ("全部就绪" if not fails else "; ".join(fails)))
        else:
            why = "ops-repair-enabled 不存在" if not os.path.exists(REPAIR_FLAG) else ("fleet-mode≠pi" if mode() != "pi" else "panoptes 在跑")
            L.append(f"- 未修复（{why}），只报告")
        L.append("- **失败**: " + "; ".join(fails))
        ap = f"{BASE}/out/ops/env-{d}.alerts.json"
        al = json.load(open(ap)) if os.path.exists(ap) else []
        al.append({"at": now_cst().isoformat(timespec="seconds"), "fails": fails}); write_atomic(ap, json.dumps(al, ensure_ascii=False, indent=1))
    p = f"{BASE}/out/ops/env-{d}.md"; old = open(p).read() if os.path.exists(p) else f"# 环境自检 {d}（Pi line，默认只查；修复需 ops-repair-enabled）\n"
    write_atomic(p, old + "\n".join(L) + "\n")
    if mode() == "pi": alert_on_change(d, fails)
if __name__ == "__main__": main()
