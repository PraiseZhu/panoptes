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

## bot 的工作目录和自动清理（10-24）

bot 建的、用完就扔的东西只放在 `/workspace/pi-work/`（`bin/workroot.py` 定布局）：

| 目录 | 放什么 |
|---|---|
| `clones/mivo`、`clones/panoptes` | bot 自己的 clone（只 fetch；推送走 outbound 自己的临时 clone）。人用的 `/workspace/mivo-canvas-plugin`、`/workspace/praise-pr` 不再被 bot 碰 |
| `wt/mivo/pi-wt-<N>-<fp8>`、`wt/mivo/pi-wt-rev-<N>` | prfix 的修复 / revise 工作树；`wt/mivo-dry/` 是 dry-run 的 |
| `wt/panoptes/pi-infra-<sig8>` | infrafix 的工作树 |
| `wt/opsfix/wt-<sha12>` | opsfix 复测的工作树；依赖在 `cache/nm/<lock 哈希>/`，工作树里只放一个软链接 |
| `tmp/` | 临时目录（outbound 的临时 clone 等） |
| `.trash/` | 回收站：删工作树 = 改名进来（瞬间），后台低优先级 `rm -rf` |
| `locks/` | `mivo.repo.lock` / `panoptes.repo.lock`（clone 的 fetch、建删工作树、删分支都要拿）、`janitor.lock` |

**身份标记**：每个 bot 工作树里有 `.pi-owned.json`（谁建的、哪一单、进程号和启动时间、单子锁、分支）。clone 的 `info/exclude` 让 git 看不见它，
不会被当成修复的一部分提交。**清理只认这个标记**，不靠目录名猜；没有标记的目录永远不删，只报告。

**谁来清**：
- 作业自己：一单结束（失败 / 中断；`enforce` 打开后成功的也算）就把工作树改名进回收站，再 `git worktree prune`、删本地分支——不到 1 秒。
- `janitor`（每 10 分钟；prwatch 看到 bot 的草稿 PR 合并 / 关闭时也会立刻叫它一次）：对账。规则：
  - 带标记、进程已不在（进程号 + 启动时间都对上才算「还在」）、单子锁空闲、单子不在 running 的工作树 → 有没提交的改动先留底
    `out/janitor/salvage/`（≤ 2MB）→ 回收；整个判断在 clone 的 repo 锁里再做一遍。
  - bot clone 里没有工作树在用的 bot 分支（`fix/qa-<N>-<fp8>`、`fix/infra-<sig8>`）→ 删；infrafix 的分支在它的 PR 还在路上时保留。
  - 已关闭的 bot PR 的远程分支（panoptes `fix/infra-*`；mivo 只删**关闭未合并**的 `qa-fix/*`，合并的 GitHub 自己删）：PR 正文必须带 bot 的标记，
    远程分支的最新提交必须等于 PR 的 head 或已在 main 里；gh 出错、分支被推进过 → 保留并写原因。
  - `tmp/*`：超过 6 小时、没有进程在用 → 删；依赖缓存只留最新 2 份和正在被链接的。
  - 测试证据 `argus-root/app/evidence/nightly/<日期>`：留 7 天、总量上限 3G（先删最老的，最新 2 天永远不删）；`cache/step-results`：留 14 天。每小时一次。
  - 剩余空间低于 15G：加紧模式（回收站立即清、缓存只留 1 份、证据上限减半），看门狗私信。
- 硬边界：删之前把路径解析成真实路径，必须在 `/workspace/pi-work/` 或上面两个数据目录里；软链接不跟随；人建的工作树和分支、
  `/workspace/mivo-canvas-plugin`、`/workspace/panoptes` 部署签出、`~/.claude`、`~/.config` 一律不碰。

**开关**：`conf/janitor.json` 的 `"enforce"`。`false` = 演练：每个决定都写进 `out/janitor/last.json` 和 `out/logs/janitor-<日期>.log`，
什么都不删（例外：回收站里本来就是作业扔掉的东西，照清）。上线先演练 24 小时，看过日志没有误删再改成 `true`（走 PR）。
手动：`bin/janitor.py status`、`bin/janitor.py run --dry-run`、`bin/janitor.py reap <wt/下的路径>`。

**看门狗**（只告警）：janitor 40 分钟没成功跑 / 连续 3 次失败；剩余空间低于 15G；有没标记的目录；回收站 1 小时清不掉；
`enforce` 后用完的目录 4 小时还在；一个工作目录被占 4 小时以上。

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
| `janitor` | 自动清理：bot 用完的工作目录、临时文件、旧缓存、过期测试证据，以及已关闭 bot PR 的远程分支（见下节） | 7-59/10 * * * * | 本机看护 |
| `boxcheck` | 机器巡检：每小时只读检查机器状态 | 45 * * * * | 本机看护 |
| `handoff` | 交接仓回执扫描：扫严健、钟行天交接仓里作者的回复 | 5 * * * * @00；20 * * * * @06,15；35 * * * * @18 | PR bot |
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
| `boxhealth` | 机器体检：每小时看内存、进程、磁盘，能修的自动修；按「超过 7 天自动删」真删 npm / pip / apt 缓存、/tmp 里 7 天没动又没人用的东西、日志目录（测试日志、Pi 线日志、box-ops 轮转文件、备份检查日志）里超过 7 天的日志（按文件名里的日期，没有日期看修改时间；删日志只有它一家），超过 200MB 的日志截到最后 20MB | 17 * * * * | 本机看护 |
| `roster` | roster | 23 * * * * @08 | 项目管理/日报 |
| `dailyretro` | 日报复盘：每天 9:56 复盘日报 | 56 * * * * @09 | 项目管理/日报 |
| `outbound` | 消息外发：把各个机器人要发的私信、群消息和 GitHub 操作真正发出去 | */10 * * * * | 对外发送 |
| `credcheck` | 凭证检查：每天 7 点检查各种 token 还有没有效 | 1 * * * * @07 | 本机看护 |
| `opsfix` | 修复复测：修复 PR 合并后用原来的用例重测 | 30 * * * * @09,12,16,20 retest --execute | 修测试系统 |

公共模块（不单独跑）：`lib`、`jsonrec`、`gate_diff`、`gate_port`、`praise_notice`、`finding_policy`、`dm_compat`、`infra_outbound`、`run.sh`、`switch.sh`、`selftest.sh`、`pi-noskills.sh`。

## 模型的隔离环境

`lib.pi()` 启动模型时：环境变量只给白名单（`lib.model_env`），外面再套一层 bwrap（`lib.sandbox_prefix`）：单独的 PID 命名空间和 /proc，存密钥的目录都被遮住。

**整机只读**（10-24，Q4）：模型能改的只有下面这些，别的一律改不了（试了会得到 Read-only file system）——

| 可写 | 什么时候 |
|---|---|
| `~/.pi`（会话、用量日志）、`/workspace/mivo-qa/pi/out/tasks`（任务结果）、`~/.npm`（npx 缓存） | 每次调用（模型程序自己要写） |
| 当前工作目录 + 它所在仓库的 git 目录（`git add` / `stash` / `commit` 要写 index、objects、refs） | 只有 `write=True` 的调用（prfix、infrafix） |
| 作业指名的目录（`rw=`） | 只给那一个作业（2026-10-25 起没有作业用到） |
| `/tmp` | 在 bot 工作树里的调用：一个私有的 `/tmp`（`/workspace/pi-work/tmp/sbx-*`，调用结束就删），看不到机器上真正的 /tmp；其他调用照旧 |

改不了的包括：panoptes 部署签出（bot 自己的代码）、人的签出、别的单子的工作树、`/workspace/mivo-qa/pi` 的开关和台账、家目录。
2026-10-24 实测：mivo 工作树里 vitest、eslint、tsc、git add/stash 在沙箱内外结果一致；一次真实模型调用能在工作树里写文件，写 `/workspace/panoptes` 被拒。
单测 `test/test_model_env.py`（机器允许 bwrap 时会真的跑一次，按真实磁盘上的结果判断）。`PI_NO_SANDBOX=1` 只用于排查问题。
