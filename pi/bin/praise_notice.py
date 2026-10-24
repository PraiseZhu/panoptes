#!/usr/bin/env python3
"""给 Praise 的 Slack 私信：统一分类和骨架（Praise 2026-10-24：之前的私信一条都看不懂 —— 不知道发生了什么、哪个功能坏了、要他做什么）。

四类，标题前缀固定：
  action    🔴 要你处理      他不动手，事情就会一直卡着；「你要做」必须写成具体动作
  auto      🟡 出问题了，系统在自己处理   告诉他一声，不用动手；写清系统在做什么、什么情况下会再找他
  resolved  🟢 已恢复        只给之前让他动手（action）的问题发；系统自己处理的问题恢复了不单独打扰
  fyi       🔵 告诉你一声    进展 / 汇总，不用做什么

骨架（顺序固定，每行一句口语，不写内部代号；代号、路径只放在「你要做」里给会话看的那句话里）：
  <前缀>｜<标题：哪个功能出了什么事>
  发生了什么：…
  影响：…（不处理会怎样；没有就省略）
  你要做：…（action 必填；fyi 写「不用做什么」）/ 系统在做：…（auto）
  详情：<链接>（可选，只放他点得开的 GitHub 链接）

Slack 私信里回复不了（应用关了收消息），所以「你要做」里需要会话动手的，写成一句能直接复制给 Cindy 里会话的话（ask_session）。
"""

KINDS = {
    "action": "🔴 要你处理",
    "auto": "🟡 出问题了，系统在自己处理",
    "resolved": "🟢 已恢复",
    "fyi": "🔵 告诉你一声",
}
ORDER = ["action", "auto", "resolved", "fyi"]

# Pi 线作业 → (功能名, 它平时做什么, 它停了会怎样, 是否关键)
JOBS = {
    "outbound": ("消息外发", "把各个机器人要发的私信、群消息和 GitHub 操作真正发出去", "所有通知和自动操作都卡住、发不出去", True),
    "daily": ("QA 日报", "每天 8:35 出昨天合并 PR 的测试日报，并给作者发私信", "今天的日报和给作者的私信都出不来", True),
    "mergewatch": ("合并即测", "PR 一合并，1 分钟内触发测试", "新合并的 PR 要等每 2 小时一次的兜底才会测", True),
    "prwatch": ("修 PR 的接单哨兵", "盯你自己 PR 上测出的新问题、派修 PR bot 去修", "你的 PR 上测出的问题没人接、不会自动修", True),
    "prfix": ("修 PR bot", "给你自己 PR 上测出的问题写复现测试、开修复 PR", "你的 PR 上的问题不会被自动修", True),
    "opsenv": ("测试系统自检和自动部署", "每小时检查测试系统环境，把 panoptes 的新代码部署上线", "测试系统的新代码上不了线、环境坏了没人发现", True),
    "opsdeploy": ("测试系统自动上线", "每 10 分钟看测试系统有没有新合并的代码，空闲时就部署上线", "测试系统的修复要等每小时一次的自检才上线，碰上测试在跑还要再等一小时", False),
    "followup": ("作者跟进提醒", "提醒作者处理问题、感谢已经回复的作者", "作者没人提醒，回复了也不会被收起", True),
    "handoff": ("交接仓回执扫描", "扫严健、钟行天交接仓里作者的回复", "作者回了也认不出来，会被误算逾期", True),
    "ledger": ("问题台账", "每晚 23:40 重建所有问题的跟踪台账", "第二天的日报和修复派活用的是旧台账", True),
    "cindy": ("Cindy 保活", "每 5 分钟检查 Cindy 还活着，挂了自动拉起", "Cindy 挂了不会被自动拉起来", True),
    "merger": ("自动合并", "判断哪些 PR 可以合并并合并", "能合的 PR 不会被自动合并", True),
    "infrafix": ("测试系统自修", "发现测试系统自己的故障后写测试、开修复 PR", "测试系统自己的毛病没人修", True),
    "boxhealth": ("机器体检", "每小时看内存、进程、磁盘，能修的自动修", "机器出问题没人发现", False),
    "boxcheck": ("机器巡检", "每小时只读检查机器状态", "机器出问题晚一点才发现", False),
    "credcheck": ("凭证检查", "每天 7 点检查各种 token 还有没有效", "token 过期了没人提前发现", False),
    "jasper": ("每日简报", "每天 9:05 整理给你的工作简报", "今天没有简报", False),
    "pm": ("测试项目经理核对", "每天 10:35 核对 QA 的各项目标", "今天没人核对 QA 目标", False),
    "pmnudge": ("白天催办", "白天每 2 小时看修复 PR 有没有进展", "修复 PR 卡住了没人催", False),
    "opsfix": ("修复复测", "修复 PR 合并后用原来的用例重测", "修好的问题没人复测确认", False),
    "opsreview": ("运维复盘", "每天 11:15 复盘测试系统前一天的表现", "今天没有运维复盘", False),
    "prretro": ("修 PR 复盘", "每天 9:45 复盘修 PR bot 前一天的工作", "今天没有修 PR 复盘", False),
    "dailyretro": ("日报复盘", "每天 9:56 复盘日报", "今天没有日报复盘", False),
    "p2": ("P2 裁决提议", "每天两次给 P2 问题提裁决建议", "P2 问题没人提裁决建议", False),
    "compare": ("Pi 线和 Grok 线对比", "每天对比两条线的产出", "今天没有对比", False),
    "watchdog": ("看门狗", "每 5 分钟检查各个自动化有没有停摆", "自动化停了没人发现", True),
    "janitor": ("自动清理", "每 10 分钟清掉 bot 用完的工作目录、临时文件和过期的测试证据", "bot 用完的目录和过期文件越积越多，时间长了磁盘会满", False),
}


def job_name(job):
    return JOBS.get(job, (job, "", "", False))[0]


def job_info(job):
    j = JOBS.get(job)
    return {"name": j[0], "does": j[1], "if_down": j[2], "critical": j[3]} if j else {"name": job, "does": "", "if_down": "", "critical": False}


def ask_session(text):
    """「你要做」里需要会话动手时的写法：一句能直接复制给 Cindy 里会话的话。"""
    return f"在 Cindy 里找修测试的会话（或新开一个），把这句话发给它：「{text}」"


def item(kind, title, what, impact=None, todo=None, auto=None, link=None):
    if kind not in KINDS: raise ValueError(f"unknown notice kind {kind}")
    if kind == "action" and not todo: raise ValueError("an action notice needs todo")
    if kind == "auto" and not auto: raise ValueError("an auto notice needs auto")
    return {"kind": kind, "title": title, "what": what, "impact": impact, "todo": todo, "auto": auto, "link": link}


def _todo_lines(todo):
    if isinstance(todo, (list, tuple)):
        if len(todo) == 1: return [f"你要做：{todo[0]}"]
        return ["你要做："] + [f"{i}. {t}" for i, t in enumerate(todo, 1)]
    return [f"你要做：{todo}"]


def render(it):
    """一件事 → 按骨架的几行文字。"""
    k = it["kind"]
    out = [f"{KINDS[k]}｜{it['title']}", f"发生了什么：{it['what']}"]
    if it.get("impact"): out.append(f"影响：{it['impact']}")
    if k == "action": out += _todo_lines(it["todo"])
    elif k == "auto": out.append(f"系统在做：{it['auto']}"); out.append("你要做：不用动手。")
    elif k == "resolved": out += _todo_lines(it["todo"]) if it.get("todo") else ["你要做：不用管了。"]
    else: out += _todo_lines(it["todo"]) if it.get("todo") else ["你要做：不用做什么。"]
    if it.get("link"): out.append(f"详情：{it['link']}")
    return "\n".join(out)


def text(*items, still_open=()):
    """一条私信：一件事就是它自己；几件事按「要你处理 → 系统在处理 → 已恢复 → 告诉你一声」排，开头一句总数。
    still_open：之前说过、还没解决的事，只在末尾列标题，不重复全文。"""
    its = sorted([i for i in items if i], key=lambda i: ORDER.index(i["kind"]))
    if not its and not still_open: return ""
    parts = []
    if len(its) > 1:
        n = sum(1 for i in its if i["kind"] == "action")
        parts.append(f"{len(its)} 件事" + (f"，其中 {n} 件要你处理" if n else "，都不用你动手") + "：")
    parts += [render(i) for i in its]
    if still_open:
        parts.append("之前说过、还没解决的：" + "；".join(still_open))
    return "\n\n".join(parts)


if __name__ == "__main__":
    print(text(item("action", "测试：标题", "什么事", "会怎样", todo=ask_session("看一下")), item("fyi", "进展", "做完了")))
