#!/usr/bin/env python3
"""11-11 (#2022, Praise: "只能用 app 接口"): the ONE way to open a ready product PR.

Runs exactly `gh pr create --repo xindong/mivo-canvas-plugin --base main --head <branch> --title <t> --body-file <f>` through the
box gh wrapper (pi/bin/gh_wrapper.py, the sibling file), as the praise-qa App installation token: the wrapper excuses ("pr", "create")
for this script only (gh_wrapper.PRODUCT_WRITE_EXCEPTIONS) and mints the xindong installation token. This script is not on
CALLER_ALLOWLIST, so it never gets the PAT. Refused, nothing sent: --draft, another repo, a base other than main, any other argument,
and an App route that is off (it would fall back to the PAT).

Usage: open_product_pr.py --head <branch> --title <title> --body-file <file> [--repo xindong/mivo-canvas-plugin] [--base main]
Audit: one JSON line per call in $PI_STATE/out/gh-wrapper/open-product-pr.jsonl {at, branch, title, result, rc, url, why}."""
import datetime as dt, json, os, re, subprocess, sys
HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
import gh_wrapper  # noqa: E402
import lib  # noqa: E402

WRAPPER = os.path.join(HERE, "gh_wrapper.py")
FIXED = {"--repo": lib.REPO, "--base": "main"}
REQUIRED = ("--head", "--title", "--body-file")
HEAD_RE = re.compile(r"^[A-Za-z0-9._][A-Za-z0-9._/-]*$")   # a branch of the product repo itself: no fork `owner:branch`, no leading "-"
TOKEN_VARS = ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN")


def audit_log(): return f"{lib.PI_STATE}/out/gh-wrapper/open-product-pr.jsonl"


def audit(opts, result, rc, url=None, why=None):
    rec = {"at": dt.datetime.now(lib.CST).isoformat(timespec="seconds"), "branch": opts.get("--head"), "title": opts.get("--title"),
           "result": result, "rc": rc, "url": url, "why": lib._TOKEN_RE.sub("***", why) if why else None}
    try:
        os.makedirs(os.path.dirname(audit_log()), exist_ok=True)
        with open(audit_log(), "a") as f: f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as e: print(f"open_product_pr: audit write failed ({e.__class__.__name__})", file=sys.stderr)


def parse(argv):
    """-> (opts, None) for the one allowed shape, else (partial opts, why)"""
    opts, a, i = {}, list(argv), 0
    while i < len(a):
        k, eq, v = a[i].partition("="); i += 1
        if k not in FIXED and k not in REQUIRED: return opts, f"argument {k} refused (only --head, --title, --body-file, --repo {lib.REPO}, --base main)"
        if not eq:
            if i >= len(a): return opts, f"{k} needs a value"
            v = a[i]; i += 1
        if k in opts: return opts, f"{k} given twice"
        opts[k] = v
    for k, want in FIXED.items():
        if opts.setdefault(k, want) != want: return opts, f"{k} {opts[k]} refused (only {want})"
    missing = [k for k in REQUIRED if not opts.get(k)]
    if missing: return opts, "missing " + ", ".join(missing)
    if not HEAD_RE.match(opts["--head"]): return opts, f"--head {opts['--head']} refused (a branch of {lib.REPO}, no owner: prefix)"
    if not os.path.isfile(opts["--body-file"]): return opts, f"--body-file {opts['--body-file']} is not a file"
    return opts, None


def main(argv, run=subprocess.run):
    opts, why = parse(argv)
    if not why and not (gh_wrapper.wrapper_on() and lib.gh_app_product_writes_on()):
        why = "praise-qa App route is off (gh-app wrapper / product-writes switch); refusing rather than using the PAT"
    if why:
        print(f"open_product_pr: refused: {why}; nothing was sent", file=sys.stderr)
        audit(opts, "refused", gh_wrapper.EXIT_RED_LINE, why=why)
        return gh_wrapper.EXIT_RED_LINE
    env = {k: v for k, v in os.environ.items() if k not in TOKEN_VARS}   # the wrapper mints the installation token itself
    cmd = [sys.executable, WRAPPER, "pr", "create", "--repo", lib.REPO, "--base", "main",
           f"--head={opts['--head']}", f"--title={opts['--title']}", f"--body-file={opts['--body-file']}"]
    r = run(cmd, env=env, capture_output=True, text=True, timeout=180)
    out, err = r.stdout or "", r.stderr or ""
    sys.stdout.write(out); sys.stderr.write(err)
    url = next((l.strip() for l in out.splitlines() if re.match(rf"https://github\.com/{re.escape(lib.REPO)}/pull/\d+$", l.strip())), None)
    audit(opts, "opened" if r.returncode == 0 and url else "failed", r.returncode, url=url, why=None if r.returncode == 0 else err.strip()[-300:])
    return r.returncode


if __name__ == "__main__": sys.exit(main(sys.argv[1:]))
