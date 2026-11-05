"""retest_runner.py — fail->pass evidence for a fix PR (11-05, retest-1104 ANALYSIS.md §2: no path existed that reran the
original failing case on the original sha and on the fix sha). Not wired into cron; writes only the --out paths given.

For each sha (original PR's tested/merge sha, fix PR's head/merge sha) it runs
  1. the original failing test case(s) — vitest files / -t names, or the exact command from failed_checks[].evidence.command
  2. `vitest related --run <changed files>` — the fix PR's changed source files (targeted regression)
and pairs them: fail on original + pass on fix = "fail->pass" (evidence); pass on both = "not-reproduced"; fail on fix = "still-fails".

  python3 pi/bin/retest_runner.py --orig-sha A --fix-sha B --case src/x.test.ts[::name] ... [--command '...'] \
      --changed src/a.ts ... --out /path/evidence.json      (summary -> /path/evidence.md)

Pure parts (cases_from_report, case_cmds, classify, verdict, summary) are tested in test/test_retest_runner.py; the
runner takes injectable `worktree`/`sh` so tests never touch git or node."""
import argparse, json, os, re, shlex, subprocess, sys, datetime as dt

CST = dt.timezone(dt.timedelta(hours=8))
VITEST = ["node", "scripts/run-vitest.mjs"]
TEST_FILE = re.compile(r"\.(test|spec)\.[cm]?[jt]sx?$")

def cases_from_report(pr_report, fingerprints=None):
    """Original failing cases from a reports/merged PR entry: failed_checks[] (optionally only the given fingerprints).
    Each case: {id, file, name, command}. command is the check's own command (evidence.command) when there is one."""
    out = []
    for c in (pr_report or {}).get("failed_checks") or []:
        if fingerprints and c.get("fingerprint") not in fingerprints: continue
        ev = c.get("evidence") or {}; t = c.get("test") or ""
        if " > " in t: file, _, name = t.partition(" > ")
        elif TEST_FILE.search(t): file, name = t, ""
        else: file, name = "", t
        out.append({"id": c.get("check_id"), "file": file or None, "name": name or None, "command": ev.get("command"), "fingerprint": c.get("fingerprint")})
    return out

def parse_case(s):
    """CLI --case 'file[::test name]'."""
    f, _, n = s.partition("::"); return {"id": f, "file": f or None, "name": n or None, "command": None}

def case_cmds(cases):
    """[(label, argv)] — a case with a command runs it verbatim; vitest file/name cases run one vitest per case."""
    out = []
    for c in cases:
        if c.get("command"): out.append((f"case:{c.get('id')}", ["bash", "-lc", c["command"]])); continue
        if not c.get("file") and not c.get("name"): continue
        argv = VITEST + ["run", *([c["file"]] if c.get("file") else []), *(["-t", c["name"]] if c.get("name") else [])]
        out.append((f"case:{c.get('file') or c.get('id')}" + (f"::{c['name']}" if c.get("name") else ""), argv))
    return out

def related_cmd(changed):
    src = [f for f in changed or [] if re.search(r"\.[cm]?[jt]sx?$", f)]
    return ("related", VITEST + ["related", "--run", *src, "--passWithNoTests"]) if src else None

def classify(rc, out=""):
    """A run's outcome: pass / fail (tests failed) / error (could not run: missing file, crash, timeout -> rc None)."""
    if rc is None: return "error"
    if rc == 0: return "pass"
    if re.search(r"(^| )FAIL |Tests?\s+\d+\s+failed|✗|AssertionError", out or "", re.M): return "fail"
    if re.search(r"No test files found|Cannot find module|ENOENT", out or ""): return "error"
    return "fail"

def verdict(orig, fix):
    if fix == "pass" and orig == "fail": return "fail->pass"
    if fix == "pass" and orig == "pass": return "not-reproduced"
    if fix == "fail": return "still-fails"
    return "inconclusive"

def default_sh(argv, cwd, timeout=1800):
    try:
        p = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                           env={k: v for k, v in os.environ.items() if not k.endswith(("_TOKEN", "_API_KEY"))} | {"CI": "1"})
        return p.returncode, p.stdout + "\n" + p.stderr
    except subprocess.TimeoutExpired as e: return None, f"timeout after {timeout}s: {e}"

def run(orig_sha, fix_sha, cases, changed, worktree, sh=default_sh, drop=None):
    """worktree(sha) -> dir; sh(argv, cwd) -> (rc, output); drop(dir) cleans up. Returns the evidence dict."""
    cmds = case_cmds(cases); rel = related_cmd(changed)
    runs = {}
    for side, sha in (("original", orig_sha), ("fix", fix_sha)):
        wt = worktree(sha); runs[side] = {}
        try:
            for label, argv in cmds + ([rel] if rel else []):
                rc, out = sh(argv, wt)
                runs[side][label] = {"argv": argv, "rc": rc, "result": classify(rc, out), "tail": (out or "")[-4000:]}
        finally:
            if drop: drop(wt)
    rows = []
    for label, _ in cmds + ([rel] if rel else []):
        o, f = runs["original"][label]["result"], runs["fix"][label]["result"]
        rows.append({"check": label, "original": o, "fix": f, "verdict": verdict(o, f) if label != "related" else ("pass" if f == "pass" else "fail")})
    case_rows = [r for r in rows if r["check"] != "related"]
    rel_row = next((r for r in rows if r["check"] == "related"), None)
    if not case_rows: overall = "blocked"
    elif all(r["verdict"] == "fail->pass" for r in case_rows) and (not rel_row or rel_row["fix"] == "pass"): overall = "pass"
    elif any(r["verdict"] == "still-fails" for r in case_rows) or (rel_row and rel_row["fix"] == "fail"): overall = "fail"
    else: overall = "inconclusive"
    return {"kind": "fix-retest-evidence", "at": dt.datetime.now(CST).isoformat(timespec="seconds"), "original_sha": orig_sha, "fix_sha": fix_sha,
            "cases": cases, "changed": changed, "rows": rows, "result": overall, "runs": runs}

def summary(ev):
    L = [f"# 原用例复测证据（{ev['original_sha'][:10]} → {ev['fix_sha'][:10]}）：{ev['result']}", "",
         "| 检查 | 原 sha | 修复 sha | 结论 |", "|---|---|---|---|"]
    L += [f"| {r['check']} | {r['original']} | {r['fix']} | {r['verdict']} |" for r in ev["rows"]]
    if ev["result"] == "blocked": L += ["", "没有可执行的原用例（failed_checks 里没有测试文件 / 名称 / 命令）。"]
    return "\n".join(L) + "\n"

def git_worktree(repo, root):
    def mk(sha):
        d = os.path.join(root, f"wt-{sha[:12]}")
        subprocess.run(["git", "-C", repo, "worktree", "add", "--detach", d, sha], check=True, capture_output=True); return d
    def rm(d): subprocess.run(["git", "-C", repo, "worktree", "remove", "--force", d], capture_output=True)
    return mk, rm

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo", required=True, help="a mivo clone used as object store (worktrees under --work)")
    ap.add_argument("--work", required=True); ap.add_argument("--orig-sha", required=True); ap.add_argument("--fix-sha", required=True)
    ap.add_argument("--case", action="append", default=[]); ap.add_argument("--command", action="append", default=[])
    ap.add_argument("--report", help="original reports/merged/PR-N.json: take cases from failed_checks"); ap.add_argument("--fingerprint", action="append")
    ap.add_argument("--changed", nargs="*", default=[]); ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    cases = [parse_case(c) for c in a.case] + [{"id": f"cmd{i}", "file": None, "name": None, "command": c} for i, c in enumerate(a.command)]
    if a.report: cases += cases_from_report((json.load(open(a.report)).get("prs") or [{}])[0], set(a.fingerprint or []) or None)
    mk, rm = git_worktree(a.repo, a.work)
    ev = run(a.orig_sha, a.fix_sha, cases, a.changed, mk, drop=rm)
    with open(a.out, "w") as f: json.dump(ev, f, ensure_ascii=False, indent=1)
    md = re.sub(r"\.json$", "", a.out) + ".md"
    with open(md, "w") as f: f.write(summary(ev))
    print(summary(ev)); return 0 if ev["result"] == "pass" else 1

if __name__ == "__main__":
    sys.exit(main())
