#!/usr/bin/env python3
"""PM 11-03: QA findings on kirozeng's PRs are followed up by Praise PR (handoff-routes.json praise_pr_authors).
Daily report: no @, no DM. track-followups: no DM, overdue notice says 「由 Praise PR 跟进（排队中）」 with no @.
Temp files and a fake gh only; nothing is sent. Run: python3 pi/test/test_kiro_praise_pr.py"""
for _k in [k for k in __import__("os").environ if k.startswith("GIT_")]: del __import__("os").environ[_k]
import os, sys, json, tempfile, subprocess, unittest
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "pi", "bin"))
import daily as D  # noqa: E402

KIRO, PR = "kirozeng", 1449
HC = {"verified": 0, "failed": 0, "capped": 0, "not-run": 0}

def kiro_item():
    f = {"severity": "P1", "title": "画布保存丢失", "test_backed": True, "source": "check",
         "evidence": {"recommendation": "保存前先 flush 队列"}}
    return {"n": PR, "title": "fix canvas", "login": KIRO, "who": "Kiro", "a": {"notify_via_feishu": True}, "merged": "2026-11-02T03:00:00Z",
            "sev": "P1", "real": [f], "leads": [], "hc": HC, "manual": 0, "failed_checks": [], "code_review_p3": False, "docs": False}

class Daily(unittest.TestCase):
    def render(self):
        D.SENT = tempfile.mkdtemp()
        return D.render("2026-11-02", [kiro_item()], [], [], {})
    def test_daily_entry_no_at_praise_pr_followup(self):
        txt = self.render()[0]
        self.assertNotIn("@Kiro", txt)
        self.assertIn(f"#{PR} fix canvas — 由 Praise PR 跟进（排队中）", txt)
        self.assertNotIn("下一步", txt)
    def test_daily_no_dm_for_kiro(self):
        txt, *_, dms = self.render()
        self.assertEqual([i["login"] for _, i, _ in dms], [])
        self.assertNotIn("已私信通知", txt)

def dump(p, x):
    with open(p, "w") as fh: fh.write(x if isinstance(x, str) else json.dumps(x))

def run_followups(*flags):
    d = tempfile.mkdtemp(); qa = f"{d}/qa"; bin_ = f"{d}/bin"
    for p in ("reports/merged", "followup", "report/slack", "report/sent"): os.makedirs(f"{qa}/{p}")
    os.makedirs(bin_)
    dump(f"{bin_}/gh", "#!/bin/sh\ncase \"$*\" in *--paginate*) exit 0;; *) echo '[]';; esac\n"); os.chmod(f"{bin_}/gh", 0o755)
    f = {"severity": "P1", "title": "画布保存丢失", "fingerprint": "fp1", "source": "stated-acceptance", "file": "src/a.ts"}
    dump(f"{qa}/reports/merged/PR-{PR}.json", {"prs": [{"number": PR, "author": KIRO, "title": "fix canvas", "merged_at": "2026-10-28T03:00:00Z", "findings": [f]}]})
    dump(f"{qa}/author-map.json", {"mivo_authors": [{"github_login": KIRO, "name": "Kiro", "feishu_open_id": "ou_kiro", "notify_via_feishu": True}]})
    dump(f"{qa}/feishu-sent.json", {f"xindong/mivo-canvas-plugin#{PR}:fp1": {"open_id": "ou_kiro", "sent_at": "2026-10-28T04:00:00Z", "message_id": "om_1"}})
    env = dict(os.environ, QA_HOME=qa, PATH=f"{bin_}:{os.environ['PATH']}", MIVO_QA_HANDOFF_REPOS="",
               MIVO_QA_FOLLOWUP_STATE=f"{qa}/followup/state.json", MIVO_QA_FOLLOWUP_HOLDS=f"{qa}/followup/holds.json")
    r = subprocess.run(["node", os.path.join(ROOT, "report", "track-followups.mjs"), "--dry-run", *flags],
                       capture_output=True, text=True, env=env, timeout=120)
    assert r.returncode == 0, r.stderr[-800:]
    return json.loads(r.stdout[r.stdout.index("{"):])

class Followups(unittest.TestCase):
    def test_followup_sends_no_dm_to_kiro(self):
        s = run_followups()
        self.assertEqual(s["drafts"], []); self.assertEqual(s["reminded"], []); self.assertEqual(s["escalated"], [])
    def test_overdue_notice_praise_pr_wording(self):
        s = run_followups("--group-overdue", "--overdue-only")
        text = (s.get("overdue") or {}).get("text") or ""
        self.assertIn(f"#{PR} fix canvas — 由 Praise PR 跟进（排队中）", text)
        self.assertNotIn("@Kiro", text)

if __name__ == "__main__": unittest.main(verbosity=2)
