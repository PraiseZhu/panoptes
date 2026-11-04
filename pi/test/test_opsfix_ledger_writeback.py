"""python3 test/test_opsfix_ledger_writeback.py (or pytest) — 11-04 retest write-back incident:
1) ledger_path() took a hand-made 2-row 2026-11-04.json as the ledger -> 3 passing retests written back as 0 rows;
2) results whose row was removed by a 不修/已修 ruling (#1440 ← #1409) had no destination;
3) retest-replay re-applies recorded results to the right ledger (dry-run default, --yes, idempotent). Only /tmp files."""
import sys, os, json, glob, tempfile, argparse
sys.path.insert(0, os.environ.get("PI_BIN") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"))
import opsfix_retest as R

FP = "mq1-a4b3e0d3594c99940e19"

def row(pr, fp, status="fix_merged_pending_retest"):
    return {"key": f"xindong/mivo-canvas-plugin#{pr}:{fp}", "repo": "xindong/mivo-canvas-plugin", "pr": pr, "fingerprint": fp,
            "level": "P2", "severity": "P2", "status": status, "fix_pr": None, "merged": None, "fix_prs": [], "retests": [],
            "retest": None, "retest_pr": None, "fix_round": 0, "needs_human": False}

def ledger(day, rows, **extra):
    return {"schema_version": "1.0", "kind": "mivo-qa-feishu-ledger", "date": day, "generated_at": f"{day}T15:40:01.698Z",
            "summary": {"total": len(rows)}, "needs_you": [], "rows": rows, **extra}

def put(path, doc):
    os.makedirs(os.path.dirname(path), exist_ok=True); json.dump(doc, open(path, "w"), ensure_ascii=False, indent=2)

def full_rows(n=20):
    return [row(1518, FP)] + [row(2000 + i, f"mq1-{i:020d}") for i in range(n - 1)]

def test_ledger_path_skips_handmade_partial_file():
    with tempfile.TemporaryDirectory() as qa:
        d = f"{qa}/ledger/daily"
        put(f"{d}/2026-11-03.json", ledger("2026-11-03", full_rows()))
        put(f"{d}/2026-11-04.json", ledger("2026-11-04", [row(1409, "mq1-9e3ba49a29b41364103b", "fixed"), row(1686, "mq1-281a3948f5a36b08dd51", "open")],
                                           manual_note="手工建档：仅含 #1409 一行；非完整日台账"))
        put(f"{d}/2026-11-04.json.bak-20261104-retest", ledger("2026-11-04", []))
        old = R.QA; R.QA = qa
        try: got = R.ledger_path()
        finally: R.QA = old
        assert got == f"{d}/2026-11-03.json", got

def test_ledger_path_fails_loudly_when_newest_is_truncated():
    with tempfile.TemporaryDirectory() as qa:
        d = f"{qa}/ledger/daily"
        put(f"{d}/2026-11-03.json", ledger("2026-11-03", full_rows()))
        put(f"{d}/2026-11-04.json", ledger("2026-11-04", full_rows(2)))       # looks generated, but 2 rows vs 20
        old = R.QA; R.QA = qa
        try:
            try: got = R.ledger_path(); raise AssertionError(f"picked {got} instead of failing")
            except R.LedgerError as e: assert "2026-11-04.json" in str(e)
            os.remove(f"{d}/2026-11-03.json"); os.remove(f"{d}/2026-11-04.json")
            put(f"{d}/2026-11-04.json", ledger("2026-11-04", [], manual_note="手工"))
            try: R.ledger_path(); raise AssertionError("no complete ledger must raise")
            except R.LedgerError: pass
        finally: R.QA = old

RULINGS = """| 日期 | 报告 PR | 问题 | 裁决 | 说明 |
| 2026-11-03 | #1409 | 凭证写入失败 | 已修 | 原「不修」，#1440 修复 |
| 2026-11-03 | #1518 | 连续失败判定 | 修 | #1563 |
"""

def test_ruled_out_result_gets_explicit_record():
    with tempfile.TemporaryDirectory() as tmp:
        led = ledger("2026-11-03", full_rows())
        res = [dict(fix_pr=1440, original_pr=1409, fingerprint="mq1-9e3ba49a29b41364103b", result="pass", reason="ok", evidence="e1440", at="2026-11-04T09:33:54+08:00"),
               dict(fix_pr=1999, original_pr=1998, fingerprint="mq1-99999999999999999999", result="fail", reason="x", evidence=None, at="2026-11-04T09:34:00+08:00")]
        um = []
        assert R.apply_ledger(led, res, um) == []
        R.classify_unmatched(um, RULINGS)
        assert [(u["fix_pr"], u["kind"]) for u in um] == [(1440, "ruled_out"), (1999, "no_row")]
        assert um[0]["ruling"] == "已修" and "裁决移除" in um[0]["why"] and um[0]["evidence"] == "e1440"
        p = f"{tmp}/retest-unmatched.jsonl"
        assert len(R.record_unmatched(um, "L", path=p)) == 2
        assert R.record_unmatched(um, "L", path=p) == []                       # idempotent
        lines = [json.loads(x) for x in open(p)]
        assert [(x["fix_pr"], x["original_pr"], x["result"], x["kind"]) for x in lines] == [(1440, 1409, "pass", "ruled_out"), (1999, 1998, "fail", "no_row")]

def test_replay_dry_run_then_yes_then_idempotent():
    with tempfile.TemporaryDirectory() as qa:
        d, rpt = f"{qa}/ledger/daily", f"{qa}/reports/merged"
        put(f"{d}/2026-11-03.json", ledger("2026-11-03", full_rows()))
        put(f"{d}/2026-11-04.json", ledger("2026-11-04", [row(1409, "mq1-9e3ba49a29b41364103b", "fixed")], manual_note="手工"))
        put(f"{rpt}/PR-1563.json", {"prs": [{"number": 1563, "retest": [
            {"fingerprint": FP, "original_pr": 1518, "result": "pass", "method": "code-review", "reason": "paginate", "evidence": "ev", "at": "2026-11-04T12:30:03+08:00", "by": R.BY}]}]})
        f = f"{d}/2026-11-03.json"; raw0 = open(f).read(); um_log = f"{qa}/ledger/retest-unmatched.jsonl"
        A = lambda yes: argparse.Namespace(pr=[1563], yes=yes, ledger=None, daily=d, reports=rpt, rulings=f"{qa}/none.md", unmatched_log=um_log)
        assert R.replay(A(False)) == 0 and open(f).read() == raw0 and not glob.glob(f"{f}.bak-*")      # dry-run writes nothing
        assert json.load(open(f"{d}/2026-11-04.json"))["rows"][0]["pr"] == 1409                         # hand-made file untouched
        assert R.replay(A(True)) == 0
        r = next(x for x in json.load(open(f))["rows"] if x["pr"] == 1518)
        assert r["status"] == "fixed" and r["retest"] == "pass" and r["retest_pr"] == 1563 and r["fix_pr"] == 1563
        assert [x["pr"] for x in r["retests"]] == [1563] and len(glob.glob(f"{f}.bak-*-replay")) == 1
        raw1 = open(f).read()
        assert R.replay(A(True)) == 0 and open(f).read() == raw1 and len(glob.glob(f"{f}.bak-*-replay")) == 1   # idempotent

if __name__ == "__main__":
    for k, v in list(globals().items()):
        if k.startswith("test_"): v()
    print("ok test_opsfix_ledger_writeback")
