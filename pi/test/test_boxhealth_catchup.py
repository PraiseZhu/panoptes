"""python3 test/test_boxhealth_catchup.py — 11-10：平台暂停虚拟机、cron 漏分钟，06:17 / 07:17 的 boxhealth 没启动
（/workspace/box-ops/boxhealth-miss-20261110.txt）。cron 加一条 :47 `$R boxhealth catchup`；run.sh 看当天
boxhealth-<日期>.log 里本（北京）小时已有 "start boxhealth" 就记一行 skip 退出，没有或日志不存在就照常跑。
run.sh 复制到临时目录，bin/boxhealth.py 换成只写标记文件的桩（不真跑体检、不发消息）；时间用 PI_TEST_NOW 注入。"""
import os, re, shutil, subprocess, tempfile, importlib.util
R = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
s = importlib.util.spec_from_file_location("cron_sync", f"{R}/pi/bin/cron_sync.py"); C = importlib.util.module_from_spec(s); s.loader.exec_module(C)

# 1) cron：原 :17 行不变；紧跟一条 :47 catchup，同用户、同 $R 写法；格式校验通过
t = open(f"{R}/pi/cron/pi-line.cron").read()
assert not C.validate(t)
jobs = [l for l in t.splitlines() if l.strip() and not l.lstrip().startswith("#") and not C.ENV.match(l.strip())]
bh = [l.split() for l in jobs if l.split()[6:8] == ["$R", "boxhealth"]]
assert ["17", "*", "*", "*", "*", "box", "$R", "boxhealth"] in bh, bh
assert ["47", "*", "*", "*", "*", "box", "$R", "boxhealth", "catchup"] in bh, bh
assert len(bh) == 2, bh

# 2) run.sh 门控
tmp = tempfile.mkdtemp()
try:
    code, state = f"{tmp}/pi", f"{tmp}/state"; os.makedirs(f"{code}/bin"); logs = f"{state}/out/logs"
    shutil.copy(f"{R}/pi/bin/run.sh", f"{code}/bin/run.sh"); open(f"{code}/bin/lib.py", "w").write("")
    mark = f"{tmp}/called"
    open(f"{code}/bin/boxhealth.py", "w").write(f"import sys\nopen({mark!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n')\n")
    env = {"PATH": "/usr/bin:/bin", "HOME": tmp, "PI_STATE": state, "PI_FLEET_MODE": "test", "PI_TEST_MEM_AVAIL_MB": "8000",
           "DEEPSEEK_API_KEY": "", "TZ": "America/Los_Angeles"}   # run.sh 自己 export TZ=Asia/Shanghai；盒子时区不影响

    def run(now, *args):
        if os.path.exists(mark): os.remove(mark)
        p = subprocess.run(["bash", f"{code}/bin/run.sh", "boxhealth", *args], env={**env, "PI_TEST_NOW": now},
                           capture_output=True, text=True, timeout=60)
        assert p.returncode == 0, (p.returncode, p.stdout, p.stderr)
        return open(mark).read() if os.path.exists(mark) else None

    def put(day, *lines):
        os.makedirs(logs, exist_ok=True)
        open(f"{logs}/boxhealth-{day}.log", "w").write("".join(l + "\n" for l in lines))

    def text(day): return open(f"{logs}/boxhealth-{day}.log").read()

    real = "=== 2026-11-10 {} CST start boxhealth  mode=pi outbound=open"   # 现网写法（两个空格：$* 为空）
    # a) 本小时已跑 → 跳过，记一行 skip，不调 boxhealth.py，也不写新的 start
    put("2026-11-10", real.format("05:17:01"), "=== 2026-11-10 05:17:40 CST end boxhealth rc=0", real.format("08:17:01"),
        "=== 2026-11-10 08:17:30 CST end boxhealth rc=0")
    assert run("2026-11-10 08:47:01", "catchup") is None
    tail = text("2026-11-10").splitlines()[-1]
    assert re.match(r"^=== 2026-11-10 08:47:01 CST skip boxhealth catchup: hour 08 already started", tail), tail
    assert text("2026-11-10").count("start boxhealth") == 2
    # skip 行不能被 watchdog 当成一次 start / skip:（它只认 "=== … CST start" 和 "skip:" 开头）
    assert not tail.startswith("skip:") and " start " not in tail
    # b) 本小时没跑（06:17 漏了）→ 照常跑，boxhealth.py 不收到 catchup 参数
    assert run("2026-11-10 06:47:01", "catchup") == "\n"
    assert "catchup: hour 06 has no start boxhealth -> run" in text("2026-11-10")
    # c) 日志不存在 → 照常跑
    assert run("2026-11-11 00:47:02", "catchup") == "\n"
    assert any("catchup: hour 00 has no start boxhealth -> run" in open(f"{logs}/{f}").read() for f in os.listdir(logs))
    # d) 日期边界：昨天文件里 00 点的 start 不算今天 00 点
    put("2026-11-12", "=== 2026-11-12 00:17:01 CST start boxhealth  mode=pi outbound=open")
    assert run("2026-11-13 00:47:00", "catchup") == "\n"
    # e) 本小时只有 skip 行（catchup 跳过过）不算 start；其他作业的 start 也不算
    put("2026-11-14", "=== 2026-11-14 09:47:01 CST skip boxhealth catchup: hour 09 already started",
        "=== 2026-11-14 09:17:01 CST start boxhealthx  mode=pi outbound=open")
    assert run("2026-11-14 09:50:00", "catchup") == "\n"
    # f) 不带 catchup（:17 正点、手动）不受门控：本小时已有 start 也照常跑
    put("2026-11-10", real.format("08:17:01"))
    assert run("2026-11-10 08:20:00") == "\n"
finally:
    shutil.rmtree(tmp)
print("ok test_boxhealth_catchup")
