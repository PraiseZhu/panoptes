#!/usr/bin/env python3
"""panoptes deploy between the hourly opsenv runs (10-24). opsenv tries once an hour (:25) and a merge test holding the panoptes
locks makes it wait a whole hour (10-23 evening it was blocked 3 times; #18 went live ~13 h after its merge). Every 10 min this
runs the same guarded fast-forward, opsenv.deploy(): fleet-mode=pi, on main, clean tracked tree, origin/main strictly ahead, every
GitHub Actions run on the target green, both panoptes locks free (held during the update). Quiet while there is nothing to deploy
or it has to wait; an actual deploy is also written to out/ops/env-<day>.md. Failures that need a human stay with opsenv's hourly
report and watchdog's "panoptes behind > 3 h"."""
from lib import *
import opsenv


def sync_allowed(err):
    """仓库是线上的真相来源，只有部署无错、且 HEAD == origin/main（已部署到最新）时才同步 pi-call 和 cron。"""
    if err: return False, f"deploy error: {err}"
    _, head = opsenv.sh(f"git -C {opsenv.P} rev-parse HEAD"); _, tgt = opsenv.sh(f"git -C {opsenv.P} rev-parse origin/main")
    if not head or head != tgt: return False, f"HEAD {head[:7]} != origin/main {tgt[:7]}"
    return True, ""


def main():
    if mode() != "pi": return 0
    L = []; err = opsenv.deploy(L)
    if L: log(L[-1])
    if L and "→" in L[-1]:
        f = f"{BASE}/out/ops/env-{today()}.md"
        os.makedirs(os.path.dirname(f), exist_ok=True)
        with open(f, "a") as fh: fh.write(f"\n## {now_cst().strftime('%H:%M')} 部署（opsdeploy，每 10 分钟）\n{L[-1]}\n")
    if err: log("deploy:", err)
    ok, why = sync_allowed(err)
    if not ok:   # 10-30：checkout 落后时以旧仓库为准同步，把线上 pi-auto 从 discount 回滚成 v4.1-flash
        log("skip pi-call/cron sync:", why); return 0
    try:   # 10-28：线上 Pi 启动器以仓库为准，有漂移就同步（boxcheck 报漂移）
        import picall_sync; synced = picall_sync.sync()
        if synced: log("pi-call synced from repo:", ", ".join(synced))
    except Exception as e: log("pi-call sync failed:", str(e)[:200])
    try:   # 10-28：/etc/cron.d/pi-fallback 以仓库 pi/cron/pi-line.cron 为准；部署后或每轮有漂移就校验→备份→安装，diff 写日志
        import cron_sync; r = cron_sync.sync()
        if r: log("pi-fallback cron:", r[1]); r[2] and log("pi-fallback cron diff:\n" + r[2][:4000])
    except Exception as e: log("pi-fallback cron sync failed:", str(e)[:200])
    return 0


if __name__ == "__main__":
    sys.exit(main())
