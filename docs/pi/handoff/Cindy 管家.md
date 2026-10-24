# Cindy 管家 · Pi 线交接验收（2026-10-23 12:25 北京时间）

## 职责表
| 职责（Grok 线 ensure-cindy.sh） | Pi 线实现 | 触发 |
|---|---|---|
| 缺失时重装 Cindy（manifest→sudo apt，含 gnome-keyring libsecret-tools xdotool） | cindy.py → ensure-cindy.sh §3 | cron `*/5 * * * * box run.sh cindy` |
| 只经 start-cindy.sh 启动（DISPLAY=:2、setsid、桌面环境变量） | 同上 §4 | 同上 |
| 非脚本启动（缺 --password-store=gnome-libsecret）→ 经脚本重启 | 同上 §3c | 同上 |
| 进程在但 :2 无窗口（xdotool search）→ 重启，每小时最多 1 次、进程需 >120s | 同上 §3d（**已存在**） | 同上 |
| 安装 Cindy 自己下载到 ~/.config/CindyGlobal/updates/ 的更新版 .deb（sha256 校验） | 同上 §3b2 | 同上 |
| 每日 manifest 更新检查 | 同上 §3b（.last-update-check） | 同上 |
| 鼠标轻推 / xset 防息屏 / nudge-loop | 同上 §2、§2b | 同上 |
| 登录健康（signed-out / safeStorage 报错） | ensure STATUS 的 login=；cindy.py 记录 mode | 同上 |
| 只查进程（不重启） | boxcheck.py | `45 * * * *` |

## 脚本
- `/workspace/panoptes/pi/bin/cindy.py`（门控：`takeover/cindy` 存在才动；`--dry-run` / `PI_CINDY_DRYRUN=1` 只读）
- `~/.config/cindy-launch/ensure-cindy.sh`（唯一执行逻辑，Pi 与 Grok 共用）、`start-cindy.sh`、`nudge-loop.sh`
- `run.sh`：MemAvailable<3G 跳过、`/tmp/pi-fallback-cindy.lock` flock、日志 out/logs。

## 跑的命令
```
python3 bin/cindy.py --dry-run                      # → ensure 将做: 无（只 nudge）
bash ~/.config/cindy-launch/ensure-cindy.sh          # → STATUS=SKIP pi-line takeover/cindy active（守卫生效）
PI_TAKEOVER_DIR=/tmp/empty-to python3 bin/cindy.py   # → takeover/cindy absent -> skip
bin/run.sh cindy                                     # 真跑 → ensure-cindy.sh rc=0: STATUS=OK
```
真跑前后 Cindy PID 均为 53786（0.1.115，cloud 登录，窗口在），未被重启。

## 产出路径
- 运行记录：`/workspace/mivo-qa/pi/out/cindy/<日>.md`（dry-run：`<日>.dryrun.md`）
- 日志：`/workspace/mivo-qa/pi/out/logs/cindy-<日>.log`；ensure 启动日志 `~/.local/state/cindy.log`；apt 日志 `/tmp/cindy-apt.log`

## 与 Grok 对比结果
- 旧 cindy.py：只在进程不在时才调 ensure → 缺：下载更新安装、每日 manifest 更新、非脚本启动重启、无窗口重启、轻推。**已修**：每次都调 ensure（与 Grok routine 完全同一脚本），等价。
- 无窗口重启：ensure §3d 已有（每小时 ≤1 次），无重启循环。
- 单实例：run.sh flock + ensure 新加 `/tmp/ensure-cindy.lock`。
- 双跑：Grok routine 已暂停；另在 ensure 顶部加守卫，takeover/cindy 存在时非 Pi 调用者（无 PI_CINDY_CALLER）直接 SKIP，即使 routine 被误恢复也不会动手。
- boxcheck.py 只 pgrep，不重启（已核）。
- 重置恢复：20-system 恢复 cron 守护进程 → 50-bots.d/cindy.sh 装 Cindy → 60-pi-line.sh 重装 `/etc/cron.d/pi-fallback`（与仓内副本一致）+ takeover 目录；cindy.py/ensure 在 /workspace（同步）和 ~/.config/cindy-launch（30-files 恢复）。结论：重置后 cron + cindy.py 会回来。

## 改动（均留 .bak）
- `bin/cindy.py` → 备份 `bin/cindy.py.bak-20261023-1224`
- `~/.config/cindy-launch/ensure-cindy.sh` 顶部加接管守卫 + flock → 备份 `ensure-cindy.sh.bak-20261023-1224`
- `bin/boxcheck.py` 仅改 Cindy 那行说明文字 → 备份 `bin/boxcheck.py.bak-20261023-1224`
- 未动：外发闸文件、Grok routine、Cindy DB、运行中的 Cindy。

## 未解决问题
1. 重置后 `takeover/` 目录若只被 mkdir 为空（未同步 cindy 标记文件），cindy.py 会跳过；且 restore 的 50-bots.d/cindy.sh 直接调 ensure，若 takeover/cindy 已在则被守卫 SKIP（需 `ENSURE_FORCE=1`），之后由 cindy.py 在 5 分钟内补启动——前提是 takeover/cindy 在。建议 60-pi-line.sh 按 fleet-mode=pi 恢复 takeover/cindy，或 cindy.sh 加 `ENSURE_FORCE=1`（grokbot-box 文件，未改）。
2. README 覆盖表 Cindy 行仍写“⚠ 只查”，需 Jasper 更新为 ✅ cindy.py 接管。
3. 切回 grok（switch.sh grok）时需确保删除 takeover/cindy，否则恢复的 Grok routine 只会 SKIP。
4. 重装依赖 sudo 免密 apt 与外网 hotfix.cindy.app；manifest 失败回退 0.1.103（旧）。

## 需要 Praise 提供的东西
- 盒子重置后：`~/.config/cindy-launch/keyring-pass`（不进同步，经 gen-secrets 备份）若丢失需重新提供；Cindy 若掉到 signed-out 需 Praise 在 :2 桌面重新登录。
- 确认是否永久由 Pi 线接管（决定 Grok routine 是删除还是保留暂停）。

## 增补 2026-10-23 13:51 北京时间：switch.sh 同步 takeover 标记
- 改动：`bin/switch.sh` 新增——`switch.sh pi` 创建 `takeover/cindy`、`takeover/boxhealth`；`switch.sh grok` 删除二者；`--dry-run` 只打印。备份 `/workspace/panoptes/pi/bin/switch.sh.bak-20261023-1351`。（解决上文未解决问题 3）
- 只做了 dry-run，未真切换；fleet-mode 仍为 pi，外发闸未动，takeover/ 仍有 boxhealth、cindy。

```
### switch.sh grok --dry-run
fleet-mode: pi -> grok
DRY: echo grok > /workspace/fleet-mode
outbound-enabled: absent
DRY: rm -f /workspace/mivo-qa/pi/takeover/cindy
DRY: rm -f /workspace/mivo-qa/pi/takeover/boxhealth

RESUME these Grok Bot routines (in the Grok Bot app; bot-to-bot scheduling inside Grok stays on Grok):
  Mivo QA 日报     : mivo-qa-daily-report 08:30, retro 09:56, followup 15:17, followup-scan 06:17, handoff 回执 18:30/00:00, ledger 23:30, roster 1/15 08:23
  Mivo 测试项目经理: 「Mivo 测试线每日核对」10:30, praise-pr-2「Praise PR 白天催办」10–20 每 2h
  Mivo QA 运维     : panoptes-env-guard (hourly), panoptes-daily-review 10:57  (mivo-qa-on-merge may stay: catch-up cron covers it)
  Praise PR        : 9:41 复盘, 22:23 清理
  Mivo 合并官      : mivo-merge-sweep :13/:43
  备份管家         : 「grokbot-box 推送监控」:40
  电脑运维         : box-health :17        (Pi job gated by takeover/boxhealth)
  Cindy 管家       : 「Cindy 保活」@every 5m (Pi job gated by takeover/cindy)
  知识沉淀         : 周五 8:04 选题, 工作日 10:04 X 风格收集 (needs X_BEARER_TOKEN + takeover/xstyle)
  Jasper           : 9:00 简报
### switch.sh pi --dry-run
fleet-mode: pi -> pi
DRY: echo pi > /workspace/fleet-mode
outbound-enabled: absent
DRY: mkdir -p /workspace/mivo-qa/pi/takeover && touch /workspace/mivo-qa/pi/takeover/cindy
DRY: mkdir -p /workspace/mivo-qa/pi/takeover && touch /workspace/mivo-qa/pi/takeover/boxhealth

PAUSE these Grok Bot routines (in the Grok Bot app; bot-to-bot scheduling inside Grok stays on Grok):
  Mivo QA 日报     : mivo-qa-daily-report 08:30, retro 09:56, followup 15:17, followup-scan 06:17, handoff 回执 18:30/00:00, ledger 23:30, roster 1/15 08:23
  Mivo 测试项目经理: 「Mivo 测试线每日核对」10:30, praise-pr-2「Praise PR 白天催办」10–20 每 2h
  Mivo QA 运维     : panoptes-env-guard (hourly), panoptes-daily-review 10:57  (mivo-qa-on-merge may stay: catch-up cron covers it)
  Praise PR        : 9:41 复盘, 22:23 清理
  Mivo 合并官      : mivo-merge-sweep :13/:43
  备份管家         : 「grokbot-box 推送监控」:40
  电脑运维         : box-health :17        (Pi job gated by takeover/boxhealth)
  Cindy 管家       : 「Cindy 保活」@every 5m (Pi job gated by takeover/cindy)
  知识沉淀         : 周五 8:04 选题, 工作日 10:04 X 风格收集 (needs X_BEARER_TOKEN + takeover/xstyle)
  Jasper           : 9:00 简报

Pi takeover markers present: boxhealth cindy 
Gated jobs without marker stay off: roster dailyretro xstyle (touch /workspace/mivo-qa/pi/takeover/<name> to enable)
NOTE: outbound still closed (files only). Re-run with --with-outbound to let bin/outbound.py send.
```
