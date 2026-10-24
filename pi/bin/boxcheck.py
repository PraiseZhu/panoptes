#!/usr/bin/env python3
"""备份管家 / 电脑运维 / Cindy 管家: verify their cron-or-script duties keep running without Grok Bot (check-only)."""
from lib import *
def sh(c):
    r = subprocess.run(c, shell=True, capture_output=True, text=True, timeout=60); return (r.stdout + r.stderr).strip()
def age_min(p):
    try: return round((time.time() - os.path.getmtime(p)) / 60)
    except Exception: return None
import time
def main():
    d = today(); L = [f"## {now_cst().strftime('%H:%M')}"]
    a = age_min("/tmp/grokbot-box-sync.log"); L.append(f"- 备份: sync 日志 {a} 分钟前更新（cron :17 每小时；>70 算异常）{'⚠' if a is None or a > 70 else '✓'}")
    loc = sh("git -C /workspace/grokbot-box rev-parse HEAD"); rem = sh("git -C /workspace/grokbot-box ls-remote origin HEAD | cut -f1")
    L.append(f"- 备份: 本地 {loc[:8]} vs 远端 {rem[:8]} {'✓' if loc == rem else '⚠ 未推送/不一致'}")
    lc = sh("git -C /workspace/grokbot-box log -1 --format=%cI"); L.append(f"- 备份: 最近提交 {cst(lc) if lc else '?'}")
    m = age_min("/workspace/box-ops/mem.log"); L.append(f"- 电脑运维: mem.log {m} 分钟前更新（每分钟 cron）{'✓' if m is not None and m <= 2 else '⚠'}")
    ma = sh("awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo"); sw = sh("awk '/SwapTotal/{print int($2/1024)}' /proc/meminfo")
    df = sh("df -h / | awk 'NR==2{print $4}'"); eo = "运行中 ✓" if sh("pgrep -x earlyoom") else "未运行 ⚠"
    L.append(f"- 电脑运维: earlyoom {eo}；MemAvailable {ma}MB；swap {sw}MB；磁盘 / 剩余 {df}")
    h = age_min("/workspace/box-ops/health.log"); L.append(f"- 电脑运维: health.log（Pi 线 boxhealth.py :17；Grok box-health 已暂停）{h} 分钟前更新")
    c = sh("pgrep -af '^(/usr/bin/cindy|/usr/lib/cindy/Cindy)( |$)' | grep -v -- --type= | head -1 | cut -c1-80")   # was '[c]indy': matched nudge-loop/earlyoom -> always ✓
    L.append(f"- Cindy: 进程 {'在 ✓' if c else '不在 ⚠'}；保活由 Pi 线 cindy.py（*/5，takeover/cindy）调 ensure-cindy.sh；boxcheck 只查不重启")
    p = f"{BASE}/out/box/{d}.md"; old = open(p).read() if os.path.exists(p) else f"# 机器类 bot 核查 {d}（Pi line，只查）\n"
    write_atomic(p, old + "\n".join(L) + "\n")
if __name__ == "__main__": main()
