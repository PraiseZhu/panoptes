#!/usr/bin/env python3
"""Install / update pstack (poteto's skill pack) for every Pi run on the box (Praise 10-27).

Source: github.com/cursor/plugins, directory pstack/ (sparse clone at ~/.pi/vendor/cursor-plugins, kept pristine).
Target: ~/.pi/agent/skills/pstack -- Pi's global skills dir, so manual `pi` runs discover it; the Pi line's coding steps
(bin/pi-noskills.sh, --no-skills) pass it with --skill.
Upstream marks every skill but setup-pstack `disable-model-invocation: true` (Cursor: load only on /command), which in Pi hides
it from the model's skill list. The Pi line wants the model to see and pick them, so the installed copy drops that one
frontmatter line; nothing else changes. ~13 KB of descriptions per request.
Usage: pstack_sync.py [--ref <git ref>] [--no-fetch]. Idempotent; the swap is a rename, a run in flight keeps the old copy."""
import argparse, json, os, re, shutil, subprocess, sys, tempfile

REPO = "https://github.com/cursor/plugins.git"
VENDOR = os.path.expanduser("~/.pi/vendor/cursor-plugins")
TARGET = os.path.expanduser("~/.pi/agent/skills/pstack")
FLAG = re.compile(r"^disable-model-invocation:\s*true\s*\n", re.M)

def enable_model_invocation(text):
    """drop `disable-model-invocation: true` from the leading frontmatter only"""
    m = re.match(r"(---\n)(.*?\n)(---\n)", text, re.S)
    if not m: return text
    return m.group(1) + FLAG.sub("", m.group(2)) + m.group(3) + text[m.end():]

def git(*a, cwd=VENDOR):
    return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True, timeout=300).stdout.strip()

def fetch(ref):
    if not os.path.isdir(f"{VENDOR}/.git"):
        os.makedirs(os.path.dirname(VENDOR), exist_ok=True)
        git("clone", "-q", "--depth", "1", "--filter=blob:none", "--sparse", REPO, VENDOR, cwd=None)
        git("sparse-checkout", "set", "pstack")
    git("fetch", "-q", "--depth", "1", "origin", ref)
    git("checkout", "-q", "--detach", "FETCH_HEAD")

def build(src_skills, target, meta):
    """copy src_skills to a sibling temp dir, apply enable_model_invocation to each SKILL.md, swap in by rename"""
    parent = os.path.dirname(target); os.makedirs(parent, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix=".pstack-new-", dir=parent)
    shutil.copytree(src_skills, f"{tmp}/s", symlinks=True)
    n = 0
    for root, _, files in os.walk(f"{tmp}/s"):
        if "SKILL.md" in files:
            p = f"{root}/SKILL.md"; t = open(p).read(); u = enable_model_invocation(t)
            if u != t: open(p, "w").write(u); n += 1
    json.dump(meta | {"enabled": n}, open(f"{tmp}/s/.pstack-source.json", "w"), indent=1)
    old = None
    if os.path.lexists(target):
        old = tempfile.mkdtemp(prefix=".pstack-old-", dir=parent); os.rename(target, f"{old}/x")
    os.rename(f"{tmp}/s", target); shutil.rmtree(tmp, ignore_errors=True)
    if old: shutil.rmtree(old, ignore_errors=True)
    return n

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--ref", default="main"); ap.add_argument("--no-fetch", action="store_true")
    a = ap.parse_args()
    if not a.no_fetch: fetch(a.ref)
    commit = git("rev-parse", "HEAD")
    version = json.load(open(f"{VENDOR}/pstack/plugin.json")).get("version", "?")
    n = build(f"{VENDOR}/pstack/skills", TARGET, {"repo": REPO, "path": "pstack/skills", "commit": commit, "version": version})
    print(f"pstack {version} @ {commit[:12]} -> {TARGET} ({n} skills made model-visible)")

if __name__ == "__main__": sys.exit(main())
