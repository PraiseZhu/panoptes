> 历史记录（写于 10-24 Pi 线并入 panoptes 之前）：文中的 `/workspace/pi-fallback/...` 是旧位置——代码现在在 `/workspace/panoptes/pi/`，状态在 `/workspace/mivo-qa/pi/`，对照见 `pi/README.md`「三类存放位置」。

# Mivo QA 运维 · Grok ↔ Pi 机制对齐表（2026-10-23 14:40 北京时间）

范围：只写文件。没开闸，没恢复或修改任何 Grok routine，没开 issue，没往 mivo-canvas-plugin 推任何东西，panoptes 只在本地分支上改（没推、没合）。
状态：**一致** = 机制相同且有证据；**已修** = 这次改过之后才一致；**无法对齐** = 写明原因和需要什么；**待拍板** = 需要 Praise 决定。

## 一键切换（B）
| Grok routine（北京时间） | 门控（要加在 routine 开头，Praise/Grok 侧去改，我没有改 routine） | Pi 等价作业 | 切线 |
|---|---|---|---|
| mivo-qa-on-merge（合并事件触发） | 先读 /workspace/fleet-mode：值是 `pi` 就立刻结束（不调模型、不发消息），值是 `grok` 才干活 | 不迁：panoptes catch-up cron（`7 */2 * * *`，两种模式都跑，见 #2） | 只改 fleet-mode |
| panoptes-env-guard（每小时） | 同上 | `run.sh opsenv`（:25）只查；修复要 fleet-mode=pi **且** `/workspace/pi-fallback/ops-repair-enabled` | 只改 fleet-mode（要让 Pi 修复还需建开关文件） |
| panoptes-daily-review 10:57 | 同上 | `run.sh opsreview @11`（11:15） | 只改 fleet-mode |

routine 开头可直接粘贴的门控文字：
> 第一步：用 Shell 读 `/workspace/fleet-mode`。内容是 `pi`（或文件不是 `grok`）就立刻结束本次运行，不调用任何模型、不发任何消息、不写报告；只有内容是 `grok` 才继续往下做。

### 切换步骤
**Grok → Pi**
1. `bin/switch.sh pi --dry-run` 看清会改什么，再 `bin/switch.sh pi`（写 fleet-mode=pi，建 takeover/cindy、boxhealth；外发闸不动）。
2. 运维这条线：Grok 三个 routine 有门控就可以保持启用（读到 pi 自己退出）；没加门控前要手动暂停。
3. 可选：`touch /workspace/pi-fallback/ops-repair-enabled`，让 opsenv 自己跑 bootstrap 修复（会 sudo 装 apt/node）。只有 Grok env-guard 确认不再修复时才建，免得两边同时修。
4. 核对：下一个 :25 `out/logs/opsenv-<日>.log` 显示 `mode=pi`；11:15 生成 `out/ops/review-<数据日>.md`。

**Pi → Grok**
1. `bin/switch.sh grok`（写 fleet-mode=grok，删 takeover 标记）。
2. `rm -f /workspace/pi-fallback/ops-repair-enabled`（Pi 修复只在 fleet-mode=pi 时才会跑，这一步是保险）。
3. 恢复（或保持启用）Grok 三个 routine。opsenv、opsreview 仍会照常写影子文件（只写 out/，不冲突）。
4. 核对：Grok env-guard 下一小时有输出；10:57 写出 review-ops/daily/<日>.md。

catch-up cron 两种模式下都跑，而且不读 fleet-mode。这没问题：它会跳过已经有报告、或者 on-merge 正在跑/排队的 PR（panoptes #16），所以和 Grok on-merge 同时存在也不会重复测。

## 机制逐行对齐（A）
| # | 机制 | Grok 线 | Pi 线实现与证据 | 状态 |
|---|---|---|---|---|
| 1 | 合并即测的触发 | mivo-qa-on-merge：合并事件到了就跑 `run-nightly.sh --pr N --trigger event --wait-lock --create-issues`（control.log:8004，#434 22:38 合并后 23:38 启动） | 没有事件源。由 catch-up 兜底：偶数小时 :07 扫昨天和今天合并的 PR | **无法对齐（延迟）**。需要 GitHub webhook/事件转发到盒子，或者 Praise 接受这个延迟 |
| 2 | 延迟 | 合并后几分钟开测，加上测试时间（中位 16 分钟，最长约 32 分钟） | 等下一轮最多 2 小时，加上测试时间，单个 PR 最坏约 **2.5 小时**。合并扎堆时串行排锁，12 个 PR 再多 3–6 小时（10-21 实例：晚了 164 和 178 分钟）。日报 08:35 只看前一天，不受影响；受影响的是当天就要看结果的人 | **无法对齐**，代价可量化。可以缩短：把 cron 改成每小时 `7 * * * *`（panoptes 仓库里的 scripts/catch-up.cron，归 panoptes 管，我没改），最坏延迟降到约 1.5 小时 |
| 3 | 排队、锁、去重 | `--wait-lock`（14400 秒），nightly.lock | 同一把锁。catch-up 跳过已有报告和 on-merge 在跑的 PR（#16、#17） | 一致 |
| 4 | 开 issue（外发） | on-merge 带 `--create-issues`，条件是有测试佐证的 P0/P1 | catch-up cron 带 `ARGUS_ALLOW_ISSUES=1 --create-issues`，**绕开了 Pi 线外发闸**（fleet-mode/outbound-enabled 管不到它） | **待拍板**，选项见下文「#4 选项」 |
| 5 | 报告产出 | reports/merged/PR-N.json，schema 1.0 | 同一个 run-nightly.sh 写同一路径 | 一致 |
| 6 | 环境自检：检查项 | env-guard：chromium/cron/node/npm/venv/catch-up/repo | opsenv.py：bootstrap-box.sh --check，加上抽查和 HEAD 对 origin、gh 登录、secrets 600 | 一致（12:24 实跑全部就绪） |
| 7 | 环境自检：修复 | env-guard 发现问题就修 | 默认只查；修复需要 ops-repair-enabled、fleet-mode=pi、panoptes 没在跑，修完再查一遍 | **待拍板**：开关文件没建（见切换第 3 步） |
| 8 | 环境自检：告警 | 发到 Grok 群 | env-<日>.alerts.json（只写文件） | **无法对齐**：Grok 群没有 API，需要 Praise 定 Slack 或飞书渠道 |
| 9 | 复盘时间 | 10:57 | 11:15（排在 10:07 catch-up 之后） | 一致（刻意错开） |
| 10 | 复盘内容 ①–⑦ | collector 加主持人手工审：测试分配逐条审、触发原因、issue 规则 | collector 原样输出，加上 review tier 重查 P1（最多 4 次调用） | **无法对齐**：手工判断部分要接模型（work tier）去逐条审，可以做但需要 Praise 同意多花调用 |
| 11 | P1 校准：提议 | 主持人判断后直接改报告 | 只写提议，从不改 reports/merged/ | 一致（刻意改成要人确认，原因见 #1137） |
| 12 | P1 校准：落盘字段 | 手工改：问题级别、PR 级 severity/verdict、summary 计数、gate failed→passed 并带 calibrated_from、code_review.status | `opsfix.py calibrate --apply`：**这次补了 recompute()**，重算 PR severity/verdict、summary 两组计数，没有 P0/P1 时把 gate 改成 passed 并带 calibrated_from，status 也改。测试 `/workspace/pi-fallback/test/test_opsfix_recompute.py` 拿 PR-1145 的备份重放，和 Grok 手工结果逐字段一致（severity、verdict、gate、status、prs_by_severity）。差别：Pi 还把 code_review.verdict.status 改成 passed，Grok 那次没改（Grok 那份文件本身前后不一致） | **已修**（备份 opsfix.py.bak-20261023-1430-parity） |
| 13 | 复盘发群 | 发到「MivoCanvas QA」Grok 群 | review-<日>.post.md 加一条 action（grok.group，幂等 key `ops-review:<日>`），没有执行器 | **无法对齐**：同 #8 |
| 14 | 正式复盘文件 | review-ops/daily/<日>.md | out/ops/review-<日>.md（影子） | **待拍板**：Pi 切过来以后谁写正式版 |
| 15 | 补测/重跑 | 主持人手动跑 | opsfix backfill：内存不低于 3G、绝对路径 --out、记 manual-runs.jsonl、旧报告进 history/PR-N.vK.json、--no-issues | 一致 |
| 16 | 内存/并发闸 | 无明确闸 | run.sh 内存低于 3G 跳过，每个作业一把 flock | 一致（Pi 更严） |
| 17 | 模型 | Grok 模型 + panoptes 网关 gpt-6-luna | 复盘用 pi-auto review（luna）；panoptes 本身不变 | 一致 |

### #4 选项（catch-up 的 --create-issues）
- A. **保持现状**：Grok 线一直就是这样，panoptes 开 issue 本来就不经过 Grok。代价是 Pi 线「外发只写 actions.json」这条承诺对 issue 不成立。
- B. 改成 `--no-issues`，另写一个 Pi 作业：读当天报告里有测试佐证的 P0/P1，写成 `gh.issue` action（幂等 key `issue:<PR>:<fingerprint>`，去重依据 issues.json），由 outbound.py 在开闸后执行。外发就全部经过一个闸门。工作量是新作业加执行器。
- C. 在 crontab 里让 ARGUS_ALLOW_ISSUES 跟着 fleet-mode 走（例如 `$( [ "$(cat /workspace/fleet-mode)" = grok ] && echo --create-issues || echo --no-issues )`）：grok 模式开 issue，pi 模式不开。改动最小，但 pi 模式下真 P0/P1 就没人开 issue，除非同时做 B。
- 建议 **B+C**。crontab 由 panoptes 仓库的 bootstrap-box.sh 安装，改它属于 panoptes 改动，需要 Praise 批准，我没动。

### #1137 P1 校准分歧：重新核实
- 发现内容（PR-1137 报告，reviewed_sha eca0f5d）：`hydrateOwnedMediaHashes`（hydrateCanvasMediaRefs.ts:136）遇到不合法的 hash 会 `continue` 跳过，其余图片照样提交；调用方的数量校验在提交之后才执行，所以没法回滚，会留下孤儿资产。
- 生产链路：唯一的生产调用方是 agentBridge.ts:650，`materializeConversationGenerateRefs(planned.refs, hydrateOwnedMediaHashes)`。planned 来自 `planConversationGenerateRefs`，它对 `input.hashes` 里不合法的值直接 `return {ok:false, reason:'invalid'}`（conversationGenerateRefs.ts:77–81）；assetUrl 那条路径只有 `hashFromValue` 解析出合法 hash 时才会生成 owned 引用。所以**在 eca0f5d 的生产链路上，触发条件到不了**。这一点 Pi 说对了。唯一的余地：hashExtFromCindyMediaUrl 解析出来的 hash 理论上可能不满足 HASH_RE（`/^[a-f0-9]{16,128}$/i`），我没有深挖。
- 是不是缺陷：函数本身的契约确实有洞，「部分成功提交后再报错」是真的。#1161（11ee478，10-21 17:55 北京）专门修了它：进事务之前先整批校验。这一点 Grok 说对了。
- **结论**：两边各对一半。报告 severity_scale 里 LLM 审查的 P1 会原样记成 P1，但校准看的是真实影响（和 #1145、#1144 的校准口径一样）。这条在生产链路上复现不了，应该**降为 P2**（潜在缺陷，已由 #1161 修复）。不算误报（代码洞是真的），也不该维持 P1。建议落盘：`opsfix.py calibrate --pr 1137 --match "无效 hash" --to P2 --reason "生产链路 planConversationGenerateRefs 已拒绝不合法 hash（conversationGenerateRefs.ts:81），不可复现；函数契约缺陷真实，已由 #1161 修复" --apply`。**我没有执行**，等 Praise 确认。
- 机制层面：这正是 #11「只提议、不自动落盘」的理由。

## 这次在 Pi 脚本里修掉的
1. opsfix calibrate 不重算 PR 级字段 → 补了 recompute()，并加测试（见 #12）。dry-run 也会预览重算结果，例如 `opsfix.py calibrate --pr 1215 --match 未带 --to P2 --reason x` 输出 `PR P1/needs-attention → P2/review-suggested; gate passed`。

## 其他机制缺口（没修，原因已注明）
- G1 code_review.status 的含义：panoptes 把「有阻断级发现」写成 status=failed，collector 和日报会把它读成「审查没跑成」（review-ops/daily/2026-10-20.md 已经误报过一次）。panoptes 本地分支 `fix/code-review-status-semantics`（7a960e8）已修，没推。另外有一个并行做法 `.worktrees/cr-ran`（c8b7bc4，加 `ran` 字段，status 不变），两者选一个就行。
- G2 api-probe 的 field-mismatch 失败类型是 unknown（runner.mjs:566 checkFailureType），导致首测和重跑结果一模一样也只能判成「重跑不完整/unverified」。panoptes 改动，没做，建议另提 PR：把 probe 的「字段 X 期望…实际…」归类为 assertion。
- G3 README 和 /etc/cron.d/pi-fallback 的头注释还写着「盒子是洛杉矶时间、catch-up 在奇数小时」，实际是上海时区、偶数小时。共享文件，没动。
- G4 Pi 作业不按 fleet-mode 停下（grok 模式下也跑影子），只有修复和外发看 fleet-mode。只写文件，所以没有危害，但 out/ops 下会两线并存，这是刻意的。
