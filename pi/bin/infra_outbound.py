#!/usr/bin/env python3
"""Outbound executors for the infrafix line (new action types; wired into outbound.handler() by 3 lines, see
outbound-snippet.md). Same contract as outbound.py handlers: handler(a) -> (who, state, validate, do);
state() -> "done" | "pending" | "stale:<why>" (stale = not now, re-checked next run); validate() read-only; do() acts.

  gh.infra.pr     {repo, sig, marker, patch, body_file, title, branch, auto_merge, ...}
                  temp clone of PraiseZhu/panoptes -> branch from origin/main -> `git am` the patch (author kept) -> push ->
                  `gh pr create` (NOT draft). Idempotent on the marker "Infra-Sig: <sig>[ rN]" in a PR body; a remote branch
                  left by an interrupted run is reused only when its head commit carries the same marker.
  gh.infra.merge  {repo, sig, pr, head, auto_merge}
                  merges (squash, --match-head-commit) ONLY when: (a) every GitHub Actions run / check on that head is
                  completed+success, (b) the action says auto_merge=true, (c) conf/gates.json infra_automerge is not false,
                  plus: PR open, head unchanged, body still has the marker, mergeable, changed files pass infrafix.sensitive()
                  again (defence in depth), at most INFRA_MERGE_DAY_CAP infra merges today. Otherwise DMs Praise the link and the
                  reasons (no merge). CI still running -> stale (retried next run).
  gh.infra.issue  {repo: PraiseZhu/pr-qa-agent-deploy, issue, comment, close}
                  comment (marker <!-- pi-line KEY -->) and close as completed when close=true. done = marker present and
                  (close -> CLOSED). Someone else already closed it -> done without a second comment.
                  (outbound's gh.issue.close cannot be reused: its validate() only allows the two handoff repos and it
                  is built around handoff issues.json records.)
Env for tests: INFRA_GATES (gates file)."""
import json, os, re, shutil, subprocess, tempfile, datetime as dt
import praise_notice as PN
import workroot as W

PANOPTES_REPO = "PraiseZhu/panoptes"
ISSUE_REPO = "PraiseZhu/pr-qa-agent-deploy"
INFRA_MERGE_DAY_CAP = 3
GIT_NAME, GIT_EMAIL = "Praise", "zhuzan@xd.com"      # committer for `git am` (= panoptes repo-local config / history)
CST = dt.timezone(dt.timedelta(hours=8))
OK = ("SUCCESS", "SKIPPED", "NEUTRAL")

def gates_file(): return os.environ.get("INFRA_GATES") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "conf", "gates.json")
def automerge_allowed():
    """conf/gates.json infra_automerge: key missing -> allowed; false -> off; file missing / unreadable -> off (fail closed)."""
    try:
        with open(gates_file()) as f: return json.load(f).get("infra_automerge") is not False
    except Exception: return False

def _git(d, *a, env=None):
    r = subprocess.run(["git", "-C", d, *a], capture_output=True, text=True, timeout=600, env=env)
    if r.returncode: raise RuntimeError(f"git {a[0]} rc={r.returncode}: {(r.stderr or r.stdout).strip()[-300:]}")
    return r.stdout

def checks_of(gh, repo, head):
    """(state, detail): green / red / running / none -- from statusCheckRollup of the PR head and `gh run list --commit`."""
    runs = gh(["run", "list", "-R", repo, "--commit", head, "--json", "name,status,conclusion", "--limit", "50"])
    if not runs: return "none", "该提交没有任何 GitHub Actions 运行"
    if any(r.get("status") != "completed" for r in runs): return "running", ", ".join(f"{r.get('name')}={r.get('status')}" for r in runs)
    bad = [f"{r.get('name')}={r.get('conclusion')}" for r in runs if (r.get("conclusion") or "").upper() not in ("SUCCESS",)]
    return ("red", ", ".join(bad)) if bad else ("green", f"{len(runs)} 个运行全部 success")

def handler(a, gh, dm, sensitive=None, now=None):
    """gh(args, js=True) as in lib; dm(text, key) sends a Slack DM to Praise; sensitive = (infrafix.files_from_diff,
    infrafix.sensitive) or None (then the merge refuses: no re-check possible)."""
    t = a.get("type", "")
    if t.startswith("gh.infra.pr"): return h_pr(a, gh)
    if t.startswith("gh.infra.merge"): return h_merge(a, gh, dm, sensitive, now)
    if t.startswith("gh.infra.issue"): return h_issue(a, gh)
    return None

# ---------------------------------------------------------------- gh.infra.pr
def find_pr(gh, repo, marker):
    prs = gh(["pr", "list", "-R", repo, "--state", "all", "--limit", "200", "--json", "number,state,body,url"])
    exact = re.compile(re.escape(marker) + r"(?![ \t]*r\d)")       # "Infra-Sig: X" must not match the round-2 "Infra-Sig: X r2"
    return next((p for p in prs if exact.search(p.get("body") or "")), None)

def h_pr(a, gh):
    repo, marker, patch = a.get("repo") or PANOPTES_REPO, a.get("marker") or "", a.get("patch")
    def state(): return "done" if marker and find_pr(gh, repo, marker) else "pending"
    def validate():
        if repo != PANOPTES_REPO: raise RuntimeError(f"refused: infra PRs only go to {PANOPTES_REPO}")
        if not marker.startswith("Infra-Sig: inf1-"): raise RuntimeError("no Infra-Sig marker")
        if not (patch and os.path.exists(patch)): raise RuntimeError("patch missing")
        if not re.fullmatch(r"fix/infra-[0-9a-f]{8}(?:-r\d)?", a.get("branch") or ""): raise RuntimeError(f"bad branch {a.get('branch')}")
        v = gh(["repo", "view", repo, "--json", "viewerPermission,defaultBranchRef"])
        return f"patch {os.path.getsize(patch)}B, perm={v['viewerPermission']}, base={v['defaultBranchRef']['name']}, auto_merge={a.get('auto_merge')}"
    def do():
        validate()
        d = W.mkdtemp("pi-infrapr-")   # PI_WORK/tmp: janitor removes what a killed run leaves
        try:
            r = subprocess.run(["gh", "repo", "clone", repo, d, "--", "--depth", "50", "-q"], capture_output=True, text=True, timeout=600)
            if r.returncode: raise RuntimeError(f"clone rc={r.returncode}: {r.stderr[-200:]}")
            br = a["branch"]
            env = dict(os.environ, GIT_COMMITTER_NAME=GIT_NAME, GIT_COMMITTER_EMAIL=GIT_EMAIL)
            remote = _git(d, "ls-remote", "--heads", "origin", f"refs/heads/{br}").strip()
            if remote:   # an interrupted earlier run pushed it already: reuse only if it is ours
                _git(d, "fetch", "-q", "--depth", "5", "origin", f"refs/heads/{br}:refs/remotes/origin/{br}")
                msg = _git(d, "log", "-1", "--format=%B", f"origin/{br}")
                if marker not in msg: raise RuntimeError(f"remote branch {br} exists and is not ours (no marker); refusing to overwrite")
                note = "remote branch reused"
            else:
                _git(d, "checkout", "-q", "-b", br, "origin/main")
                try: _git(d, "am", "--3way", "--keep-cr", patch, env=env)
                except RuntimeError:
                    subprocess.run(["git", "-C", d, "am", "--abort"], capture_output=True)
                    raise RuntimeError("patch no longer applies on origin/main (git am --3way failed); infrafix must redo this ticket")
                _git(d, "push", "-q", "-u", "origin", f"HEAD:refs/heads/{br}")    # new branch, plain push, never --force
                note = "pushed"
            with open(a["body_file"]) as f: body = f.read()
            if marker not in body: body += f"\n\n{marker}"
            body += f"\n<!-- pi-line {a['key']} -->"
            out = gh(["pr", "create", "-R", repo, "--base", "main", "--head", br, "--title", a.get("title") or f"fix: infra {marker[-20:]}",
                      "--body", body], js=False).strip()
            return f"{out} | {note}"
        finally: shutil.rmtree(d, ignore_errors=True)
    return f"gh infra PR {repo} ({marker[-25:]})", state, validate, do

# ---------------------------------------------------------------- gh.infra.merge
def merges_today(gh, repo, now):
    day = (now or dt.datetime.now(CST)).astimezone(CST).date()
    a = dt.datetime(day.year, day.month, day.day, tzinfo=CST).astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    prs = gh(["pr", "list", "-R", repo, "--state", "merged", "--search", f"merged:>={a}", "--limit", "50", "--json", "number,body"])
    return sum(1 for p in prs if "Infra-Sig: inf1-" in (p.get("body") or ""))

def merge_blockers(a, p, ci, gh, sensitive, now):
    """-> reasons not to merge (empty = merge). p = gh pr view json."""
    why = []
    if ci[0] != "green": why.append(f"CI 未全绿：{ci[1]}")
    if a.get("auto_merge") is not True: why.append("动作里 auto_merge 不是 true（没有先红后绿的复现测试 / 碰了敏感路径 / 改动过大）")
    if not automerge_allowed(): why.append("conf/gates.json infra_automerge=false（或闸文件读不出）")
    if f"Infra-Sig: {a.get('sig')}" not in (p.get("body") or ""): why.append("PR 正文没有 Infra-Sig 标记（不是 infrafix 开的？）")
    if (p.get("mergeable") or "").upper() == "CONFLICTING": why.append("与 main 冲突")
    if not sensitive: why.append("没有敏感路径复核函数（infrafix 导入失败？）")
    else:   # sensitive = (files_from_diff, sensitive) from infrafix: re-judge the PR's real diff, not the plan
        try:
            diff = gh(["pr", "diff", str(a["pr"]), "-R", a.get("repo") or PANOPTES_REPO], js=False)
            why += sensitive[1](sensitive[0](diff), diff)
        except Exception as e: why.append(f"读不到 PR diff，无法复核敏感路径：{str(e)[:80]}")
    if not why and merges_today(gh, a.get("repo") or PANOPTES_REPO, now) >= INFRA_MERGE_DAY_CAP:
        why.append(f"今天已自动合并 {INFRA_MERGE_DAY_CAP} 个 infra PR（日上限）")
    return why

def h_merge(a, gh, dm, sensitive, now):
    repo, n, head = a.get("repo") or PANOPTES_REPO, a["pr"], a.get("head") or ""
    def view(): return gh(["pr", "view", str(n), "-R", repo, "--json", "state,headRefOid,body,mergeable,files,url"])
    def state():
        p = view()
        if p["state"] == "MERGED": return "done"
        if p["state"] != "OPEN": return f"stale:{p['state'].lower()}"
        if head and p["headRefOid"] != head: return "stale:head moved"
        ci = checks_of(gh, repo, p["headRefOid"])
        if ci[0] in ("running", "none"): return f"stale:ci {ci[0]}"
        return "pending"
    def validate():
        if repo != PANOPTES_REPO: raise RuntimeError(f"refused: infra merges only in {PANOPTES_REPO}")
        p = view(); ci = checks_of(gh, repo, p["headRefOid"])
        why = merge_blockers(a, p, ci, gh, sensitive, now)
        return f"#{n} would merge" if not why else f"#{n} would DM Praise: " + "；".join(why)
    def do():
        if repo != PANOPTES_REPO: raise RuntimeError(f"refused: infra merges only in {PANOPTES_REPO}")
        p = view()
        if p["state"] != "OPEN" or (head and p["headRefOid"] != head): raise RuntimeError("PR state/head changed since planning")
        ci = checks_of(gh, repo, p["headRefOid"])
        why = merge_blockers(a, p, ci, gh, sensitive, now)
        url = p.get("url") or f"https://github.com/{repo}/pull/{n}"
        if why:
            sent = dm(PN.text(PN.item("action", f"测试系统的修复 PR #{n} 要你看一眼再合", f"「测试系统自修」给测试系统自己的一个故障开了修复 PR，自动测试已经跑完，但有几处不满足自动合并的条件：{'；'.join(why)[:200]}。",
                                "不合并的话这个故障一直在。", todo="打开 PR 看一下：没问题就合并（合并后每小时 25 分自动上线），有问题就在 PR 下说", link=url)), f"infra-review:{a['key']}")
            # False = muted (praise-dm-mute): nobody was told. Say so in the ledger; watchdog + Jasper's brief carry awaiting_praise from here
            return ("not merged; DM muted, waiting in Jasper brief: " if sent is False else "not merged; DM sent: ") + "；".join(why)[:150]
        return gh(["pr", "merge", str(n), "-R", repo, "--squash", "--match-head-commit", p["headRefOid"]], js=False).strip() or f"merged #{n}"
    return f"gh infra merge #{n}@{head[:7]}", state, validate, do

# ---------------------------------------------------------------- gh.infra.issue
def h_issue(a, gh):
    repo, n, close = a.get("repo") or "", int(a.get("issue") or 0), bool(a.get("close"))
    mark = f"<!-- pi-line {a['key']} -->"
    def cur(): return gh(["issue", "view", str(n), "-R", repo, "--json", "state,comments"])
    def state():
        v = cur(); commented = any(mark in (c.get("body") or "") for c in v.get("comments") or [])
        if close and v["state"] == "CLOSED": return "done"           # closed (by us or anyone): never a second comment
        return "done" if commented and (not close or v["state"] == "CLOSED") else "pending"
    def validate():
        if repo != ISSUE_REPO: raise RuntimeError(f"refused: infra issue actions only in {ISSUE_REPO}")
        if not (n and a.get("comment")): raise RuntimeError("need issue and comment")
        v = cur(); return f"{repo}#{n} {v['state']} -> {'close' if close else 'comment'}"
    def do():
        validate(); v = cur()
        commented = any(mark in (c.get("body") or "") for c in v.get("comments") or [])
        body = a["comment"] + "\n\n" + mark
        if close and v["state"] == "OPEN":
            if commented: return gh(["issue", "close", str(n), "-R", repo, "--reason", "completed"], js=False).strip() or "closed"
            return gh(["issue", "close", str(n), "-R", repo, "--reason", "completed", "--comment", body], js=False).strip() or "commented+closed"
        if not commented: return gh(["issue", "comment", str(n), "-R", repo, "--body", body], js=False).strip()
        return "already commented"
    return f"gh issue {'close' if close else 'comment'} {repo}#{n}", state, validate, do
