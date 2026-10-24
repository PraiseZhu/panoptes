# Pi 线（pi/）

Praise 的自动化全家：修 PR bot、测试系统自修、日报 / 跟进 / 外发、本机看护。原来在 `/workspace/pi-fallback`，10-24 并入 panoptes（Praise 定：不新建仓库）。
详细的运行说明、历史和规则在 `docs/pi/README.md`；修 PR bot 的岗位说明在 `/workspace/fleet-standards/Praise-PR.md`。

## 三类存放位置

| 类别 | 位置 | 备份 | 清理 |
|---|---|---|---|
| 代码 | `/workspace/panoptes/pi`（部署签出；开发在 worktree，走 PR） | GitHub + 每日 bundle | 不清 |
| 要留的数据 | `/workspace/mivo-qa/pi/`：`out/`（各作业的状态、台账、日志）、`takeover/`（接管标记）、`outbound-enabled`、`outbound-disabled-types` | 每小时备份 | 按 `conf/janitor.json` |
| 用完就扔 | `/workspace/pi-work/`：`clones/ wt/ cache/ tmp/ .trash/ locks/` | 不备份 | 秒清 |

代码里只用 `bin/lib.py` 的三个常量：`PI_CODE`（本目录）、`PI_STATE`（默认 `/workspace/mivo-qa/pi`，环境变量可覆盖）、`PI_WORK`（默认 `/workspace/pi-work`）。
`BASE` 是历史名字，等于 `PI_STATE`。shell 脚本同理：`run.sh` 的 `CODE` / `BASE`。

## 怎么改

- **只走 PR**（PraiseZhu/panoptes）。合并后由 `opsdeploy`（每 10 分钟）/ `opsenv`（每小时）按原有保护条件快进部署，下一轮 cron 就用上新代码。
- 提交前跑 `bash pi/bin/selftest.sh`（CI 也跑同一个）：全部 .py 能编译、.sh 能解析、`pi/test/test_*.py` 全过；测试在 `env -i` 白名单环境里跑，看不到真密钥。
- **infrafix / opsfix 不许改 `pi/`**（bot 不改自己）：infrafix 提交前检查，改到 `pi/` 就放弃这一单；外发合并前按 PR 真实 diff 再查一次；CI 对 `fix/infra-*` 分支的 PR 也会拦。三道都把「挪走」算成「从原处删掉 + 在新处加上」，挪出 `pi/` 也拦得住；中文等非 ASCII 文件名照样认得。
- 定时表源文件是 `cron/pi-line.cron`，装到 `/etc/cron.d/pi-fallback`（`sudo install -o root -g root -m 644 cron/pi-line.cron /etc/cron.d/pi-fallback`）。
- 开关（值不变，只换了位置）：`/workspace/fleet-mode`；`$PI_STATE/outbound-enabled`；`$PI_STATE/outbound-disabled-types`；`$PI_STATE/takeover/<作业>`。切换用 `bin/switch.sh pi|grok [--with-outbound] [--dry-run]`。

## 作业清单

| 作业 | 干什么 | 什么时候（cron；`@HH` = 北京时间小时） | 类别 |
|---|---|---|---|
| `mergewatch` | 合并即测：PR 一合并，1 分钟内触发测试 | * * * * * | PR bot |
| `prwatch` | 修 PR 的接单哨兵：盯你自己 PR 上测出的新问题、派修 PR bot 去修 | * * * * * | PR bot |
| `watchdog` | 看门狗：每 5 分钟检查各个自动化有没有停摆 | */5 * * * * | 本机看护 |
| `merger` | 自动合并：判断哪些 PR 可以合并并合并 | 13,43 * * * * | 对外发送 |
| `opsenv` | 测试系统自检和自动部署：每小时检查测试系统环境，把 panoptes 的新代码部署上线 | 25 * * * * | 修测试系统 |
| `opsdeploy` | 测试系统自动上线：每 10 分钟看测试系统有没有新合并的代码，空闲时就部署上线 | 3-59/10 * * * * | 修测试系统 |
| `boxcheck` | 机器巡检：每小时只读检查机器状态 | 45 * * * * | 本机看护 |
| `handoff` | 交接仓回执扫描：扫严健、钟行天交接仓里作者的回复 | 5 * * * * @00；20 * * * * @06,15；35 * * * * @18 | PR bot |
| `knowledge` | 知识沉淀选题：每周五出公众号选题 | 5 * * * * @08 | 知识/文风 |
| `daily` | QA 日报：每天 8:35 出昨天合并 PR 的测试日报，并给作者发私信 | 35 * * * * @08 | 项目管理/日报 |
| `jasper` | 每日简报：每天 9:05 整理给你的工作简报 | 5 * * * * @09 | 知识/文风 |
| `prretro` | 修 PR 复盘：每天 9:45 复盘修 PR bot 前一天的工作 | 45 * * * * @09 | PR bot |
| `pmnudge` | 白天催办：白天每 2 小时看修复 PR 有没有进展 | 5 * * * * @10,12,14,16,18,20 | 项目管理/日报 |
| `opsreview` | 运维复盘：每天 11:15 复盘测试系统前一天的表现 | 15 * * * * @11 | 修测试系统 |
| `pm` | 测试项目经理核对：每天 10:35 核对 QA 的各项目标 | 35 * * * * @10 | 项目管理/日报 |
| `p2` | P2 裁决提议：每天两次给 P2 问题提裁决建议 | 20 * * * * @12,18 | 项目管理/日报 |
| `compare` | Pi 线和 Grok 线对比：每天对比两条线的产出 | 50 * * * * @12 | 知识/文风 |
| `prfix` | 修 PR bot：给你自己 PR 上测出的问题写复现测试、开修复 PR | 20 * * * * @14 | PR bot |
| `ledger` | 问题台账：每晚 23:40 重建所有问题的跟踪台账 | 40 * * * * @23 | 项目管理/日报 |
| `followup` | 作者跟进提醒：提醒作者处理问题、感谢已经回复的作者 | 27 * * * * @06,15 | PR bot |
| `cindy` | Cindy 保活：每 5 分钟检查 Cindy 还活着，挂了自动拉起 | */5 * * * * | 本机看护 |
| `boxhealth` | 机器体检：每小时看内存、进程、磁盘，能修的自动修 | 17 * * * * | 本机看护 |
| `roster` | roster | 23 * * * * @08 | 项目管理/日报 |
| `dailyretro` | 日报复盘：每天 9:56 复盘日报 | 56 * * * * @09 | 项目管理/日报 |
| `xstyle` | xstyle | 4 * * * * @10 | 知识/文风 |
| `outbound` | 消息外发：把各个机器人要发的私信、群消息和 GitHub 操作真正发出去 | */10 * * * * | 对外发送 |
| `credcheck` | 凭证检查：每天 7 点检查各种 token 还有没有效 | 1 * * * * @07 | 本机看护 |
| `opsfix` | 修复复测：修复 PR 合并后用原来的用例重测 | 30 * * * * @09,12,16,20 retest --execute | 修测试系统 |

公共模块（不单独跑）：`lib`、`jsonrec`、`gate_diff`、`gate_port`、`praise_notice`、`finding_policy`、`dm_compat`、`infra_outbound`、`run.sh`、`switch.sh`、`selftest.sh`、`pi-noskills.sh`。

## 模型的隔离环境

`lib.pi()` 启动模型时：环境变量只给白名单（`lib.model_env`），外面再套一层 bwrap（`lib.sandbox_prefix`）：单独的 PID 命名空间和 /proc，存密钥的目录都被遮住。
`PI_NO_SANDBOX=1` 只用于排查问题。收紧成「整机只读 + 白名单可写」的计划见 docs/pi/README.md。
