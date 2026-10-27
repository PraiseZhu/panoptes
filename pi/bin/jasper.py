#!/usr/bin/env python3
"""Jasper 09:00 brief (fleet part) + fleet check: verify each bot's latest evidence on disk/gh, then 'work' tier
writes a short Chinese brief from those facts only. Slack/email/calendar scanning can't move (connectors)."""
from lib import *
def age(p):
    try: return round((dt.datetime.now().timestamp() - os.path.getmtime(p)) / 3600, 1)
    except Exception: return None
def jload_(f, dflt):
    try: return json.load(open(f))
    except Exception: return dflt
def main():
    d = sys.argv[1] if len(sys.argv) > 1 else today(); y = (dt.date.fromisoformat(d) - dt.timedelta(days=1)).isoformat()
    backfill = d != today()   # 补过去的日子：只写文件，不记外发（不发 DM）
    sj = json.load(open(f"{QA}/report/slack/sent.json"))
    facts = {
      "Mivo QA 日报": {"slack_sent_for_yesterday": (sj.get(f"mivo-daily-report:{y}") or {}).get("sent_at"),
                     "ledger_latest": sorted(glob.glob(f"{QA}/ledger/daily/*.json"))[-1]},
      "Mivo 测试项目经理": {"pm_daily_yesterday_exists": os.path.exists(f"/workspace/fleet-standards/pm-daily/{y}.md")},
      "Mivo QA 运维": {"review_ops_latest": sorted(glob.glob("/workspace/review-ops/daily/*.md"))[-1],
                     "catch_up_log_age_h": age("/workspace/mivo-qa/argus-root/logs/catch-up-cron.log")},
      "Mivo 合并官": {"PAUSED": os.path.exists("/workspace/mivo-merger/PAUSED"), "audit_age_h": age("/workspace/mivo-merger/audit.log")},
      "备份管家": {"sync_log_age_h": age("/workspace/grokbot-box-check/sync.log")},
      "电脑运维": {"memlog_age_h": age("/workspace/box-ops/mem.log")},
      "pi_line_outputs": sorted(os.path.relpath(p, BASE) for p in glob.glob(f"{BASE}/out/*/{y}*") + glob.glob(f"{BASE}/out/*/{d}*"))[:60],
    }
    if mode() == "pi":   # 10-23: Pi line is the production line -> its outputs are the bots' deliveries
        tk = jload_(f"{BASE}/out/praise-pr/tickets.json", {}).get("tickets", {}); wd = jload_(f"{BASE}/out/watchdog/state.json", {})
        sync = [l for l in open("/workspace/grokbot-box-check/sync.log", errors="replace").read().splitlines()[-300:] if l.startswith(("pushed", "PUSH FAILED", "no changes"))] if os.path.exists("/workspace/grokbot-box-check/sync.log") else []
        facts["pi_line_evidence"] = {
            "Mivo QA 日报": {"daily_md": os.path.exists(f"{BASE}/out/daily/{y}.md"), "slack_sent_for_yesterday": facts["Mivo QA 日报"]["slack_sent_for_yesterday"]},
            "Mivo 测试项目经理": {"pm_md": os.path.exists(f"{BASE}/out/pm/{y}.md") or os.path.exists(f"{BASE}/out/pm/{d}.md")},
            "Mivo QA 运维": {"ops_review_md": os.path.exists(f"{BASE}/out/ops/review-{y}.md"), "mergewatch_last_check": jload_(f"{BASE}/out/mergewatch/state.json", {}).get("last_check"),
                           "reports_merged_count": len(glob.glob(f"{RPT}/PR-*.json"))},
            "Mivo 合并官": {"merger_md_age_h": age(f"{BASE}/out/merger/{d}.md") or age(f"{BASE}/out/merger/{y}.md"), "note": "STOP identity until a repo-only token is provided (Praise decision)"},
            "Praise PR": {"tickets_by_status": {k: sum(1 for v in tk.values() if isinstance(v, dict) and v.get("status") == k) for k in ("queued", "pr_action_written", "norepro", "needs_human", "failed")},
                          "prwatch_last_check": jload_(f"{BASE}/out/prwatch/state.json", {}).get("last_check")},
            "备份管家": {"last_sync_result": sync[-1] if sync else None, "sync_log_age_h": facts["备份管家"]["sync_log_age_h"]},
            "电脑运维": {"memlog_age_h": facts["电脑运维"]["memlog_age_h"], "watchdog_open_problems": list((wd.get("open") or {}).values())[:8]},
        }
    ydaily = f"{BASE}/out/daily/{y}.md"
    if os.path.exists(ydaily): facts["pi_daily_headline"] = open(ydaily).read().splitlines()[1]
    pm = f"{BASE}/out/pm/{y}.md"
    if os.path.exists(pm): facts["pi_pm_goals"] = [l for l in open(pm).read().splitlines() if l.startswith("## G")]
    fj = f"{BASE}/out/jasper/{d}.facts.json"; write_atomic(fj, json.dumps(facts, ensure_ascii=False, indent=1))
    RULE = ("fleet-mode=pi (since 2026-10-23): the Pi line (code /workspace/panoptes/pi, state /workspace/mivo-qa/pi) IS the production line; judge each bot by "
            "facts.pi_line_evidence (its outputs are the deliveries). Grok-line fields (fleet-standards/pm-daily, review-ops, mivo-merger "
            "audit.log) are expected to be stale in pi mode and are NOT failures." if mode() == "pi" else
            'Paths under /workspace/mivo-qa/pi or "pi_line_outputs" are the SHADOW Pi line, never evidence that a Grok-line bot delivered; '
            "judge each bot only by its own Grok-line fields.")
    if os.environ.get("JASPER_FACTS_ONLY") == "1": print(json.dumps(facts.get("pi_line_evidence"), ensure_ascii=False, indent=1)); return
    try:
        txt, tid = pi("work", "/tmp", f"""Write Praise's 9:00 fleet brief in Chinese (<=15 lines) using ONLY facts in {fj} (read it; you may also read files it names, read-only).
Format: 一句话总览；然后每个 bot 一行：状态 ✓/⚠ + 证据路径；最后「需要你定」(only if facts show something needs a decision; else 无).
Never guess; if a fact is missing say 未核实. {RULE} Today {d}, data day {y} (Beijing time).""", f"pi-line-jasper-{d}", timeout=400)
    except Exception as e: txt, tid = f"brief 生成失败：{e}", "?"
    write_atomic(f"{BASE}/out/jasper/{d}.md", f"<!-- Pi line brief, task {tid}, tier work; Slack/邮件/日历部分无法覆盖（需连接器） -->\n{txt.strip()}\n")
    if backfill: return
    key = f"brief:{d}"   # 补跑 / 重跑同一天：账本或待发清单里已有这条就不再记外发（只发一次）
    if key in jload_(f"{BASE}/out/outbound/ledger.json", {}) or any(a.get("key") == key for a in jload_(f"{BASE}/out/jasper/{d}.actions.json", {}).get("actions", [])):
        print(f"jasper: {key} already in ledger/actions -> not sent again"); return
    record_actions("jasper", d, [{"key": f"brief:{d}", "type": "grokbot.chat-to-Praise", "body_file": f"{BASE}/out/jasper/{d}.md"}])
if __name__ == "__main__": main()
