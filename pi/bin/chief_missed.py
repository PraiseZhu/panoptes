#!/usr/bin/env python3
"""给 Chief（Jasper）汇总里的「漏项」两节（Praise 2026-10-26 定的口径）。零模型、只读本地文件和 gh，不发送。
只列四样都齐的：已核实、有负责人、有期限、有证据（路径或 PR/issue 链接）。AI 单方面提出、没复核的（pending_verification、
ai-test / coverage-gate 这类待核实来源）不列。日报（out/daily/<数据日>.md）里已经写过的不重复列。
- 系统漏跑：看门狗当天日志 new=[...] 里的 missed:/failed:<作业>:<HHMM>（看门狗连看两轮才报，算已核实），加 daily 崩溃。
- 工作逾期：复用 pm.py / pm_extra 的遗漏清单（out/pm/<数据日>.json 的 missing）里「第三轮未报出」「交接 …#N 超 72h 未见逾期提醒」，
  再用最新台账 / gh 复核一遍此刻是否仍开着（已修且复测通过、issue 已关的不列）。
用法：chief_missed.py [YYYY-MM-DD]   打印该天 chief.md 要追加的两节（不写文件）。"""
import os, re, sys, glob, json, datetime as dt
from lib import BASE, QA, REPO, CST, gh

ROUTES = "/workspace/panoptes/report/handoff-routes.json"
UNVERIFIED_SOURCES = {"ai-test", "coverage-gate", "own-tests", "qa-error"}
CLOSED = {"fixed", "concluded"}

def _read(p): return open(p).read() if p and os.path.exists(p) else ""

def watchdog_misses(day, base=BASE):
    """当天看门狗日志里报过的 missed:/failed: 键 → [(key, job, HHMM, kind, recovered)]"""
    lg = f"{base}/out/logs/watchdog-{day}.log"; seen = {}
    for line in _read(lg).splitlines():
        m = re.search(r"new=\[(.*?)\] recovered=\[(.*?)\]", line)
        if not m: continue
        for k in re.findall(r"'([^']+)'", m.group(1)):
            km = re.fullmatch(r"(missed|failed):([a-z0-9_-]+):(\d{4})", k)
            if km: seen[k] = [km.group(2), km.group(3), km.group(1), False]
        for k in re.findall(r"'([^']+)'", m.group(2)):
            if k in seen: seen[k][3] = True
    return lg, [(k, *v) for k, v in seen.items()]

def system_section(day, daily_txt, base=BASE, crash=None):
    lg, ms = watchdog_misses(day, base)
    try: from praise_notice import job_info
    except Exception: job_info = lambda j: {"name": j}
    nxt = (dt.date.fromisoformat(day) + dt.timedelta(days=1)).isoformat()
    out = []
    for k, job, hm, kind, rec in sorted(ms, key=lambda x: x[2]):
        if job == "daily" and crash: continue   # 同一件事，下面按崩溃写
        if re.search(rf"{re.escape(job)}[^\n]*(漏跑|没启动|没成功)", daily_txt): continue
        jl = f"{base}/out/logs/{job}-{day}.log"
        ev = f"{lg}（{k}）" + (f"、{jl}" if os.path.exists(jl) else "")
        what = "没启动" if kind == "missed" else "跑了没成功"
        out.append(f"- {k}：「{job_info(job)['name']}」{hm[:2]}:{hm[2:]} 那轮{what}{'（之后已恢复）' if rec else ''}。"
                   f"负责人：Pi 线运维（{job} 作业）；期限：下一轮 {nxt} {hm[:2]}:{hm[2:]} 前修好；证据：{ev}")
    if crash and not re.search(r"日报[^\n]*崩溃", daily_txt):
        out.append(f"- daily 崩溃：{day} 08:35 的日报运行 rc≠0，日报和私信没出。负责人：Pi 线运维（daily 作业）；"
                   f"期限：下一轮 {nxt} 08:35 前修好；证据：{crash}")
    return out

def _ledger(day):
    fs = [f for f in sorted(glob.glob(f"{QA}/ledger/daily/*.json")) if os.path.basename(f)[:10] <= day]
    return (fs[-1], json.load(open(fs[-1]))) if fs else (None, {"rows": []})

def _open_row(r):
    return (r.get("level") in ("P0", "P1", "P2") and r.get("source") not in UNVERIFIED_SOURCES
            and r.get("status") not in CLOSED and r.get("retest") != "pass")

def work_section(day, data_day, miss, daily_txt, issue_state=None):
    """miss = pm 遗漏清单；issue_state(repo, n) -> dict(state, createdAt, url) 或 None（gh 查不到就不列：未核实）"""
    led_f, led = _ledger(day)
    routes = {r["repo"]: r for r in json.load(open(ROUTES))["routes"]} if os.path.exists(ROUTES) else {}
    nxt = (dt.date.fromisoformat(day) + dt.timedelta(days=1)).isoformat()
    out = []
    for x in miss:
        m = re.match(r"第三轮未报出：#(\d+)", x)
        if m:
            n = int(m.group(1))
            rows = [r for r in led["rows"] if r.get("pr") == n and (r.get("fix_round") or 0) >= 3 and _open_row(r)]
            if not rows: continue   # 已修且复测通过 / 已结论 / 只剩待核实来源
            if re.search(rf"#{n}\b[^\n]*(第三轮|第 3 轮|round ?3|转人工)", daily_txt): continue
            r = rows[0]
            ev = f"https://github.com/{REPO}/pull/{n}、{led_f}" + (f"、修复 https://github.com/{REPO}/pull/{r['retest_pr']}" if r.get("retest_pr") else "")
            out.append(f"- #{n} {r['level']} 修到第 {r['fix_round']} 轮仍{r.get('status_zh') or r.get('status')}，没进日报「需要你做」。"
                       f"负责人：Mivo QA 日报；期限：{nxt} 08:35 日报里升级；证据：{ev}")
            continue
        m = re.match(r"交接 ([\w.-]+/[\w.-]+)#(\d+) 超 72h 未见逾期提醒", x)
        if m:
            repo, n = m.group(1), int(m.group(2)); i = issue_state(repo, n) if issue_state else None
            if not i or i.get("state") != "OPEN": continue
            if re.search(rf"{re.escape(repo)}(#|/issues/){n}\b", daily_txt): continue
            due = dt.datetime.fromisoformat(i["createdAt"].replace("Z", "+00:00")).astimezone(CST) + dt.timedelta(hours=72)
            owner = (routes.get(repo) or {}).get("owner")
            if not owner: continue
            out.append(f"- {repo}#{n}（{i.get('title', '')[:40]}）超 72h 仍开，没人提醒逾期。负责人：{owner}（交接仓 owner），"
                       f"提醒由交接扫描发；期限：{due.strftime('%m-%d %H:%M')} 已过；证据：{i.get('url') or f'https://github.com/{repo}/issues/{n}'}、out/pm/{data_day}.json")
    return out

def gh_issue(repo, n):
    try: return gh(["issue", "view", str(n), "-R", repo, "--json", "state,createdAt,url,title"])
    except Exception: return None

def pm_missing(day, od):
    """数据日 = day-1；pm 那天没跑（如 missed:pm:1035）就退回最近一份，返回 (实际数据日, missing)"""
    prv = (dt.date.fromisoformat(day) - dt.timedelta(days=1)).isoformat()
    fs = [f for f in sorted(glob.glob(f"{od}/????-??-??.json")) if os.path.basename(f)[:10] <= prv]
    if not fs: return None, []
    return os.path.basename(fs[-1])[:10], json.load(open(fs[-1])).get("missing", [])

def sections(day, base=BASE, issue_state=gh_issue):
    import pm
    prv = (dt.date.fromisoformat(day) - dt.timedelta(days=1)).isoformat()
    dtf, dtxt = pm.daily_text(prv)
    data_day, miss = pm_missing(day, f"{base}/out/pm")
    sysl = system_section(day, dtxt, base, crash=pm.daily_crash(prv))
    work = work_section(day, data_day or prv, miss, dtxt, issue_state)
    L = ["", "## 漏项（只列已核实、有负责人、有期限、有证据的；日报已写过的不重复）",
         f"来源：看门狗 out/logs/watchdog-{day}.log；PM 遗漏清单 out/pm/{data_day or '（无）'}.json" +
         (f"（{prv} 那份没有，用的是最近一份并已按此刻台账 / gh 复核）" if data_day and data_day != prv else "") + f"；去重对照 {dtf}。", "",
         "### 系统漏跑"] + (sysl or ["- 无"]) + ["", "### 工作逾期"] + (work or ["- 无"]) + [""]
    return "\n".join(L)

if __name__ == "__main__":
    print(sections(sys.argv[1] if len(sys.argv) > 1 else dt.datetime.now(CST).date().isoformat()))
