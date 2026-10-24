# Mivo 合并官 · 机制对齐表（Grok 线 ↔ Pi 线）· 2026-10-23 14:00 北京时间

- **Grok 线**：
  - Grok Bot routine `mivo-merge-sweep`（:13/:43）。它不调 GitHub 写，只巡检、只报告，写 audit.log。
  - 盒上的 `run.mjs round`（automerge 应用）。原本 cron 每 5 分钟跑一次，10-22 被 Praise 叫停，现在没有 crontab。
- **Pi 线**：
  - `bin/merger.py`：cron :13/:43，只写文件。
  - 判定在 `bin/gate_port.py`：gate.mjs 的逐行移植。
  - 外发由 `bin/outbound.py` 执行：*/10 跑一次，闸门是 fleet-mode=pi 且 outbound-enabled。

**所有权**：merger.py、gate_port.py 是我的，本轮直接修了。outbound.py 是共享执行器，属 Jasper。按任务书“不碰别人的脚本”，本轮没改，只列缺口和建议改法。
**标记**：一致 / 已修（本轮在 merger.py 修好）/ 缺口-outbound（需 outbound 负责人改）/ 无法对齐（需 Praise 提供凭证或拍板）。

## 证据
- 判定：
  - 差分 10 万例，0 差异：`python3 bin/gate_diff.py <seed> 5000`，真实 gate.mjs 对比 gate_port.py。
  - 回放 audit.log 165/165；877 轮共 2467 项，0 差异：`python3 bin/merger_replay.py`，结果在 `out/merger/replay-2026-10-23.md`。
- 文案：`human_notice_body`、`stall_body`、`circuit_body` 和 notify.mjs 的输出逐字节相同（7 个样例，含未知原因的兜底文案）。delivery marker 的 sha256 和 node 算出来的一致。
- 实跑：13:55 北京时间，`python3 bin/merger.py` → 全局 STOP identity，可写=False，0 条动作。
- 模拟可写轮（只在内存里，不落盘）：动作顺序是先出 HUMAN 评论，再出 1 个 merge，和 round.mjs 相同。

## 对齐表
| # | 机制 | Grok 线（文件:行） | Pi 线（文件:行） | 状态 |
|---|---|---|---|---|
| 1 | 触发 | run.mjs 每 5 分钟一轮（已停）；routine :13/:43 只报告 | cron :13/:43 merger + */10 outbound | **无法对齐**：Praise 10-22 叫停 5 分钟轮询。Pi 线每 30 分钟一轮，只会合得更晚。要完全一致需 Praise 定频率 |
| 2 | 并发锁 | state.mjs lock 目录 | run.sh flock（每个作业一把） | 一致 |
| 3 | 额度预留 core≥1500 / graphql≥1000 | round.mjs:46-51 | merger.py run_round 开头 | 已修 |
| 4 | 全局门 G0–G10、PR 门 P1–C7、两段取结论包、选 1 个 | gate.mjs、round.mjs:95-140 | gate_port.py、merger.py run_round | 一致（差分 + 回放证明） |
| 5 | 维护窗口、skill-sha-quiet | gate.mjs:166-169 | gate_port.evaluate_global | 一致 |
| 6 | 待确认合并 / rule-suite 未确认时不合下一个 | round.mjs:64-80 | merger.py 读 Grok state.json 的 pendingMergeRequests/pendingRuleSuites，有就 held；blockers 同样拦 | 已修。Pi 自己合的没有 rule-suite 记录，见 #15 |
| 7 | **合并方式** | merge.mjs:268 `PUT /pulls/N/merge {sha: head, merge_method: squash}` | outbound.py:247 `gh pr merge --squash --match-head-commit <sha>` | 一致：同一个 API，sha 锁 head；都没有 admin/auto/删分支 |
| 8 | 合并前终检：重新采集、重新判定 | merge.mjs:236-250 | merger.blockers：重新取全局状态、重新深采这个 PR、必要时取结论包、再判一次 | 一致 |
| 9 | kill-switch：main 被推进就放弃（main-moved） | merge.mjs:82-83 | merger.py 合并动作里带 mainHead，blockers 比较当前 main | 已修 |
| 10 | kill-switch：mode/host/状态 issue/熔断标签/PAUSED/PR 标签与 canary 来源 | merge.mjs:55-88 | blockers 里重算 can_write + PAUSED + 全量门（含 canary/hold） | 一致。canary 打标人和打标时间“未变”的比较是近似（重新判定时会再核打标人） |
| 11 | 证据最大年龄 60 秒 | merge.mjs:255 | outbound :242→:245→:247 连续调用，blockers 一跑完就发 merge | 基本一致。**缺口-outbound**：没有显式计时。建议：在 h_merge.do() 里记 blockers 开始时间，超过 60 秒就拒绝（outbound.py:244-247） |
| 12 | 意图记录 intent / pendingMergeRequests | merge.mjs:258-262 | 无 | **缺口-outbound**：建议在 outbound.py:246 发请求前，往 ledger 写 `{status:"in-flight", decisionId}` 并立刻 persist；下一轮见到 in-flight 先回读，不重发 |
| 13 | 结果分类：409 head 不符、405 base 变、422 被拒、429 限流、401/403 身份、5xx 不明 | merge.mjs:17-28 | outbound.py:247 只看 gh 的退出码，失败一律 FAILED + 退避 | **缺口-outbound**：建议解析 gh 的 stderr/HTTP 码，照 classifyMergeResponse 分类。identity → 熔断；backoff → 5 分钟冷却；unknown → 回读 3 次（10/30/60 秒），再设 unknownCooldown 30 分钟 |
| 14 | 合并后回读：merged_by=令牌账号、merge sha、父提交=main_before | merge.mjs:287-297、verifyMergeCommit | 无 | **缺口-outbound**：建议在 outbound.py:247 之后 `gh api pulls/N`，核对 merged_by、merge_commit_sha，以及 commits/<sha> 的 parents==[mainHead]；不符就熔断（见 #19） |
| 15 | rule-suite 自检（bypass/fail/30 分钟读不到就熔断） | merge.mjs:355-389 | 无 | **缺口-outbound**：建议把 Pi 合并记进 `out/merger/merges.jsonl`（含 mergeSha），由 merger.py 下一轮查 rule-suite（只读）；结果 bypass 就出熔断动作 |
| 16 | 合并后在 PR 发“已自动合并”评论（mergedBody） | merge.mjs:340-346、notify.mjs mergedBody | 无 | **缺口-outbound**：建议 h_merge.do() 成功后追加一条 gh.pr.comment 动作，文案照 mergedBody（merger.py 可以提供生成函数） |
| 17 | **每轮上限** 1 | round.mjs:136-141、policy maxMergesPerRound=1 | merger 每轮只出 1 个；outbound.py:21/:315/:323 每轮最多 1 次合并尝试 | 一致 |
| 18 | **每日上限** canary 2 / on 12，按 Grok 自己的 merges 计 | gate.mjs:201-203 G10、state.mergesToday | merger.py：mergesToday = Pi ledger + Grok merges.jsonl，另加 Pi 上限 2；outbound.py:30 只数 Pi ledger | merger 已修（两条线共用当日额度）。canary 档一致；on 档按 Praise 要求固定 2，比 Grok 更严，需 Praise 拍板是否放到 12。**缺口-outbound**：outbound.py:30-32 merges_today 应加上 `/workspace/mivo-merger/state/merges.jsonl` 当天条数 |
| 19 | **熔断**：#1027 加 `automerge:circuit-open` 标签 + circuitBody 评论 + 写本机 PAUSED；identity 只写 PAUSED、不用这个身份写 GitHub | merge.mjs:31-44、round.mjs:84-87、:126-131 | merger.py 在可写时出 `circuit:<reason>:<日>` 动作（类型 gh.issue.circuit，文案逐字节同 circuitBody，标注 also=write PAUSED）；identity 只在报告里记“仅本机 PAUSED” | 判定和文案已修。**缺口-outbound**：handler（outbound.py:263-274）没有 gh.issue.circuit，会落到 unsupported。建议加 h_circuit：`gh issue edit 1027 --add-label automerge:circuit-open`，再 `gh issue comment 1027`，然后写 PAUSED（JSON {reason, at}）。写 PAUSED 要 Praise 批，本轮没碰 |
| 20 | main CI 卡住超过 30 分钟，在 #1027 发 stallBody（每个 mainHead 一次） | round.mjs:220-227 | merger.py 用 state.json 的 `_stall` 记 firstSeen，可写时出 `stall:<mainHead>` 动作（文案逐字节一致，带 delivery marker） | 已修。**缺口-outbound**：类型 gh.issue.comment 没有 handler；现有 h_comment（:249-260）用的是 `gh pr comment`，对 issue 不适用。建议加 issue 评论 handler |
| 21 | **HUMAN 通知文案** | notify.mjs humanNoticeBody | merger.human_notice_body | 已修（逐字节一致） |
| 22 | 通知标记 / 去重键 | key=`PR:head:reason`；state.notices[key]；PR 评论带 `<!-- mivo-automerge:delivery id=sha256(key) -->`，只认 tokenLogin 发的 | merger：同一个 key；Grok state.notices 已有就不出；文本带同一个 delivery marker；动作里带 notice_key/delivery_marker | merger 已修。**缺口-outbound**：clean_body（outbound.py:33-41）会删掉所有 `<!-- -->`，notice 和 delivery marker 发不出去（只剩 :259 补回的 mivo-merger）。h_comment.state（:251）只认 `<!-- pi-line key -->`。建议：对带 delivery_marker 的动作不剥离 `<!-- mivo-automerge:` 开头的标记，state() 再加一条“评论里有 delivery_marker 且作者是 PraiseZhu 即 done” |
| 23 | 通知“更新” | Grok 不改已发的通知，一个 key 只发一次；只有状态 issue 的标记段会被 PATCH | Pi 也不改 | 一致 |
| 24 | 结尾多一个 `<!-- mivo-merger -->` | Grok 没有 | Pi 文本末尾多这一行（第一轮任务要求） | **无法对齐-需拍板**：100% 对齐就该去掉。请 Praise 二选一 |
| 25 | 状态 issue 心跳（每 30 分钟 PATCH #1027 正文标记段：模式、今日已合、熔断） | round.mjs:230-257、notify.updateStatusSection | 无 | **缺口**：建议 merger.py 出 `heartbeat:#1027:<半小时槽>` 动作，outbound 加 h_status（读正文，只替换标记段，找不到标记就拒绝）。等 outbound 有 handler 后再出这个动作，免得堆 unsupported |
| 26 | 失败重试 / 退避 | backoff 冷却 5 分钟；unknown 冷却 30 分钟；24 小时内 unknown ≥2 或平台不一致 ≥2 → 熔断 | outbound.py:23-29 merger 作业 10→20→40 分钟，封顶 6 小时；merger 遵守 Grok cooldown | **缺口-outbound**：节奏不同，也没有“重复不明就熔断”。建议按 #13 分类后照 Grok 的冷却和计数 |
| 27 | **身份门** G0：令牌 login=PraiseZhu 且 scopes 恰好是 [repo] | gate.mjs:156-158；identity 时 round 只写 PAUSED | gate_port 一致；blockers 终检也含 G0。盒上 gh 的 scopes 是一大串 → 每轮 STOP identity，Pi 线永不合并 | 判定一致。**无法对齐-需凭证**：需要 Praise 给 Pi 线一个只有 `repo` 权限的 PraiseZhu 令牌，并让 outbound 的 gh 调用用它（例如 GH_TOKEN 从 ~/.pi-fallback-secret/env 读）。我没读 Grok 的 secrets/token |
| 28 | 模式门（shadow 只判不写） | canWrite：mode∈canary/on、host、PAUSED、熔断、状态 issue | merger 只有 can_write 且 PAUSED 不在才出动作 | 一致。现在 mode=shadow + PAUSED，两条线都不写 |
| 29 | 平台对照采样（只审计） | round.mjs:188-205 | 无 | 缺（不影响外发）。可以补成只写文件，优先级低 |
| 30 | 产出 | state/audit.jsonl（round/write/circuit…）、audit.log | out/merger/<日>.md、.actions.json、state.json；ledger 在 out/outbound | 格式不同但信息覆盖。差异在 #12/#15 |
| 31 | 线路切换时的状态 | Grok state.json（thread 静置、notices、cooldown） | Pi 读 Grok 的 notices/cooldown/pending，自己记 thread 静置 | 切到 Pi 后静置计时从头算（只会更晚）；Grok 回来时不知道 Pi 发过的通知和合并。建议 outbound 成功后把 notice key 和合并也追加进 Pi 的 merges/notices 文件，由 run.mjs 侧读取（这要改 run.mjs，需 Praise 决定） |

## B. 一键切换
- Grok 侧 routine：**`mivo-merge-sweep`**（:13/:43）。Praise 说自己改，我没动。需要的改法：开头先读 `/workspace/fleet-mode`，是 `pi` 就立刻结束（不调模型、不发消息），是 `grok` 才继续；保持暂停，等 Praise 说恢复再启用。
- `run.mjs round`：现在没有 cron，不用改。如果以后恢复 cron，建议在 cron 行加 `[ "$(cat /workspace/fleet-mode)" = grok ] &&`。
- Pi 侧：merger.py 两种模式下都只写文件。真实外发只经 outbound.py，它自己按 fleet-mode=pi 且 outbound-enabled 门控（outbound.py:264 附近）。切线只改 `/workspace/fleet-mode` 一个文件。

## 本轮改动
- `bin/merger.py`（备份 `merger.py.bak-20261023-1354-v4`）：
  - 额度预留。
  - 照 Grok 处理 pending 合并 / rule-suite。
  - identity / STOP 的熔断动作。
  - stall 通知。
  - HUMAN 文案和标记移植，按 Grok notices 去重。
  - 两条线共用当日额度。
  - 合并动作带 mainHead，blockers 查 main-moved 和 Grok 的 pending。
  - STOP 后结束本轮的判定循环。
- 回放和差分重跑都是 0 差异。outbound.py 没改。PAUSED、闸门、cron 没碰，没有写 GitHub。

## 需要 Praise 的
1. 给 Pi 线一个只有 `repo` 权限的 PraiseZhu 令牌（#27）。
2. outbound 缺口 #11–16、#18、#19、#20、#22、#25、#26 由 outbound 负责人改，或授权我改。
3. 是否保留结尾的 `<!-- mivo-merger -->`（#24）；on 档每日上限要不要放到 12（#18）；轮询频率（#1）。
4. Pi 和 Grok 的通知、合并记录要不要互通（#31）。
