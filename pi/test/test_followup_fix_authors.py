"""python3 test/test_followup_fix_authors.py — 11-04: followup.to_actions never makes a reminder DM for a finding_policy
FIX_AUTHORS author (PraiseZhu, kirozeng: Praise PR fixes their findings); other authors are still reminded. Temp files only."""
import sys, os, json, tempfile, unittest
B = os.environ.get("PI_BIN") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"); sys.path.insert(0, B)

class FollowupFixAuthors(unittest.TestCase):
    def test_no_remind_for_fix_authors(self):
        import followup as F, finding_policy as FP
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(f"{d}/qa")
            json.dump({"mivo_authors": [{"github_login": l, "feishu_open_id": f"ou_{l}"} for l in (*FP.FIX_AUTHORS, "alice")]},
                      open(f"{d}/qa/author-map.json", "w"))
            os.environ["PI_STATE"] = d   # no fix-authors-praise-only rollback flag here
            old = F.QA; F.QA = f"{d}/qa"
            try:
                dms = [{"open_id": f"ou_{l}", "dedupe_key": f"followup-remind:{l}:2026-11-04", "text": "t"} for l in (*FP.FIX_AUTHORS, "alice")]
                acts = F.to_actions(dms, "remind")
            finally: F.QA = old
        self.assertEqual([a["to"] for a in acts], ["alice"])

if __name__ == "__main__": unittest.main()
