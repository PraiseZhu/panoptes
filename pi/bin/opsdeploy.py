#!/usr/bin/env python3
"""panoptes deploy between the hourly opsenv runs (10-24). opsenv tries once an hour (:25) and a merge test holding the panoptes
locks makes it wait a whole hour (10-23 evening it was blocked 3 times; #18 went live ~13 h after its merge). Every 10 min this
runs the same guarded fast-forward, opsenv.deploy(): fleet-mode=pi, on main, clean tracked tree, origin/main strictly ahead, every
GitHub Actions run on the target green, both panoptes locks free (held during the update). Quiet while there is nothing to deploy
or it has to wait; an actual deploy is also written to out/ops/env-<day>.md. Failures that need a human stay with opsenv's hourly
report and watchdog's "panoptes behind > 3 h"."""
from lib import *
import opsenv


def main():
    if mode() != "pi": return 0
    L = []; err = opsenv.deploy(L)
    if L: log(L[-1])
    if L and "→" in L[-1]:
        f = f"{BASE}/out/ops/env-{today()}.md"
        os.makedirs(os.path.dirname(f), exist_ok=True)
        with open(f, "a") as fh: fh.write(f"\n## {now_cst().strftime('%H:%M')} 部署（opsdeploy，每 10 分钟）\n{L[-1]}\n")
    if err: log("deploy:", err)
    return 0


if __name__ == "__main__":
    sys.exit(main())
