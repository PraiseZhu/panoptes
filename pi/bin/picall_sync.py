#!/usr/bin/env python3
"""picall_sync — ~/tools/pi-call（线上 pi-auto / pi-task 等启动器）以仓库 pi/restore/pi-call 为准（10-28：线上 work 档 thinking=medium、
仓库是 high，启动器也不一样，没人发现）。drift() 列出不一致的文件；sync() 把仓库版本装上去（先备份旧的到 <live>.bak-<时间>/）。
boxcheck 每小时报漂移；opsdeploy 每 10 分钟在部署后自动同步（有漂移才动，写日志）。
用法：picall_sync.py [check|sync]"""
import os, sys, shutil, hashlib, time
REPO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "restore", "pi-call")
LIVE = os.environ.get("PI_CALL_DIR") or os.path.expanduser("~/tools/pi-call")


def _h(p):
    try: return hashlib.sha256(open(p, "rb").read()).hexdigest()
    except OSError: return None


def drift(repo=REPO, live=LIVE):
    return sorted(f for f in os.listdir(repo) if os.path.isfile(os.path.join(repo, f)) and _h(os.path.join(repo, f)) != _h(os.path.join(live, f)))


def sync(repo=REPO, live=LIVE):
    d = drift(repo, live)
    if not d: return []
    os.makedirs(live, exist_ok=True); bak = f"{live}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
    for f in d:
        if os.path.exists(os.path.join(live, f)): os.makedirs(bak, exist_ok=True); shutil.copy2(os.path.join(live, f), os.path.join(bak, f))
        t = os.path.join(live, f + ".tmp"); shutil.copy2(os.path.join(repo, f), t); os.chmod(t, 0o755); os.replace(t, os.path.join(live, f))
    return d


if __name__ == "__main__":
    a = sys.argv[1] if len(sys.argv) > 1 else "check"
    d = sync() if a == "sync" else drift()
    print(("synced: " if a == "sync" else "drift: ") + (", ".join(d) or "none")); sys.exit(1 if d and a == "check" else 0)
