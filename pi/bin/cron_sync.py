#!/usr/bin/env python3
"""cron_sync — /etc/cron.d/pi-fallback 以仓库 pi/cron/pi-line.cron 为准（10-28：opsdeploy 只同步 pi-call，定时表改了要手工装）。
opsdeploy 每 10 分钟调 sync()：部署后、或任何一轮发现漂移时，先校验仓库版语法（不过就不装），把线上旧文件备份到
<state>/cron-bak/pi-fallback.<时间>（不放 /etc/cron.d，免得被 cron 读），用和手工一样的
`sudo -n install -o root -g root -m 644` 装上，返回 diff 写日志。
用法：cron_sync.py [check|sync]"""
import os, sys, re, time, difflib, subprocess, shutil
REPO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cron", "pi-line.cron")
LIVE = os.environ.get("PI_CRON_LIVE", "/etc/cron.d/pi-fallback")
BAKDIR = os.environ.get("PI_CRON_BAK", "/workspace/mivo-qa/pi/cron-bak")
RANGES = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)]
NAMES = [None, None, None, "jan feb mar apr may jun jul aug sep oct nov dec".split(), "sun mon tue wed thu fri sat".split()]
SPECIAL = {"@reboot", "@yearly", "@annually", "@monthly", "@weekly", "@daily", "@midnight", "@hourly"}
ENV = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\s*=")


def _field(s, i):
    lo, hi = RANGES[i]
    for part in s.split(","):
        rng, _, step = part.partition("/")
        if step and not (step.isdigit() and int(step) > 0): return False
        if rng == "*": continue
        ends = rng.split("-")
        if len(ends) > 2: return False
        vals = []
        for e in ends:
            e2 = e.lower()
            if NAMES[i] and e2 in NAMES[i]: vals.append(NAMES[i].index(e2) + (1 if i == 3 else 0)); continue
            if not e.isdigit(): return False
            vals.append(int(e))
        if any(v < lo or v > hi for v in vals) or (len(vals) == 2 and vals[0] > vals[1]): return False
    return True


def validate(text):
    """返回错误列表（空=通过）。/etc/cron.d 格式：5 个时间字段（或 @关键字）+ 用户 + 命令；注释、空行、VAR=值。"""
    errs = []
    if not text.endswith("\n"): errs.append("文件末尾没有换行（cron 会丢最后一行）")
    jobs = 0
    for n, ln in enumerate(text.splitlines(), 1):
        s = ln.strip()
        if not s or s.startswith("#") or ENV.match(s): continue
        p = s.split()
        if p[0].startswith("@"):
            if p[0] not in SPECIAL or len(p) < 3: errs.append(f"第 {n} 行: {s[:80]}"); continue
        elif len(p) < 7 or not all(_field(p[i], i) for i in range(5)):
            errs.append(f"第 {n} 行: {s[:80]}"); continue
        jobs += 1
    if not jobs: errs.append("没有任何任务行")
    return errs


def _read(p):
    try: return open(p).read()
    except OSError: return None


def drift(repo=REPO, live=LIVE): return _read(repo) != _read(live)


def _install(src, dst):
    cmd = ["install", "-o", "root", "-g", "root", "-m", "644", src, dst]
    if os.geteuid() != 0 and not os.environ.get("PI_CRON_NOSUDO"): cmd = ["sudo", "-n"] + cmd
    elif os.environ.get("PI_CRON_NOSUDO"): cmd = ["install", "-m", "644", src, dst]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode: raise RuntimeError(f"install 失败: {(r.stderr or r.stdout).strip()[:200]}")


def sync(repo=REPO, live=LIVE, bakdir=BAKDIR):
    """无漂移返回 None；有漂移返回 (ok, 消息, diff)。校验不过绝不安装。"""
    new, old = _read(repo), _read(live)
    if new is None: return (False, f"仓库文件不存在 {repo}", "")
    if new == old: return None
    diff = "".join(difflib.unified_diff((old or "").splitlines(True), new.splitlines(True), live, repo))
    errs = validate(new)
    if errs: return (False, "语法校验没过，未安装：" + "; ".join(errs[:5]), diff)
    bak = ""
    if old is not None:
        os.makedirs(bakdir, exist_ok=True); bak = os.path.join(bakdir, f"{os.path.basename(live)}.{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(live, bak)
    _install(repo, live)
    if _read(live) != new: return (False, "安装后内容仍与仓库不一致", diff)
    return (True, f"已安装仓库版到 {live}" + (f"，旧版备份 {bak}" if bak else ""), diff)


if __name__ == "__main__":
    a = sys.argv[1] if len(sys.argv) > 1 else "check"
    if a == "sync":
        r = sync(); print("no drift" if r is None else f"{r[1]}\n{r[2]}"); sys.exit(0 if r is None or r[0] else 1)
    e = validate(_read(REPO) or ""); d = drift()
    print("syntax:", "ok" if not e else e, "| drift:", d); sys.exit(1 if e or d else 0)
