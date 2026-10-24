> 历史记录（写于 10-24 Pi 线并入 panoptes 之前）：文中的 `/workspace/pi-fallback/...` 是旧位置——代码现在在 `/workspace/panoptes/pi/`，状态在 `/workspace/mivo-qa/pi/`，对照见 `pi/README.md`「三类存放位置」。

# Cindy 管家 · Grok↔Pi 机制对齐表（2026-10-23 13:55 北京时间）

E = `~/.config/cindy-launch/ensure-cindy.sh`（两条线共用的唯一执行脚本），C = `/workspace/pi-fallback/bin/cindy.py`，R = `bin/run.sh`。行号来自 `grep -n`（13:54）。
本阶段 cindy.py 备份：`bin/cindy.py.bak-20261023-1355`（上一版 `bin/cindy.py.bak-20261023-1224`）。

| # | 机制 | Grok 线 | Pi 线实现 | 证据 | 状态 |
|---|---|---|---|---|---|
| 1 | 触发/时间 | 服务端 routine「Cindy 保活」@every 5m | `/etc/cron.d/pi-fallback` L27 `*/5 * * * * box $R cindy` | cron 文件；日志每 5 分钟一段 | 一致 |
| 2 | 线路门控 | routine 先读 /workspace/fleet-mode，pi→立即结束 | C L50 `mode()!="pi"`→skip；L51 还要 takeover/cindy | `PI_FLEET_MODE=grok python3 cindy.py` → `fleet-mode=grok -> skip` | 已修（本阶段新增 fleet-mode 门控） |
| 3 | 输入 | 进程表、dpkg 版本、:2 桌面环境、Cindy main-*.log、updates/patch-info.json、manifest | 同一 E；C 另读同样数据做记录/dry-run（C L13 `would()`） | dry-run 输出见下 | 一致 |
| 4 | 缺失重装 | /usr/bin/cindy 或 gnome-keyring 缺→manifest 版本→sudo apt 装 deb+gnome-keyring libsecret-tools xdotool（manifest 失败回退 0.1.103） | E L29 §3 | — | 一致 |
| 5 | 每日 manifest 更新 | `.last-update-check` 每天一次，新版本才装 | E L38 §3b | stamp=2026-10-23 → dry-run 不列 | 一致 |
| 6 | 安装 Cindy 自己下载的更新 | updates/patch-info.json 版本更高且 sha256 通过才装 | E L53 §3b2 | 当前 patch=0.1.115=已装 → 不动作 | 一致 |
| 7 | 非脚本启动→经脚本重启 | 进程缺 `--password-store=gnome-libsecret` 即重启 | E L68 §3c | 进程带 flag ✓ | 一致 |
| 8 | 无窗口重启 | 进程 >120s 且 `xdotool search --class cindy` 为空，每小时 ≤1 次（.last-window-relaunch） | E L73–79 §3d | 窗口 ✓ | 一致 |
| 9 | 启动方式 | 只经 start-cindy.sh（:2 桌面 env、setsid、30s 后复查） | E L85 §4 | — | 一致 |
| 10 | 鼠标轻推/防息屏 | xset + xdotool ±1px；nudge-loop.sh 每 60s | E L21 §2、L27 §2b | — | 一致 |
| 11 | Plank Dock 启动器恢复 | Grok 侧由 agent 检查/恢复 cindy.dockitem | C L28 `dock()`：缺则写回 `~/.config/plank/dock1/launchers/cindy.dockitem`（dry-run 只报“将恢复”） | 记录 `- Dock: ✓` | 已修 |
| 12 | 登录/凭据日志检查 | 末 200 行 safeStorage/login failed/Sign-in failed/tombstones→problem；最后 mode signed-out→signed-out | E L97–101 §5；C 另记 `登录: mode` | `登录: mode: 'cloud'` | 一致 |
| 13 | 单实例/锁 | 服务端单 routine | R L20 flock `/tmp/pi-fallback-cindy.lock` + E L7 flock `/tmp/ensure-cindy.lock`（跨线共用） | — | 一致（更强） |
| 14 | 双跑防护 | routine 看 fleet-mode | C 看 fleet-mode+takeover；E L4 守卫：takeover/cindy 在且非 Pi 调用（无 PI_CINDY_CALLER/ENSURE_FORCE）→STATUS=SKIP | `bash E` → `STATUS=SKIP pi-line takeover/cindy active` | 已修 |
| 15 | 防重启循环 | 无窗口 1 次/小时；更新只装更高版本；flag 检查只在缺 flag 时 | 同 E | — | 一致 |
| 16 | 产出格式 | E 打印一行 `STATUS=OK / CHANGED actions=… login=… / FAIL …`，agent 回聊天 | C 把 STATUS 写入 `out/cindy/<日>.md`（dry-run：`<日>.dryrun.md`），日志 `out/logs/cindy-<日>.log` | `ensure-cindy.sh rc=0: STATUS=OK` | 一致（位置不同：文件） |
| 17 | 通知 | CHANGED / login≠ok / FAIL 时聊天告诉 Praise，同一失败不重复；OK 静默 | C L33 `notify()`：写 `out/cindy/<日>.actions.json`，type `grok.chat.praise`，key `cindy-notify:<日>:<状态签名>`（CHANGED 用 `cindy-notify:<日>:<HHMM>:changed`）；`out/cindy/notify-state.json` 记上次签名，同一失败不重复，恢复 OK 后重置。outbound.py 无此 type 的 handler → **只写文件不外发** | 模拟：FAIL→记录；同 FAIL→“同一异常已记录，不重复”；OK→重置；再 FAIL→记录 | 已修（文件）；真实送达 **无法对齐**：Grok 聊天没有外部 API，需 Praise 定 Pi 线渠道（Slack/飞书私信）并批准开闸 |
| 18 | 失败重试/退避 | 下个 5 分钟再跑；无窗口 1h 退避 | 同（cron 下次再跑；E 内退避） | — | 一致 |
| 19 | 上限 | 下载 600s、manifest 20s 超时 | 同 E；C 调 E 超时 900s；R 内存 <3G 跳过 | R L15 | 一致（Pi 多一条内存门） |
| 20 | 人工闸门 | 掉登录需 Praise 在 :2 重登；keyring-pass 丢失需 Praise 提供；更新导致重启属自动 | 同；Pi 只记录+写 actions | — | 无法对齐的部分 = 需人：重登/keyring-pass |

### 证据：命令输出（13:54 北京时间）
```
python3 cindy.py --dry-run           → - ensure 将做: 无（只 nudge）   (out/cindy/2026-10-23.dryrun.md)
bin/run.sh cindy                     → 进程 53786 (start-cindy.sh flags ✓) / 窗口 ✓ / 0.1.115 / mode 'cloud' / Dock ✓ / STATUS=OK / 通知: ok
PI_FLEET_MODE=grok python3 cindy.py  → fleet-mode=grok -> skip (Grok routine owns it)
bash ensure-cindy.sh                 → STATUS=SKIP pi-line takeover/cindy active
```
同一天对比：Grok routine 已暂停，今天无 Grok 线 STATUS 可比；两线执行同一 E，判定逻辑相同。运行中的 Cindy 未被重启。

## 一键切换
- **Grok routine「Cindy 保活」（已改）**：开头先读 `/workspace/fleet-mode`，`pi` 立即结束（不调模型不发消息）；`grok` 才跑 ensure-cindy.sh，若残留 takeover/cindy 则带 `ENSURE_FORCE=1`；`STATUS=SKIP` 静默。**保持暂停，等 Praise 说恢复。**
- **Pi 线 cindy.py**：fleet-mode=pi 且 takeover/cindy 存在才动（C L50–51）。
- **switch.sh**：`pi` 建 takeover/cindy、boxhealth；`grok` 删除（备份 `bin/switch.sh.bak-20261023-1351`）。
- 结果：只改 fleet-mode 一个文件即可切线；即使 takeover 标记残留，fleet-mode=grok 时 Pi 不动；E 的 flock 防并发。

## 仍需 Praise
1. Pi 线通知的外发渠道（Slack 或飞书私信）+ 开闸批准；之后给 outbound.py 加 `grok.chat.praise` 替代 handler（Jasper 统一做）。
2. 重置后 keyring-pass；掉登录时在 :2 重登。
3. 何时恢复 Grok routine。

## F4 增补：重启前低内存保护（2026-10-23 13:58 北京时间）
来源：`handoff/电脑运维.parity.md` F4。run.sh 已负责“Cindy 不在且 <2048MB → cindy.py 只读 dry-run、不拉起”；这里补上“Cindy 在跑时，所有会**重启**它的分支”。

- `ensure-cindy.sh`（备份 `~/.config/cindy-launch/ensure-cindy.sh.bak-20261023-1357`）：
  - 新增 `mem_avail()`：自读 /proc/meminfo，读不到时回退 `PI_MEM_AVAIL_MB`；`ENSURE_TEST_MEM_AVAIL_MB` 只用于模拟。
  - `lowmem_defer <分支>`：可用 <2048MB → 输出 `STATUS=lowmem-defer avail=…MB before <分支> (no kill, retry next run)`，exit 0，不 kill。
  - 加在 4 个重启分支的 pkill 之前：§3b manifest 更新（L53，同时删掉 `.last-update-check`，免得当天的更新被跳过）、§3b2 已下载更新（L68）、§3c 非脚本启动（L76）、§3d 无窗口（L87，放在写 `.last-window-relaunch` 之前，不浪费每小时一次的名额）。
  - 普通的“不在 → 启动”（§4）这里不拦（由 run.sh 的 PI_LOWMEM/dry-run 负责）。
- `cindy.py`（备份 `bin/cindy.py.bak-20261023-1358`）：`notify()` 遇到 `STATUS=lowmem-defer` 静默返回，不写 actions.json，也不改 notify-state.json。
- Grok routine：`lowmem-defer` 应和 `SKIP` 一样静默（routine 文本由 Praise/父 agent 维护，我没改）。

### 验证（Cindy PID 53786 前后不变）
| 用例 | 命令 | 结果 |
|---|---|---|
| 语法 | `bash -n ensure-cindy.sh` | ok |
| T1 Cindy 健康，模拟 1500MB | `PI_CINDY_CALLER=1 ENSURE_TEST_MEM_AVAIL_MB=1500 bash ensure-cindy.sh` | `STATUS=OK`（没有重启分支触发，所以保护不介入） |
| T2 强制进入 §3c，模拟 1500MB | /tmp 副本把 §3c 条件改成 true，PATH 里放假 pkill（只 echo） | `STATUS=lowmem-defer avail=1500MB before relaunch-with-script (no kill, retry next run)`，rc=0，假 pkill 没被调用 |
| T3 通知静默 | `cindy.notify(day,"STATUS=lowmem-defer …")`，状态文件指向 /tmp | `lowmem-defer 静默（不记通知，下个 */5 重试）`，没写状态文件 |
| T4 真跑 | `bin/run.sh cindy` | `STATUS=OK` |
