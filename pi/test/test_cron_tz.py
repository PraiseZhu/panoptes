"""python3 test/test_cron_tz.py — catch-up 北京偶数点门控（Debian cron 不支持按文件设时区，盒子时区又会被平台切换）：
cron 每小时 :07 触发（'7 * * * *'，crontab 里没有字面 %），run-nightly.sh --catch-up 在 PANOPTES_CATCHUP_GATE=even 时用
TZ=Asia/Shanghai date +%H 判小时，奇数点写一行日志 exit 0。在 TZ=America/Los_Angeles 与 TZ=Asia/Taipei 下各测一遍（伪造北京小时）。"""
import os, sys, subprocess, tempfile, importlib.util
R = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
s = importlib.util.spec_from_file_location("cron_sync", f"{R}/pi/bin/cron_sync.py"); C = importlib.util.module_from_spec(s); s.loader.exec_module(C)
def jobs(t): return [l for l in t.splitlines() if l.strip() and not l.lstrip().startswith("#") and not C.ENV.match(l.strip())]

# 1) cron 文件：没有 CRON_TZ；catch-up 每小时 :07、带门控变量、无 %；语法校验通过；pi-line 小时字段全是 *
for f in ("pi/cron/pi-line.cron", "scripts/catch-up.cron"):
    t = open(f"{R}/{f}").read()
    assert "CRON_TZ" not in t, f
    assert not C.validate(t.replace("@USER@", "box").replace("@QA_HOME@", "/q").replace("@PANOPTES_DIR@", "/p")), f
cu = jobs(open(f"{R}/scripts/catch-up.cron").read())
assert len(cu) == 1 and cu[0].split()[:5] == ["7", "*", "*", "*", "*"], cu
assert "PANOPTES_CATCHUP_GATE=even" in cu[0] and "--catch-up" in cu[0] and "%" not in cu[0], cu
assert all(l.split()[1] == "*" for l in jobs(open(f"{R}/pi/cron/pi-line.cron").read()))

# 2) 门控：奇数北京小时 → 一行 skipped、exit 0、不往下走（catch-up.lock 都不建）；偶数 → 继续往下（锁被占时打 lock 日志）
tmp = tempfile.mkdtemp(); os.makedirs(f"{tmp}/argus-root")
def run(tz, hour, gate="even", hold_lock=False):
    env = {**os.environ, "TZ": tz, "QA_HOME": tmp, "PANOPTES_CATCHUP_GATE": gate, "PANOPTES_BOOTSTRAP": "0", "PANOPTES_TMP_SWEEP": "0",
           "PANOPTES_SEVERITY_PIN_CHECK": "0", "QA_SECRETS_FILE": "/nonexistent"}
    if hour is not None: env["PANOPTES_FAKE_BJ_HOUR"] = hour
    cmd = [f"{R}/run-nightly.sh", "--catch-up", "--trigger", "catch-up"]
    if hold_lock: cmd = ["flock", f"{tmp}/argus-root/catch-up.lock"] + cmd
    return subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=60)
for tz in ("America/Los_Angeles", "Asia/Taipei"):
    for h in ("01", "09", "13", "23"):
        r = run(tz, h); lines = [l for l in r.stdout.splitlines() if l.startswith("[catch-up]")]
        assert r.returncode == 0 and len(lines) == 1 and f"Beijing hour {h} is odd" in lines[0] and "+08:00" in lines[0], (tz, h, r.stdout, r.stderr)
        assert not os.path.exists(f"{tmp}/argus-root/catch-up.lock"), (tz, h)
    for h in ("00", "08", "10", "22"):
        r = run(tz, h)
        assert "is odd" not in r.stdout, (tz, h, r.stdout)   # 过了门控（之后因测试环境缺依赖/密钥退出，与门控无关）
# 没有门控变量（mergewatch / outage 补跑 / 手动）：奇数点也不拦
r = run("America/Los_Angeles", "09", gate="")
assert "is odd" not in r.stdout, r.stdout
# 不伪造小时：真实北京小时，按奇偶判断，两种盒子时区结论一致
import datetime as dt
bj = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=8)).hour
res = {tz: "is odd" in run(tz, None).stdout for tz in ("America/Los_Angeles", "Asia/Taipei")}
if dt.datetime.now(dt.timezone.utc).minute not in (59, 0):     # 跨整点那一刻不判
    assert res == {tz: bj % 2 == 1 for tz in res}, (bj, res)
print("ok test_cron_tz")
