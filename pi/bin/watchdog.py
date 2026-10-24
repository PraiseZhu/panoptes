#!/usr/bin/env python3
"""Pi line watchdog (zero model). One place that notices when the automation itself stops doing its job, and tells Praise
once per state change by Slack DM (new problems + recoveries; still-open problems again after 24 h).

Checks (all from files already on disk, plus one `gh pr list` every 30 min):
  * every cron job of /etc/cron.d/pi-fallback: last clean run not older than its cadence allows; daily slots that passed
    >45 min ago without a run (box down, cron gone, skipped for memory); 3 failed runs in a row; a script that no longer
    compiles (run.sh writes `syntax-error`)
  * mergewatch heartbeat (<5 min) and its ls-remote fail streak
  * merge -> report: a PR merged >90 min ago with no reports/merged/PR-N.json and no panoptes run for it
  * backup: last grokbot-box sync pushed (or nothing to push) within 3 h; a refused push because the box looks rolled back;
    the FAIL lines of the 备份管家 rules (grokbot-box/check.sh at :40 -> check.log, which nobody read before 10-24)
  * panoptes deployed = origin/main (opsenv deploys; alert if still behind after 3 h)
  * infrafix tickets: stuck in running > 2 h, waiting for Praise > 12 h, PR action not executed > 2 h, merged not deployed > 6 h
  * outbound: when outbound itself is failing, the alert bypasses it (same sender script, own dedupe key)
  * 修 PR bot (10-24; nothing watched it): a run stuck > 90 min; a fix-PR action not executed after 2 h; a ticket waiting for
    Praise (needs_praise: kind 5, sensitive files) 12-36 h old = one reminder; prwatch errors in the last 15 min; fixes queued behind memory > 3 h
  * Mac mini 盯梢 (10-24: it was down 21:54-03:20 and nobody noticed): a ready (non-draft) PraiseZhu PR with a failing check and
    no GitHub activity for 4 h (one gh call per 30 min). GitHub cannot see a watcher session working locally, hence "可能"
  * outbound keys and ledger (10-24): a Slack / Feishu key missing from the secret files; actions failing on a key / login
    error; actions failed twice or more. Failed actions sit in backoff, so the cron rounds stay rc=0 -- the run-based checks
    above never saw the 02:07-02:28 outage after the restore (keys not restored), and nobody was told.
Cron: */5, own quiet log out/logs/watchdog-<day>.log (only on change). --dry-run prints findings, sends nothing.
"""
import sys, time, glob, hashlib
from lib import *
try:
    import praise_notice as PN
except Exception as _pn_err:   # 10-24 复查：praise_notice 写坏了，看门狗（含绕过 outbound 的直发通道）不能跟着倒 —— 退回纯文本
    import types
    _PN_ERR = str(_pn_err)[:80]   # the except variable is deleted after the block
    def _item(kind, title, what, impact=None, todo=None, auto=None, link=None):
        return {"kind": kind, "title": title, "what": what, "impact": impact, "todo": todo, "auto": auto, "link": link}
    def _text(*items, still_open=()):
        out = [f"[{i['kind']}] {i['title']}：{i['what']}" + (f" 你要做：{i['todo']}" if i.get("todo") else "") for i in items if i]
        return "\n".join(out + (["之前说过、还没解决的：" + "；".join(still_open)] if still_open else []) + [f"（通知格式模块 praise_notice 加载失败：{_PN_ERR}）"])
    PN = types.SimpleNamespace(item=_item, text=_text, ask_session=lambda t: f"让会话处理：{t}",
                               job_info=lambda j: {"name": j, "does": "", "if_down": "", "critical": True}, job_name=lambda j: j)

DRY = "--dry-run" in sys.argv
LOGS = f"{BASE}/out/logs"
STATE = f"{BASE}/out/watchdog/state.json"
CADENCE = {  # job: max minutes between clean runs (hourly jobs: one missed slot is tolerated)
    "cindy": 20, "outbound": 40, "merger": 75, "boxhealth": 150, "boxcheck": 150, "opsenv": 150, "opsdeploy": 40, "janitor": 40,
}
DAILY = {  # job: [(Beijing hour, minute)] -- mirrors pi-fallback.cron
    "credcheck": [(7, 1)], "knowledge": [(8, 5)], "daily": [(8, 35)], "jasper": [(9, 5)], "opsfix": [(9, 30)],
    "prretro": [(9, 45)], "dailyretro": [(9, 56)], "pm": [(10, 35)], "opsreview": [(11, 15)], "p2": [(12, 20), (18, 20)],
    "compare": [(12, 50)], "handoff": [(0, 5), (6, 20), (15, 20), (18, 35)], "ledger": [(23, 40)], "followup": [(6, 27), (15, 27)],
}
GRACE_MIN = 45
START = re.compile(r"^=== (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) CST start (\S+)")
END = re.compile(r"^=== (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) CST end (\S+) rc=(\d+)")


def jload(f, d):
    try: return json.load(open(f))
    except Exception: return d


def runs(job, days=2):
    """[(start_dt, end_dt|None, rc|None, skipped_reason|None)] from out/logs/<job>-<day>.log of the last `days` days"""
    out = []
    for i in range(days - 1, -1, -1):
        day = (now_cst().date() - dt.timedelta(days=i)).isoformat()
        try: lines = open(f"{LOGS}/{job}-{day}.log", errors="replace").read().splitlines()
        except OSError: continue
        cur = None
        for l in lines:
            m = START.match(l)
            if m: cur = [dt.datetime.fromisoformat(m[1]).replace(tzinfo=CST), None, None, None]; out.append(cur); continue
            if cur is None: continue
            m = END.match(l)
            if m: cur[1], cur[2] = dt.datetime.fromisoformat(m[1]).replace(tzinfo=CST), int(m[3]); continue
            if l.startswith("skip:") or "syntax-error" in l: cur[3] = l[:120]
    return out


def ok_run(r): return r[2] in (0, 75) and not (r[3] and r[3].startswith("syntax-error"))   # 75 = busy, retried next slot


def job_item(job, what, clue):
    """一个作业停了 / 失败了 → 给 Praise 的一件事：关键作业要他找会话看，其他的告诉一声、有空再看"""
    j = PN.job_info(job)
    ask = PN.ask_session(f"Pi 线的 {job} 作业：{clue}，日志在 {BASE}/out/logs/{job}-*.log，查一下原因修好")
    if j["critical"]:
        return PN.item("action", f"「{j['name']}」停了", f"{what}。这个功能平时{j['does']}。", f"{j['if_down']}。", todo=ask)
    return PN.item("fyi", f"「{j['name']}」停了（不急）", f"{what}。这个功能平时{j['does']}。", f"{j['if_down']}，不影响测试和通知。",
                   todo=f"不急，有空时{ask}")


def job_checks(now, probs, since):
    takeovers = {"dailyretro"}  # gated jobs: only expected when their marker exists
    for job, gap in CADENCE.items():
        rs = runs(job)
        good = [r for r in rs if ok_run(r)]
        last = good[-1][1] if good else None
        if not last or (now - last).total_seconds() / 60 > gap:
            ago = f"{int((now - last).total_seconds() // 60)} 分钟" if last else "两天多"
            probs[f"stale:{job}"] = job_item(job, f"它本该每 {gap} 分钟内成功跑一次，现在已经 {ago}没有成功跑过", f"已 {ago}没有成功运行（最近一次：{(rs[-1][3] or 'rc=' + str(rs[-1][2])) if rs else '无'}）")
        tail = [r for r in rs if r[1]][-3:]
        if len(tail) == 3 and all(r[2] for r in tail): probs[f"failing:{job}"] = job_item(job, "它连续 3 次运行都失败了", f"连续 3 次失败（rc={tail[-1][2]}）")
    for job, slots in DAILY.items():
        if job in takeovers and not takeover(job): continue
        rs = runs(job)
        for h, mi in slots:
            slot = now.replace(hour=h, minute=mi, second=0, microsecond=0)
            if slot > now: slot -= dt.timedelta(days=1)
            if (now - slot).total_seconds() / 60 < GRACE_MIN or slot < since: continue   # slots before install: not ours to judge
            after = [r for r in rs if r[0] >= slot - dt.timedelta(minutes=2)]
            if not after: probs[f"missed:{job}:{h:02d}{mi:02d}"] = job_item(job, f"今天 {h:02d}:{mi:02d} 那一轮根本没启动（可能机器停过，或定时任务丢了）", f"{h:02d}:{mi:02d} 这一轮没启动")
            elif not any(ok_run(r) for r in after):
                r = after[-1]; probs[f"failed:{job}:{h:02d}{mi:02d}"] = job_item(job, f"今天 {h:02d}:{mi:02d} 那一轮跑了但没成功", f"{h:02d}:{mi:02d} 这一轮没成功（{r[3] or 'rc=' + str(r[2])}）")
    for job in sorted(set(CADENCE) | set(DAILY)):
        rs = runs(job, 1)
        if rs and rs[-1][3] and "syntax-error" in rs[-1][3]: probs[f"syntax:{job}"] = job_item(job, "它的程序被改坏了，启动不起来", f"的程序 {job}.py 编译失败（{rs[-1][3][:80]}）")


def mergewatch_check(now, probs):
    st = jload(f"{BASE}/out/mergewatch/state.json", {})
    try: last = dt.datetime.strptime(st["last_check"][:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=CST)
    except Exception: last = None
    if not last or (now - last).total_seconds() > 300: probs["mergewatch:stale"] = job_item("mergewatch", f"它每分钟都该看一次有没有新合并，最后一次是 {str(st.get('last_check', '无'))[11:16] or '无'}", f"心跳停了（最后 {st.get('last_check', '无')}）")
    if st.get("fail_streak", 0) >= 15: probs["mergewatch:ls-remote"] = PN.item("auto", "「合并即测」连不上 GitHub", f"它已经连续 {st['fail_streak']} 分钟连不上 GitHub，看不到新合并的 PR。",
        "新合并的 PR 暂时不会马上测，要等每 2 小时一次的兜底。", auto="每分钟重试一次，连上了就恢复；24 小时后还连不上会再提醒你")
    pw = jload(f"{BASE}/out/prwatch/state.json", {})
    try: pl = dt.datetime.strptime(pw["last_check"][:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=CST)
    except Exception: pl = None
    if not pl or (now - pl).total_seconds() > 300: probs["prwatch:stale"] = job_item("prwatch", f"它每分钟都该转一圈，最后一次是 {str(pw.get('last_check', '无'))[11:16] or '无'}", f"心跳停了（最后 {pw.get('last_check', '无')}）")


def merge_report_check(now, probs, st):
    if time.time() - st.get("merged_checked_at", 0) < 1800:   # one gh call per 30 min; keep last verdict in between
        for k, v in st.get("merged_probs", {}).items(): probs[k] = v
        return
    since = (now - dt.timedelta(hours=26)).astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try: prs = gh(["pr", "list", "-R", REPO, "--state", "merged", "--search", f"merged:>={since}", "--json", "number,mergedAt", "--limit", "100"])
    except Exception as e: log("gh pr list failed:", str(e)[:120]); return
    active = subprocess.run(["pgrep", "-af", "run-nightly.sh"], capture_output=True, text=True).stdout
    mp = {}
    for p in prs:
        n = p["number"]; age = (now - parse_ts(p["mergedAt"]).astimezone(CST)).total_seconds() / 60
        if age < 90 or os.path.exists(f"{RPT}/PR-{n}.json") or re.search(rf"--pr {n}\b", active): continue
        mp[f"noreport:{n}"] = PN.item("auto", f"PR #{n} 合并 {int(age)} 分钟了还没测", f"#{n} 已经合并 {int(age)} 分钟，还没有测试报告，现在也没有在测它。",
            "这个 PR 的问题要晚一点才知道。", auto="每 2 小时一次的兜底会补测它；24 小时后还没测上会再提醒你",
            link=f"https://github.com/{REPO}/pull/{n}")
    st["merged_checked_at"] = time.time(); st["merged_probs"] = mp; probs.update(mp)


def backup_check(now, probs):
    try: lines = open("/workspace/grokbot-box-check/sync.log", errors="replace").read().splitlines()[-400:]
    except OSError: lines = []
    res = [l for l in lines if l.startswith(("pushed", "PUSH FAILED", "no changes", "SECRET SCAN HIT"))]
    head = subprocess.run(["git", "-C", "/workspace/grokbot-box", "log", "-1", "--format=%ct"], capture_output=True, text=True).stdout.strip()
    age_h = (time.time() - int(head)) / 3600 if head.isdigit() else 99
    if res and "疑似盒子被回滚" in res[-1]:
        # 10-24: sync.sh refused to overwrite a newer backup -- the box itself was rolled back. "push again" would be the wrong advice
        probs["backup:push"] = PN.item("action", "机器疑似被回滚到了旧状态",
            "每小时的机器备份发现：GitHub 上的备份比这台机器新 2 小时以上，像是机器被平台恢复成了旧快照。为了不让旧状态盖掉好的备份，这次没推。",
            "这台机器上的脚本、状态可能都退回了旧版本，自动化可能按旧规则在跑，最近的改动也可能丢了。",
            todo=PN.ask_session("盒子疑似被回滚（/workspace/box-ops/ROLLBACK-SUSPECTED）。别强推、别跑 restore.sh，"
                                "按 /workspace/fleet-standards/pi-line/README.md「疑似回滚时怎么恢复」逐项恢复"))
    elif res and not res[-1].startswith(("pushed", "no changes")):
        secret = res[-1].startswith("SECRET SCAN HIT")
        probs["backup:push"] = PN.item("action", "机器备份没推上去" + ("（发现疑似密钥）" if secret else ""),
            ("每小时一次的机器备份，这次在要备份的文件里发现了像密钥的内容，为了不把密钥传到 GitHub，整次备份没推。" if secret
             else "每小时一次的机器备份（推到 GitHub 的 grokbot-box），最近一次没推上去。"),
            "这段时间的改动没有备份，机器再出事（像昨晚被回滚）就找不回来。",
            todo=PN.ask_session(f"grokbot-box 备份同步失败（{res[-1][:60]}），看 /workspace/grokbot-box-check/sync.log，" +
                                ("找出被拦下的文件，把密钥移出备份范围或加进排除名单，再推一次" if secret else "查原因修好，再推一次")))
    if age_h > 3: probs["backup:stale"] = PN.item("action", "机器备份停了", f"机器备份本该每小时推一次，已经 {age_h:.1f} 小时没有新的了。",
        "这段时间的改动没有备份，机器再出事（像昨晚被回滚）就找不回来。",
        todo=PN.ask_session(f"grokbot-box 备份已 {age_h:.1f} 小时没有新提交，看 /workspace/grokbot-box-check/sync.log 和 /etc/cron.d/grokbot-box-sync，查原因修好"))


CHECK_LOG = "/workspace/grokbot-box-check/check.log"
CHECK_COVERED = ("last commit", "local != origin", "sync log tail", "2 consecutive non-pushes", "cron daemon", "meta head")


def backup_rules_check(now, probs):
    """FAIL lines of the newest check.sh block that backup_check does not cover already (company code in the backup, a bot dir not
    backed up, the panoptes bundle missing / old / not verifying, the sync cron line changed); the block itself older than 3 h."""
    try: lines = open(CHECK_LOG, errors="replace").read().splitlines()[-300:]
    except OSError: return
    heads = [i for i, l in enumerate(lines) if l.startswith("# grokbot-box check ")]
    if not heads: return
    try: at = dt.datetime.strptime(" ".join(lines[heads[-1]].split()[3:5]), "%Y-%m-%d %H:%M:%S").replace(tzinfo=CST)
    except ValueError: at = None
    if at and (now - at).total_seconds() > 3 * 3600:
        probs["backup:rules-stale"] = PN.item("fyi", "备份规则检查停了（不急）", f"每小时 40 分检查备份是否合规的任务，最后一次是 {at:%m-%d %H:%M}。",
            "备份里混进公司代码、漏备份某个机器人之类的问题没人发现。",
            todo="不急，有空时" + PN.ask_session("grokbot-box check.sh（/etc/cron.d/grokbot-box-sync 的 :40 那行）超过 3 小时没跑，查一下"))
    for l in lines[heads[-1] + 1:]:
        if not l.startswith("FAIL ") or l[5:].startswith(CHECK_COVERED): continue
        rule = l[5:]; grave = "company code" in rule or "bot dir" in rule
        key = "backup:rule:" + re.sub(r"[^a-z]+", "-", rule.lower())[:40]
        probs[key] = PN.item("action" if grave else "fyi", ("机器备份里混进了公司代码" if "company code" in rule else
                             "有个机器人的数据没备份" if "bot dir" in rule else "备份规则检查有一项没过（不急）"),
            "每小时一次的备份合规检查（备份管家的规则）有一项没过。",
            "公司代码不能出现在个人 GitHub 的备份仓里，要尽快从备份里清掉。" if "company code" in rule else
            "这个机器人出事后找不回来。" if "bot dir" in rule else "备份可能不完整。",
            todo=PN.ask_session(f"grokbot-box check.sh 报 FAIL：{rule[:120]}（/workspace/grokbot-box-check/check.log），按备份管家的规则处理"
                                + ("，先别再推备份" if "company code" in rule else "")) if grave else
                 "不急，有空时" + PN.ask_session(f"grokbot-box check.sh 报 FAIL：{rule[:120]}，看一下"))


def panoptes_check(now, probs, st):
    d = "/workspace/panoptes"
    h = subprocess.run(["git", "-C", d, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    o = subprocess.run(["git", "-C", d, "rev-parse", "origin/main"], capture_output=True, text=True).stdout.strip()
    if h and o and h != o and subprocess.run(["git", "-C", d, "merge-base", "--is-ancestor", "HEAD", "origin/main"]).returncode == 0:
        since = st.setdefault("panoptes_behind_since", time.time())
        if time.time() - since > 3 * 3600: probs["panoptes:behind"] = PN.item("action", "测试系统的新代码一直没上线",
            f"测试系统（panoptes）有合并好的新代码，已经 {(time.time() - since) / 3600:.1f} 小时没部署上去。正常每小时整点 25 分会自动部署，一直没成功说明被卡住了（测试一直在跑占着锁，或部署条件不满足）。",
            "测试还在用旧代码跑，新修的东西不生效。",
            todo=PN.ask_session(f"panoptes 落后 origin/main 已 {(time.time() - since) / 3600:.1f} 小时（{h[:7]} → {o[:7]}），看 out/ops/env-*.md 的部署行，没有测试在跑就部署上去"))
    else: st.pop("panoptes_behind_since", None)
    hung_test_check(probs)


HUNG_H = 5   # a run may wait up to 4 h for the lock (--wait-lock 14400) and then test for about an hour


def nightly_runs():
    """[(age_h, cmdline)] of every run-nightly.sh process (all of panoptes' entry points go through it)."""
    out = []
    for p in (x for x in os.listdir("/proc") if x.isdigit()):
        try:
            argv = open(f"/proc/{p}/cmdline", "rb").read().decode(errors="replace").split("\0")
            if not any(a.endswith("run-nightly.sh") for a in argv[:2]): continue   # the script itself, not a command mentioning it
            cmd = " ".join(argv)
            start = int(open(f"/proc/{p}/stat").read().rsplit(")", 1)[1].split()[19]) / os.sysconf("SC_CLK_TCK")
            up = float(open("/proc/uptime").read().split()[0])
            out.append(((up - start) / 3600, cmd))
        except (OSError, ValueError, IndexError): pass
    return out


def hung_test_check(probs, runs=None):
    """10-24: the 2-hourly catch-up has no per-PR timeout -- one hung test would hold catch-up.lock / nightly.lock for good, and
    every later merge test would only queue behind it, with nothing but per-PR 'not tested yet' notices."""
    old = sorted((r for r in (nightly_runs() if runs is None else runs) if r[0] > HUNG_H), reverse=True)
    if not old: return
    h, cmd = old[0]
    pr = (re.search(r"--pr (\d+)", cmd) or [None, None])[1]
    probs["panoptes:hung"] = PN.item("action", "测试系统卡住了", f"有一次测试已经跑了 {h:.1f} 小时还没结束（{'在测 #' + pr if pr else '兜底补测'}），正常一个多小时内就结束。",
        "它占着测试锁，后面合并的 PR 都排在它后面，一个都测不了。",
        todo=PN.ask_session(f"panoptes run-nightly.sh 跑了 {h:.1f} 小时没结束（{cmd[:120]}），看 /workspace/mivo-qa/argus-root/logs/ 下的日志，"
                            "卡死了就结束它，合并即测会自动补测"))


def infra_check(now, probs):
    """10-23 infrafix line: a ticket stuck in running > 2 h; a ticket waiting for Praise (needs_human / deploy_unhealthy / ci_red)
    untouched > 12 h; a PR action not turned into a PR after 2 h (outbound?); a merged infra PR not deployed+checked after 6 h.
    Clear a needs_human ticket with `infrafix.py --reset <sig8>` (or hold it in conf/gates.json infra_hold)."""
    T = jload(f"{BASE}/out/infrafix/tickets.json", {}).get("tickets", {})
    def age_h(t, field):
        try: return (now - dt.datetime.fromisoformat(t[field])).total_seconds() / 3600
        except Exception: return 0.0
    for k, t in T.items():
        s8, st = k[5:13], t.get("status")
        what = f"测试系统的一个故障（{(t.get('title') or '')[:40]}，编号 {s8}）"
        if st == "running" and age_h(t, "started_at") > 2:
            probs[f"infra:stuck:{s8}"] = PN.item("action", "「测试系统自修」卡住了", f"它在修{what}，已经跑了 {age_h(t, 'started_at'):.1f} 小时没结束，正常一两个小时内就该有结果。",
                "这个故障没人修，后面的自修也排不上。", todo=PN.ask_session(f"infrafix 工单 {s8} 卡在 running {age_h(t, 'started_at'):.1f} 小时（pid {t.get('pid')}），看 out/logs/infrafix-*.log，卡死了就结束它并重排"))
        elif st in ("needs_human", "deploy_unhealthy", "ci_red") and age_h(t, "updated_at") > 12:
            probs[f"infra:human:{s8}"] = PN.item("action", "测试系统的一个故障等你拍板", f"「测试系统自修」处理{what}时停下来了，等人决定（原因：{(t.get('last_error') or st)[:80]}），已经等了 {age_h(t, 'updated_at'):.0f} 小时。",
                "这个故障一直不修。", todo=["决定修还是先放着", PN.ask_session(f"infrafix 工单 {s8} 等人处理（{st}）。我决定：<修 / 先放着>。修就处理完跑 infrafix.py --reset {s8}；先放着就加进 conf/gates.json 的 infra_hold")])
        elif st == "pr_action_written" and age_h(t, "updated_at") > 2:
            probs[f"infra:noprs:{s8}"] = PN.item("action", "测试系统的修复 PR 没开出来", f"「测试系统自修」已经写好了{what}的修复，要开 PR，但 {age_h(t, 'updated_at'):.0f} 小时了 PR 还没出现，多半是「消息外发」那一步卡住了。",
                "修好的代码上不去。", todo=PN.ask_session(f"infrafix 工单 {s8} 的开 PR 动作 {t.get('action_key')} 已 {age_h(t, 'updated_at'):.0f} 小时没执行，看 out/outbound/ledger.json 和 outbound 日志"))
        elif st in ("pr_open", "merge_action_written") and age_h(t, "updated_at") > 24:
            probs[f"infra:stale-pr:{s8}"] = PN.item("action", f"测试系统的修复 PR #{t.get('pr')} 卡住了", f"这个 PR 修的是{what}，已经开了 {age_h(t, 'updated_at'):.0f} 小时没有进展（自动测试没跑完，或者合并被拒）。",
                "这个故障一直没修好。", todo="打开 PR 看一下：测试红了就让会话修，没问题就合并",
                link=f"https://github.com/PraiseZhu/panoptes/pull/{t.get('pr')}")
        elif st == "merged" and age_h(t, "updated_at") > 6:
            probs[f"infra:undeployed:{s8}"] = PN.item("auto", f"测试系统的修复 PR #{t.get('pr')} 合并了还没上线", f"这个 PR 修的是{what}，合并 {age_h(t, 'updated_at'):.0f} 小时了还没部署、还没确认修好。",
                "修复还没生效。", auto="每小时整点 25 分自动部署；一直部署不上会以「测试系统的新代码一直没上线」再找你",
                link=f"https://github.com/PraiseZhu/panoptes/pull/{t.get('pr')}")


def janitor_check(now, probs):
    """10-24 plan §8 (alerts only, janitor itself never pages): disk under its low-water mark; a bot work dir without the marker
    (janitor never deletes those); trash that stays (> 1 h: rm keeps failing); once janitor enforces, a finished job's work dir
    still there after 4 h; a job holding its work dir > 4 h. "Janitor did not run" / "failed 3 times" come from job_checks."""
    r = jload(f"{BASE}/out/janitor/last.json", None)
    if not r: return
    try: age = (now - dt.datetime.fromisoformat(r["ran_at"])).total_seconds() / 60
    except Exception: return
    if age > 60: return                                    # stale report: job_checks already says the janitor stopped
    low = jload(f"{PI_CODE}/conf/janitor.json", {}).get("free_gb_low", 15)
    if r.get("free_gb") is not None and r["free_gb"] < low:
        probs["janitor:disk"] = PN.item("action", "机器磁盘快满了", f"剩余空间只有 {r['free_gb']}G（低于 {low}G）。自动清理已经进入加紧模式（回收站马上清空、缓存减到最少），还是不够。",
            "磁盘满了测试、修复、外发都会失败。", todo=PN.ask_session(f"Pi 线机器剩余空间只有 {r['free_gb']}G，查一下哪里占的（du -xh /workspace | sort -h | tail），清掉不要的"))
    if r.get("unmarked"):
        paths = sorted(u["path"] for u in r["unmarked"])
        probs["janitor:unmarked:" + hashlib.sha1("|".join(paths).encode()).hexdigest()[:8]] = PN.item("fyi", "bot 的工作目录里有来路不明的目录（不急）",
            f"{PI_WORK} 下有 {len(paths)} 个目录没有 bot 的身份标记（{', '.join(os.path.basename(p) for p in paths[:3])}）。自动清理不会删它们。",
            "只是占地方，不影响运行。", todo=f"不急，有空时看一眼：是人放的就挪走，没用就删掉（{paths[0]}）")
    if (r.get("trash_oldest_min") or 0) > 60:
        probs["janitor:trash"] = PN.item("fyi", "回收站一直清不掉（不急）", f"bot 用完扔进回收站的目录，最老的已经放了 {r['trash_oldest_min'] // 60} 小时，按理 10 分钟内就会被删掉。",
            "只是占地方。", todo=f"不急，有空时{PN.ask_session('Pi 线 janitor 的回收站一直清不掉，看 out/logs/janitor-*.log 的 purge 行')}")
    if r.get("mode") == "enforce":
        stuck = [x for x in r.get("skipped") or [] if x.get("age_h", 0) > 4 and "还在跑" not in x.get("why", "") and "正在运行" not in x.get("why", "")]
        if stuck:
            probs["janitor:stuck"] = PN.item("fyi", "有 bot 用完的目录一直清不掉（不急）", f"{len(stuck)} 个目录用完 4 小时以上了还在（{stuck[0]['path']}：{stuck[0]['why']}）。",
                "只是占地方。", todo=f"不急，有空时{PN.ask_session('Pi 线 janitor 有目录一直清不掉，看 out/janitor/last.json 的 skipped')}")
    for x in r.get("long_running") or []:
        name, hrs = os.path.basename(x["path"]), x["hours"]
        ask = PN.ask_session(f"Pi 线有个工作目录被占了 {hrs} 小时：{x['path']}，看对应作业的日志是不是卡住了")
        probs[f"janitor:long:{name}"] = PN.item("fyi", "有个修复单子跑了很久（不急）", f"{name} 已经占着工作目录 {hrs} 小时（{x['why']}）。",
            "可能卡住了，后面同类的单子会排队。", todo=f"不急，有空时{ask}")


def send(text, key, bypass):
    if DRY: print("WOULD SEND", key, "\n" + text); return
    d = today()
    record_actions("watchdog", d, [{"key": key, "type": "slack.dm", "to": "Praise", "text": text}])
    if bypass:   # outbound itself is the problem: same sender, own dedupe key (sent.json) so outbound later sees it as done
        f = f"/tmp/pi-watchdog-{os.getpid()}.txt"; open(f, "w").write(text)
        r = subprocess.run(["node", f"{QA}/report/slack/send-slack-report.mjs", "--text-file", f, "--dedupe-key", key, "--channel", "U01D851JXL7"],
                           capture_output=True, text=True, timeout=120, env={**os.environ, **secret_env_dict()})
        log("direct send rc", r.returncode, (r.stdout + r.stderr).strip()[-160:])
    else:
        r = subprocess.run([sys.executable, f"{PI_CODE}/bin/outbound.py", "--job", "watchdog", "--key", key], capture_output=True, text=True, timeout=600,
                           env={**os.environ, **secret_env_dict()})
        log("outbound kick rc", r.returncode)


# secret_env_dict() comes from lib (both secret files; 10-24: this module's own copy read only ~/.pi-fallback-secret/env, which
# holds no Slack token, so the bypass sender above could never send)
LEDGER = f"{BASE}/out/outbound/ledger.json"
OUTBOUND_KEYS = ("MIVO_SLACK_BOT_TOKEN", "MIVO_FEISHU_APP_ID", "MIVO_FEISHU_APP_SECRET")
CRED_ERR = re.compile(r"not set|invalid_auth|not_authed|token_revoked|token_expired|account_inactive|invalid_token|bad credentials"
                      r"|tenant_access_token|app_access_token|unauthori[sz]ed|\b40[13]\b", re.I)


PRFIX_TICKETS = f"{BASE}/out/praise-pr/tickets.json"
PRWATCH_STATE = f"{BASE}/out/prwatch/state.json"


def prfix_check(now, probs):
    T = jload(PRFIX_TICKETS, {}).get("tickets", {}); L = jload(LEDGER, {})
    def age_h(t):
        try: return (now - dt.datetime.fromisoformat(t.get("updated_at"))).total_seconds() / 3600
        except Exception: return 0.0
    for k, t in T.items():
        n, st_ = t.get("qa") or k.split(":")[0], t.get("status")
        what = f"你的 PR #{n} 上的一个问题（{t.get('sev') or ''}，编号 {(t.get('fp') or k.split(':', 1)[-1])[4:12]}）"
        if st_ == "running" and age_h(t) > 3:   # 10-24: a run now feeds every failed check back to the model (up to 4 fix rounds + pre-push rounds)
            probs[f"prfix:stuck:{k}"] = PN.item("action", "「修 PR bot」卡住了", f"它在修{what}，已经跑了 {age_h(t):.1f} 小时，正常 1 小时内就有结果。",
                "这个问题没人修；它占着的内存也让后面的单子排不上。",
                todo=PN.ask_session(f"prfix 工单 {k} 卡在 running {age_h(t):.1f} 小时（pid {t.get('pid')}），看 out/logs/prfix-*.log，卡死了就结束它（会自动按瞬时失败重排）"))
        elif st_ == "pr_action_written" and age_h(t) > 2 and (L.get(t.get("action_key")) or {}).get("status") != "done":
            probs[f"prfix:nopr:{k}"] = PN.item("action", "「修 PR bot」修好的修复 PR 没开出来", f"{what}已经修好、测过，要开修复 PR，但 {age_h(t):.0f} 小时了 PR 还没出现。",
                "修好的代码上不去，这个问题会一直算「在修」。",
                todo=PN.ask_session(f"prfix 工单 {k} 的开 PR 动作 {t.get('action_key')} {age_h(t):.0f} 小时没执行成功，看 out/outbound/ledger.json 和 outbound 日志（补丁打不上就重排这一单）"))
        elif st_ == "pushed" and 2 < age_h(t) < 24 and (L.get(t.get("action_key")) or {}).get("status") != "done":   # prfix --revise (10-24)
            probs[f"prfix:nopush:{k}"] = PN.item("action", "「修 PR bot」修好的 CI 修正没推上去", f"修复 PR #{t.get('pr')} 的 CI 没过，bot 已经修好，但 {age_h(t):.0f} 小时了还没推到 PR 上。",
                "这个修复 PR 会一直红着、转不了正式。",
                todo=PN.ask_session(f"prfix --revise {t.get('pr')} 的推送动作 {t.get('action_key')} {age_h(t):.0f} 小时没执行成功，看 out/outbound/ledger.json 和 outbound 日志"))
        elif st_ == "needs_praise" and 12 < age_h(t) < 36:   # kind 5 only (10-24 evening): DM'd once when it happened; one reminder, then quiet
            probs[f"prfix:human:{k}"] = PN.item("fyi", "「修 PR bot」有一个修复等你点头",
                f"{what}的修复要改 CI 配置、仓库规则或密钥相关的文件，{age_h(t):.0f} 小时前私信问过你，还没有回复。",
                "你不点头，这个修复就不开 PR。",
                todo=f"同意就跟会话说「跑 bin/run.sh prfix --qa {t.get('qa')} --fp {(t.get('fp') or '')[:12]} --allow-sensitive」；不同意不用管")
    st = jload(PRWATCH_STATE, {})
    for name, e in (st.get("errors") or {}).items():
        try: at = dt.datetime.strptime(e[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=CST)
        except ValueError: continue
        if (now - at).total_seconds() < 900:
            probs[f"prwatch:error:{name}"] = PN.item("action", "「修 PR 的接单哨兵」出错了", f"每分钟盯你 PR 新问题、派修 PR bot 的接单哨兵，「{name}」这一步在出错。",
                "你的 PR 上测出的问题可能没人接、不会自动修。", todo=PN.ask_session(f"prwatch 的 {name} 步骤出错：{e[20:140]}，看 out/logs/prwatch-*.log 修一下"))
    w = st.get("fix_wait_since")
    if w and time.time() - w > 3 * 3600:
        probs["prfix:memwait"] = PN.item("fyi", "「修 PR bot」排队等内存", f"有 {st.get('fix_pending') or '几'} 个问题等着修，但机器内存不够再开一单，已经等了 {(time.time() - w) / 3600:.1f} 小时。",
            "这些问题要晚一点才修。", todo="不用做什么，内存一空出来就会开修；想快一点，可以关掉暂时不用的会话腾出内存")


VIGIL_BAD = ("FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE", "ERROR")


def vigil_check(now, probs, st):
    if time.time() - st.get("vigil_checked_at", 0) < 1800:   # one gh call per 30 min; keep the last verdict in between
        for k, v in st.get("vigil_probs", {}).items(): probs[k] = v
        return
    try: prs = gh(["pr", "list", "-R", REPO, "--author", "PraiseZhu", "--state", "open", "--limit", "60",
                   "--json", "number,title,isDraft,updatedAt,statusCheckRollup"])
    except Exception as e: log("gh pr list (vigil) failed:", str(e)[:120]); return
    stuck = []
    for p in prs:
        if p.get("isDraft"): continue
        red = [c.get("name") or c.get("context") for c in p.get("statusCheckRollup") or []
               if ((c.get("conclusion") or c.get("state") or "").upper()) in VIGIL_BAD]
        idle = (now - parse_ts(p["updatedAt"]).astimezone(CST)).total_seconds() / 3600
        if red and idle > 4: stuck.append((idle, p["number"], (p.get("title") or "")[:40], red[:2]))
    vp = {}
    if stuck:
        stuck.sort(reverse=True)
        lst = "；".join(f"#{n}「{t}」（{'、'.join(r)} 没过，{h:.0f} 小时没动静）" for h, n, t, r in stuck[:6])
        vp["vigil:stall"] = PN.item("action", "Mac mini 盯梢那边有 PR 卡了很久", f"{len(stuck)} 个已转正式的 PR 在 GitHub 上很久没动静：{lst}。"
            "可能是盯梢停了（10-23 晚就停过 5 个多小时），也可能它在本地修得比较久、还没推上来。",
            "盯梢真停了的话，这些 PR 的审查意见、测试失败没人修，合不进去。",
            todo=["在 Mac mini 上看盯梢还在不在跑（上次停摆是 Keel 插件换了位置，见 vigil PR #48）",
                  "或者在 Cindy 里让 Mac mini 上的会话直接处理这几个 PR"])
    st["vigil_checked_at"] = time.time(); st["vigil_probs"] = vp; probs.update(vp)


def outbound_health_check(now, probs):
    env = {**secret_env_dict(), **{k: os.environ[k] for k in OUTBOUND_KEYS if os.environ.get(k)}}
    miss = [k for k in OUTBOUND_KEYS if not env.get(k)]
    if miss:
        probs["creds:outbound"] = PN.item("action", "「消息外发」的密钥不见了",
            f"发 Slack 和飞书要用的密钥缺了 {len(miss)} 个，所有私信、群消息、日报都发不出去。",
            "所有通知都发不出去；缺的是 Slack 的话，这条提醒本身也未必能到你手上。",
            todo=PN.ask_session(f"Pi 线外发密钥缺失：{', '.join(miss)}。补进 ~/.config/mivo-qa/secrets.env（权限 600），再跑 bin/run.sh credcheck 确认"))
    L = jload(LEDGER, {})
    bad = {k: v for k, v in L.items() if isinstance(v, dict) and v.get("status") == "failed"}
    cred = sorted(k for k, v in bad.items() if CRED_ERR.search(v.get("last_error") or ""))
    stuck = sorted(k for k, v in bad.items() if (v.get("fails") or 0) >= 2 and k not in cred)
    if cred:
        e = (bad[cred[0]].get("last_error") or "")[:100]
        probs["failing:outbound:creds"] = PN.item("action", "「消息外发」登录不上 Slack 或飞书",
            f"有 {len(cred)} 条消息因为密钥或登录失败发不出去。",
            "这些消息会一直重试，失败 5 次就放弃；之后的新消息也一样发不出去。",
            todo=PN.ask_session(f"Pi 线外发 {len(cred)} 条因密钥/登录失败（例 {cred[0]}：{e}），跑 bin/run.sh credcheck 查哪个密钥坏了，修好后它们会自动重发"))
    if stuck:
        e = (bad[stuck[0]].get("last_error") or "")[:100]
        probs["failing:outbound:actions"] = PN.item("action", f"「消息外发」有 {len(stuck)} 条一直发不出去",
            f"这些消息或自动操作已经各失败 2 次以上，还在重试。",
            "它们还没发出去，失败 5 次就会放弃。",
            todo=PN.ask_session(f"Pi 线外发有 {len(stuck)} 条一直失败（例 {stuck[0]}：{e}），看 out/outbound/ledger.json 和 outbound 日志查原因"))


def main():
    if mode() != "pi": return 0
    import fcntl
    lk = open("/tmp/pi-fallback-watchdog.lock", "w")
    try: fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError: return 0
    now = now_cst(); st = jload(STATE, {}); probs = {}
    since = dt.datetime.fromisoformat(st.setdefault("installed_at", now.isoformat(timespec="seconds")))
    for fn in (lambda: job_checks(now, probs, since), lambda: mergewatch_check(now, probs), lambda: merge_report_check(now, probs, st),
               lambda: backup_check(now, probs), lambda: backup_rules_check(now, probs), lambda: panoptes_check(now, probs, st),
               lambda: infra_check(now, probs), lambda: prfix_check(now, probs), lambda: vigil_check(now, probs, st),
               lambda: outbound_health_check(now, probs), lambda: janitor_check(now, probs)):
        try: fn()
        except Exception as e: probs[f"watchdog:{getattr(fn, '__name__', 'check')}"] = PN.item("action", "「看门狗」自己出错了",
            "专门盯各个自动化有没有停摆的看门狗，自己的一项检查出错了。", "那一项坏了也不会有人告诉你。",
            todo=PN.ask_session(f"Pi 线 watchdog.py 检查出错：{str(e)[:100]}，修一下"))
    # debounce: a problem must be seen on 2 consecutive runs (10 min) before it is reported -- first runs, cron races and
    # one-off hiccups never page Praise (10-23 21:25: a deploy race paged "prwatch heartbeat stale" once). Recovery: at once.
    last = st.get("last_seen", {}); st["last_seen"] = dict(probs)
    prev = st.get("open", {})
    probs = {k: v for k, v in probs.items() if k in last or k in prev}
    new = {k: v for k, v in probs.items() if k not in prev}; gone = [k for k in prev if k not in st["last_seen"]]
    remind = probs and time.time() - st.get("sent_at", 0) > 86400 and not new
    if DRY:
        print(json.dumps({"open": probs, "new": list(new), "recovered": gone}, ensure_ascii=False, indent=1))
    # 2026-10-24（Praise：私信看不懂）：每件事按 praise_notice 的骨架写；恢复只告诉他之前要他动手的那些（系统自己处理的恢复了不打扰）；
    # 还没解决的老问题只在末尾列标题；24 小时提醒把还没解决的重新完整写一遍
    def as_item(v, key):
        return v if isinstance(v, dict) else PN.item("action", str(v)[:60], str(v), todo=PN.ask_session(f"Pi 线看门狗报的问题：{v}，查一下"))
    told_gone = [k for k in gone if as_item(prev[k], k)["kind"] == "action"]
    if new or told_gone or remind:
        items = [as_item(v, k) for k, v in (probs.items() if remind else new.items())]
        for k in told_gone:
            old = as_item(prev[k], k)
            items.append(PN.item("resolved", old["title"].replace("（不急）", ""), f"之前告诉你的「{old['title']}」，现在好了。"))
        others = [] if remind else [as_item(v, k)["title"] for k, v in probs.items() if k not in new]
        lines = [PN.text(*items, still_open=others)]
        key = f"watchdog:{now.strftime('%Y-%m-%d:%H%M')}"
        bypass = any(k.endswith(":outbound") or k.startswith(("failing:outbound", "stale:outbound")) for k in probs)
        if not DRY:
            day_log = f"{LOGS}/watchdog-{today()}.log"
            with open(day_log, "a") as f: f.write(f"{now.strftime('%F %T')} new={list(new)} recovered={gone} open={len(probs)}\n")
        send("\n".join(lines), key, bypass)
        st["sent_at"] = time.time()
    st["open"] = probs; st["last_run"] = now.isoformat(timespec="seconds")
    if not DRY:   # quiet atomic write (lib.write_atomic logs every call; this runs every 5 min)
        os.makedirs(os.path.dirname(STATE), exist_ok=True); tmp = f"{STATE}.{os.getpid()}"
        open(tmp, "w").write(json.dumps(st, ensure_ascii=False, indent=1)); os.replace(tmp, STATE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
