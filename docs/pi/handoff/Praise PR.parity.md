# Praise PR · Grok ↔ Pi 机制对齐表（第二阶段 A，2026-10-23 13:55 北京时间）

只写了这个文件。没写 GitHub、没 push、没开 PR，也没改 /workspace/fleet-mode、cron、别人的脚本和 Pi 脚本。
依据：`fleet-standards/Praise-PR.md`，`pi-fallback/bin/{run.sh,lib.py,prretro.py,prfix.py,outbound.py}`，`pi-fallback.cron`，以及本次 dry-run（下面列出）。
标记：**一致** / **已修**（之前已修，本次复核过）/ **无法对齐**（写明原因和需要什么）/ **不一致·待修**（代码能修，本次只读没动）

## 0. 切线门控
| 机制 | Grok 线 | Pi 线 | 状态 |
|---|---|---|---|
| 模式文件 | 两个 routine（09:41 `praise-pr`、22:23 `pr`）开头读 `/workspace/fleet-mode`，值为 `pi` 就立即退出（Praise 本人在改这两个 routine，本 bot 没动） | `lib.py:67-75 mode()` 读同一个文件，缺失或非法值按 `grok` 处理；外发只看 `outbound.py:280` 的 `gate = mode()=="pi" and outbound-enabled 存在` | 一致（Grok 侧以 Praise 的修改为准；`pi` 以外的值，比如空文件，Grok 侧要同样按 grok 处理，这点需要 Praise 确认） |
| Pi 侧生成任务 | — | prretro/prfix 不看模式，始终跑（影子模式），只有外发受门控 | **不一致·待修**：在 grok 模式下，prfix 正式模式会在 `/workspace/mivo-canvas-plugin` 里建分支 `fix/qa-<N>-<fp8>` 和 worktree `praise-pr/pi-wt-<N>` 并本地 commit（prfix.py:9、138），可能和 Grok 线的本地分支混在一起或撞车。建议 grok 模式下 prfix 只跑 `--dry-run`（在 run.sh 或 prfix 里加一行判断） |
| 当前开关状态 | — | fleet-mode=`pi`；`outbound-enabled` 不存在（只有 `.off-1151`）→ 外发闸门关着 | 记录 |

## 1. 09:41 每日复盘（routine `praise-pr`）↔ prretro.py
| 机制 | Grok | Pi（证据） | 状态 |
|---|---|---|---|
| 触发 | 每天 09:41 | cron `45 * * * *` + run.sh `@09`，即每天 09:45 北京时间（run.sh 里 TZ=Asia/Shanghai，按北京小时判断） | 一致（差 4 分钟，不影响结果） |
| 资源门槛 | MemAvailable ≥3G | run.sh：<3072MB 就跳过；flock 防止同一任务并发 | 一致 |
| 输入 | 我开的所有 PR、CI、reports/merged/PR-*.json | `gh pr list` 取 PraiseZhu 的 PR，加 `gh pr checks`、reports/merged、notes/、QA 台账 | 一致 |
| 覆盖规则 | 所有 P0–P2 都要有修复 PR 或无法复现的说明 | prretro.py:3、52：按「正文 `QA: #N`→notes→台账」的顺序查找对应，查不到标 ❌。dry-run：117 条中缺对应 14 条（#1018 #1028 #1029 #1098 #1124 #1156×2 #1196 #1216×2 #1223 #434 #907 #928） | 一致（比上次交接多出 #1223，是新报告） |
| 产出 | 复盘文本（交给 Praise） | `out/praise-pr/<日>.retro.md`，`--dry-run` 只打印 | 一致（格式不同、内容同口径；Grok 当天的复盘文件不在盒子上，没法逐行比对，见 §5） |
| 外发 | Grok 对话内 | 无外发（复盘只写文件） | 一致 |

## 2. 草稿 → ready（规则 2026-10-21 18:09）
| 机制 | Grok | Pi（证据） | 状态 |
|---|---|---|---|
| 范围 | 只处理自己的草稿修复 PR；正式 PR 不碰 | prretro.py:64-67：只看 open、isDraft、PraiseZhu、正文带 `QA: #N` 的 PR；已 ready 的只记录「不碰」 | 一致 |
| 判定 | 除 review-pr 外所有 CI 绿，没有失败也没有 pending | prretro.py:71-74：去掉 review-pr 后检查项非空，且全部为 pass/skipping | 一致 |
| 动作 | `gh pr ready` | actions key `ready:<PR>`，类型 `gh.pr.ready`（prretro.py:75）→ outbound `h_ready`（outbound.py:201-208） | 一致 |
| 幂等 | 不重复转 | `lib.record_actions`：同 key 合并、已 done 的不重做（lib.py:87-90）；执行前 `state()` 查 isDraft，非草稿就记 done，PR 已关就记 stale | 一致 |
| 时效 | 草稿 CI 一变绿就转（Grok 线随时可以转） | 每天只在 09:45 判定一次；outbound 每 10 分钟执行一次，只覆盖最近 2 天的 actions | **不一致·待修**：Pi 最多晚约 24 小时才转。建议 prretro 第 2 节改成每小时跑一次（零模型，开销很小） |
| 执行时复核 CI | — | h_ready 执行时只查 isDraft，不再查 CI；`validate()` 只打印状态 | **不一致·待修**：如果 09:45 到执行之间 CI 由绿转红，或 head 有了新提交，仍会转 ready。建议在 action 里带上 head（已经带了 `head`），执行时比对 headRefOid 和 checks |
| 转正后 | 不碰：不触发 review-pr、不转回草稿、不推提交 | Pi 没有任何 `--undo`/review-pr 逻辑 | 一致 |
| CI 红时在草稿里修 | Grok 线会去修红的检查 | prfix 只处理「新 finding」，不修已有草稿 PR 的红 CI | **无法对齐**：这需要模型改已有分支并 push 到已有 PR，outbound 没有对应的动作类型，要新写一个 `git.push(existing draft)` 处理器，加上「只推草稿、不 force」的校验。需要 Praise 决定是否开放 |

## 3. 22:23 清理（routine `pr`）↔ prretro.py 第 3 节
| 机制 | Grok | Pi | 状态 |
|---|---|---|---|
| 触发 | 每天 22:23 | 合并进 09:45 的 prretro，**没有 22:23 的任务** | **不一致·待修**（加一条 cron `23 * * * * box $R prretro @22` 加 `--cleanup-only` 参数即可） |
| 规则 | 删掉已合并 PR 的本地分支、worktree、/tmp 临时文件；完成标准：`worktree list` 里不再有已合并 PR | 只列出、不删：分支 tip 是合并 head 的祖先且 worktree 干净 → 「可清理」。dry-run：wt-1143(#1169)、fix-1215-missing-at(#1216)、wt-892(#1148)、wt-1146c(#1173)；保留 2 个；分离 HEAD 8 个交给人判断 | **无法对齐**：Praise-PR.md §4 写着「任何删除类操作要 Praise 本人决定」，和 routine 的自动删除冲突。要 Praise 拍板：Pi 是否也自动执行 `git worktree remove`/`git branch -d`（不用 -D/--force） |
| 范围 | 含 panoptes worktree、/tmp | 不扫 panoptes 仓的 `praise-pr/panoptes-wt-*`，/tmp 只看 `/tmp/pi-prfix` | **不一致·待修** |

## 4. 修 bug（QA 报告 P0–P2 → 修复 PR）↔ prfix.py + outbound
| 机制 | Grok | Pi（证据） | 状态 |
|---|---|---|---|
| 触发 | 不定时：收到报告或被点名 | cron 每天 14:20 跑一题（heavy 模型） | **无法对齐**：Pi 每天 1 题，吞吐远低于 Grok。要提高需要 Praise 决定模型额度 |
| 选题 | 所有 P0–P2 都要修（10-21 新规） | P0/P1，加上 `fleet-standards/p2-rulings.md` 里裁决为「修」的 P2（prfix.py:3、34）；本次 `--list` 已出现 #1124 P2 TODO | **无法对齐·要拍板**：「所有 P2」还是「只修判了修的 P2」，规则有冲突 |
| 跳过 #923 | #923 修复 PR 一律不开 | prfix.py:17 `GATED={923}`（`--list` 输出「Praise 闸」）；outbound 台账里 `fixpr:923` = skipped(gated) | 一致（已修）。剩余风险：outbound.h_fixpr 自身没有硬编码 #923，只靠台账那一行。建议 outbound 也加一条硬闸 |
| 最多 2 轮 | 第 3 轮转人工 | prfix.py:17、55 `MAX_ROUNDS=2`：按正文带 `QA: #N` 的 PR 数计算；#926 显示 3 轮、转人工 | 一致（口径：覆盖率类 PR 也算一轮，和 Grok 的数法可能不同，要确认） |
| 复现门槛 | 必须有复现证据，测试先失败后通过 | 先写测试并确认失败（否则 NOREPRO）→ 修复后通过 → 撤掉修复再失败 → 恢复后通过（交接 §prfix） | 一致 |
| 机器门槛 | ≥3G、nightly 不在跑 | MemAvailable≥3G、panoptes 空闲、PATH 以 /usr/local/bin 开头（prfix.py:11） | 一致 |
| 草稿开 PR，正文 `QA: #N` | `gh pr create --draft`，正文 `QA: #N` | prfix.py:136-141 写 body 和 action `fixpr:<N>:<fp>`，`body_must_contain="QA: #N"`；h_fixpr 用 `--draft` 并补上 must 文本（outbound.py:228-229） | 一致 |
| 禁止 force / push main / --no-verify | 禁止 | prfix 不用 --no-verify、不 force；h_fixpr 推 `qa-fix/...` 新分支，`git push -u` 不带 force | 一致 |
| **prfix 的 action 和 outbound 处理器的衔接** | — | prfix.py:138 写的类型是 `git.push+gh.pr.create(draft)`，但 outbound.handler（outbound.py:263-273）只认 `startswith("gh.pr.create")` → 返回 **unsupported**。另外 h_fixpr 要求 `patch` 字段，并在临时 clone 里另建 `qa-fix/` 分支，prfix 给的是 `branch`/`worktree`/`cmds` | **不一致·待修（阻断）**：切到 pi 并打开外发后，新的修复 PR 一个也开不出来。改法二选一：①prfix 额外导出 `.patch` 并把类型改成 `gh.pr.create(draft)`；②outbound 新增 `git.push+gh.pr.create` 处理器，推 prfix 已提交的本地分支（不 force），再开草稿 PR。两个文件都是 Pi 线自己的脚本，本次按只读要求没动 |
| 撞号 | 有问题时人工处理 | 只在 action 的 note 里提醒：撞号就改目录名再 push 一次，不 force | **不一致**：outbound 不会自动处理 |
| 身份 | PraiseZhu | outbound `GIT_NAME/EMAIL=PraiseZhu/zhuzan@xd.com`（outbound.py:19）；本地仓 user.name=`Praise` | 要核实：prfix 在本地 commit 时用的是 `Praise` |
| 失败重试 | 人工跟进 | 失败记进台账 `failed`，下一个 10 分钟周期重试；只有 merger 有退避（outbound.py:24），praise-pr 没有退避也没有次数上限 | **不一致·待修**：建议 fixpr 失败 1 次就停，转人工 |
| 交接 | ready 后交给 Mac mini 盯梢 / Cindy watcher | 不涉及（Pi 不碰正式 PR） | 一致 |

## 5. 同一天数据对比
- prretro `--dry-run`（今天 13:5x，rc=0）：缺对应 14 条；转 ready 0 个动作（#1166 已 ready → 不碰）；可清理 4 个。
- prfix `--list`：#1156 P1、#1196 P1、#1124 P2 为 TODO，#923 被闸住，#926 转人工。
- outbound `--dry-run --job praise-pr --days 5`（用 PI_FLEET_MODE=grok 模拟，闸门关）：`fixpr:943` done(ledger)，`fixpr:923` skipped(gated)。
- Grok 线当天复盘的原文件（`/workspace/praise-pr/review-2026-10-23.md`）盒子上没有，所以只能拿 backlog-progress.md 和 gh 现状对照，没法逐行比较。要做逐行比较，需要 Grok routine 把复盘写到固定文件。
- 遗留：旧版 prfix 08:59 生成的 `out/praise-pr/2026-10-23-PR923-*.patch` 还在（台账已闸住，没删）。

## 6. 第二阶段 B（一键切换）
两个 Grok routine（09:41 `praise-pr`、22:23 `pr`）开头加门控：读 `/workspace/fleet-mode`，值为 `pi` 就立即结束。这一步由 Praise 本人修改，本 bot 没改 routine。Pi 侧早已按同一个文件门控外发。按简报要求保持暂停，等 Praise 说恢复再启用。

## 更新 2026-10-23 15:10 BJ：prfix 阻断项已修（只改文件）
- bin/prfix.py（备份 bin/prfix.py.bak）：动作类型改为 `gh.pr.create(draft)`，带 `patch`（<day>-<tag>.pr.patch，git diff base..HEAD）、`title`、`body_must_contain: QA: #N`，PR 正文写 .pr.md。outbound.py 未改动。
- 验证：py_compile 通过；outbound.h_fixpr 能识别该动作，state=pending，validate 通过（perm=ADMIN, base=main）。没调 do()，没推送、没开 PR，发送开关没打开。

## 更新 2026-10-23 15:20 BJ：取消每日 1 条上限（Praise 群里确认，走他个人网关 key）
- bin/prfix.py（备份 prfix.py.bak-*-nocap）：原来每次只修 todo[0]，现在把单条修复抽成 fix_one()，一次运行里循环处理所有候选。每条开工前重新查内存（低于 3G 就停）和 panoptes 是否在跑。worktree 已存在只跳过那一条。cron（/etc/cron.d/pi-fallback，每天 14:20）没改。
- 已知限制：同一次运行里预测的 PR 号可能重复，由开 PR 后的改名步骤兜底（老问题）。
