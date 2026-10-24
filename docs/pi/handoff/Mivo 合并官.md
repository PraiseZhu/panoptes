# Mivo 合并官 · Pi 线交接（2026-10-23 12:30 北京时间）

## 职责 → Pi 线
| 职责 | 脚本 | 时间(北京) | 产出 |
|---|---|---|---|
| 判定可合 PR（零模型）+ 每轮最多 1 个合并动作 + 阻塞评论动作 | bin/merger.py | cron :13/:43（已有，未改 cron） | out/merger/<日>.md、.actions.json |
| 今日合并 P0/缺报告巡检 | bin/merger.py 末节 | 同上 | 同上 |
| 与 Grok 判定回放 | bin/merger_replay.py（离线） | 手动 | out/merger/replay-2026-10-23.md |

## 写了哪些文件
- `/workspace/pi-fallback/bin/merger.py` 重写（旧版留 `merger.py.bak-20261023-12xx`）。门：非草稿；有 review:merge-ready；base=main；MERGEABLE+CLEAN（BEHIND=WAIT）；8 项必需检查逐项 SUCCESS（取 head 上每个名字最新一次，不看总状态；清单读 policy.json）；讨论全解决；无 CHANGES_REQUESTED（reviewDecision）；conventional 标题；head 为 release/*、plugin/green、main 或改到 scripts/ops/、scripts/loops/automerge/、agent-use/（外加 policy.json humanOnlyPaths）→ HUMAN。判定：MERGE/WAIT/BLOCK/HUMAN。
- PAUSED 在 → 只写 .md，不写任何动作。不在 → `merge:<PR>:<headSHA>`（每轮 ≤1，取号最小）+ BLOCK/HUMAN 的 `comment:<PR>:<reasonHash>`（中文，结尾 `<!-- mivo-merger -->`）。
- `blockers(p)` 保留给 outbound.py 复核用，现在等于“judge≠MERGE 就拒”。
- `bin/merger_replay.py`、`out/merger/replay-2026-10-23.md`。
- 跑过：16 个单测用例全过；`python3 merger.py` 实跑一次（只读 gh），12:25 结果：候选 0，PAUSED，0 条动作。

## 一致性回放
- A. audit.log 判定行（10-16，27 行，展开为 165 个 PR×时间）：**165/165 一致**。（初版 2 条不一致是解析器把“#1005/#1006 已手动合并”误当成 blocked，已修。）
- B. state/audit.jsonl 877 轮（=round-*.log）：MERGE 1039、HUMAN/human-only-path 241（按 evidence 路径复判全是 HUMAN）、WAIT/not-clean 56（按 mergeStateStatus 复判全是 WAIT）→ 全一致。
- 不可比（Grok app 有、规格里没有的门）：need-artifact 156、settling 63（10 分钟沉淀）、review-not-clean 31、review-provenance STOP 3、review-head-mismatch 1、main-not-green / skill-sha-quiet / maintenance-window（merger.py 只做了 06:00–07:15 维护窗口）。所以这几种情况下 merger.py 可能比 Grok 早一轮或更宽松地判 MERGE。
- 注意：历史日志没存原始 PR 状态，回放用的是日志里记下的事实，只能证明判定规则一致，不能证明取数也一致。

## outbound.py 合并路径安全检查（没改代码）
- ✅ :234 只用 `--squash` 且带 `--match-head-commit <sha>`；没有 --admin/--auto/-d，没有 force-push，不改设置。
- ✅ :231 do() 前查 PAUSED；:232 合并前用 merger.blockers 复核；:222-227 已合=done，head 变了=stale；:276 ledger done 不重做；:264 闸 = fleet-mode=pi 且 outbound-enabled。
- ⚠ :270-301 **每轮没有合并上限**：会扫 --days 2 内所有 actions.json，多个 merge key 会在同一轮都合。建议每轮只做 1 个 gh.pr.merge。也没有每日上限（policy 写的是 canary 2 / on 12）。
- ⚠ :244 + :26-31 clean_body 会删掉所有 `<!-- -->`，所以 `<!-- mivo-merger -->` 发不出去，只剩 `<!-- pi-line key -->`。和 Grok 线的标记对不上。
- ⚠ :300 failed 每 10 分钟重试，没有退避和次数上限。
- ⚠ :302 ledger 在最后才写；中途崩了靠 state() 查 MERGED 兜底（合并能兜住，评论靠标记兜住）。
- ⚠ :219-235 复核没有 settling/main-not-green/provenance 这些门（同上面的不可比项）。
- ℹ 旧 merger 在 PAUSED 时仍写了 `merge:1222:4c67226…`（今日 actions.json）；#1222 已人工合并，开闸后 state() 会判 done，不会重合。没删这条记录。
- ℹ :208-214 fixpr 会 push 新分支（不在合并路径，不 force）。

## 待定问题
1. 是否把 Grok app 的额外门（settling 10 分钟、main-not-green、need-artifact、review-provenance）补进 merger.py？要补需要读 Actions artifact 和 main CI（只读）。
2. outbound 每轮 1 个合并的上限、`<!-- mivo-merger -->` 标记被删，这两条要 outbound.py 负责人（Jasper）改。
3. 阻塞评论在 Grok 线是“PAUSED 时不评论、暂时性的不评论”，merger.py 只给 BLOCK/HUMAN 出评论；开闸前要不要评论，由 Praise 定。
4. 不需要新凭证。PAUSED、闸门、cron 都没动。

---
## 10-23 12:40 追加（第二轮，只改本地文件）
### merger.py（备份 merger.py.bak-20261023-1227-v2）
- 按 run.mjs（lib/gate.mjs）补了 4 道门，缺数据一律不判 MERGE：
  - main-not-green（G9）：main 最新 ci.yml push run 必须 completed+success，且 plugin/green 等于 main head。
  - review-provenance（checkReviewRun / checkReviewResult）：review-pr 最新 check 的 app=15368；external_id 指向本仓 actions run；run 是 review-pr.yml、workflow_dispatch、由 PraiseZhu 触发、在 main 上、标题为“review-pr #N”。结论包的 pr/head/runUrl/checkRun 必须对得上，否则判 STOP。
  - need-artifact：读 `review-pr-<N>` 产物里的 result.json。读失败判 WAIT；不存在或过期判 HUMAN（review-artifact-missing）；reviewClean 不成立判 HUMAN（review-not-clean）；base 不对判 WAIT。
  - settling：settleFrom 取以下时间的最大值：review check 完成、PraiseZhu 在 head 上的报告 review、打 ready 标签、ready_for_review、各检查完成、threadsStableSince（记在 out/merger/state.json，第一次见到就从现在起算）。不满 10 分钟判 WAIT。
- 每轮选 settleFrom 最早的那个，相同时取 PR 号小的（与 run.mjs 一致）。
- PAUSED 时不出合并动作，也不出评论动作；STOP 也会生成评论（不在 PAUSED 时）。
- 已用真实 #1222、#1227 只读验证：来源校验通过，result.json 读到了（reviewClean=true）。15 个单测用例全过。
### 回放（out/merger/replay-2026-10-23.md）
- A. audit.log：165/165 一致。
- B. jsonl：MERGE 1039、human-only 241、need-artifact 156、全局 main-not-green 89、settling 63、not-clean 56、review-not-clean 31、review-provenance 3、review-head-mismatch 1，**全部一致**。
- 还不可比：全局 skill-sha-quiet 68（没建模；按“宁晚不早”应补，或开闸前 Praise 确认可以不要）；maintenance-window 45（merger.py 在 main() 里有，judge 里没有）。
- run.mjs 还有、merger.py 仍没有的门：hold 标签、canary、启用时刻、skip-ci 标记、ghost.json 敏感字段、base 变更、其它检查（C2）、报告后人类评论、stale-merge-base、config-drift、日预算（日预算已在 outbound 做）。
### outbound.py（只改合并相关；备份 outbound.py.bak-20261023-1229-merger，ledger 备份 ledger.json.bak-20261023-1229-merger）
- :21、:315-316、:323：每轮最多 1 个合并（尝试就算一次），每天最多 2 个（ledger 里今天 by=pi-line 的 merge done）。
- :20、:259：评论体经 clean_body 后把 `<!-- mivo-merger -->` 补回去。
- :23-29、:330：merger 作业失败后退避 10→20→40…分钟，封顶 6 小时，ledger 记 fails。
- :286 及之后：merger 作业每做完一个动作（done/SENT/FAILED）立刻写 ledger。
- :22、:294：ledger 里 status=skipped 也算终态，不再重做。
- 验证：
  - dry-run 前后对比（含 slack.post、feishu.dm、gh.issue.create、gh.pr.create、grok-only、unsupported 各类型），只有 merge:1222 从 done(ledger) 变成 skipped(ledger)，其余逐行相同。
  - 离线模拟（假 handler，不联网）：R1 合 1 个，round-cap 拦下其余；R2 合第 2 个；R3 daily-cap 拦下；失败后显示 backoff 10 分钟，再失败变 20 分钟；评论保留了标记。
### ledger
- `merge:1222:4c67226…` 改为 status=skipped，reason=“已被人工合并：PraiseZhu 2026-10-23 10:13 北京时间（merge commit 6bdda9e），Pi 线不再执行”（原值 done/target-state 记在 prev_status）。
### 仍待定
1. skill-sha-quiet 要不要补，以及上面列的其余 run.mjs 门。
2. 日上限 2 用的是 canary 档；正式开启（on=12）由 Praise 定。
3. 第一轮里“outbound 合并上限、标记被删需 Jasper 改”那条已由本轮代改，请 Jasper 复核 outbound.py 的 diff。

---
## 10-23 12:45 追加（第三轮：完整移植 run.mjs 的门）
### 做法
- 新增 `bin/gate_port.py`：逐行移植 `automerge/lib/gate.mjs`，是纯函数。
  - 全局门 G0–G10：身份、模式、熔断、维护窗口、skill-sha-quiet、前置心跳、规则漂移、main-not-green、日预算。
  - PR 门 P1–C7：范围、release head、hold 标签、canary、启用时刻、作者、人工路径、ghost.json、skip-ci、review-pr 来源与结论包、报告 review、CHANGES_REQUESTED、base 变更、必需检查、其它检查、not-clean、thread、报告后人类评论、stale-merge-base、静置、need-artifact。
  - 维护窗口现在在判定函数里（evaluate_global）。
- `bin/merger.py` 重写（备份 `merger.py.bak-20261023-1236-v3`）：
  - 取数照 collect.mjs，全部只读 gh api；轮次逻辑照 round.mjs：全局门 → 预筛 → 非 CLEAN 判 not-clean → 两段取结论包 → 按 settleFrom 选 1 个 → 有 STOP 本轮不合。
  - thread 静置计时照 state.mjs（指纹 + head），存 out/merger/state.json。
  - Pi 线额外的门（只会让合并更晚）：conventional 标题、Pi 每日上限 2、遵守 Grok 线 cooldown。
  - 只有 Grok 线自己能写时才记动作：mode 是 canary/on、主机绑定一致、熔断关、状态 issue=1027，并且 PAUSED 不在。所以现在（PAUSED，mode=shadow）**不记合并，也不记评论**。评论只针对 HUMAN，每个 (PR, head, 原因) 一条，同 deliverNotice。
  - `blockers()` 给 outbound 复核用：重新取全局状态，重新深采这个 PR，再判一次，同时查可写、PAUSED、head、cooldown。
- 差分测试 `bin/gate_diff.py` + `test/gate_fuzz.mjs`：用真实 testkit 生成快照，同一份快照分别交给真实 gate.mjs 和 gate_port.py 判。20 个种子 × 5000 例 = **10 万例，0 差异**，覆盖 59 种 verdict:reason。
- 实跑（12:37）：Grok mode=shadow，PAUSED 在，全局判 **STOP identity**，本轮不判 PR，0 条动作。原因：盒上 gh 令牌的 scopes 是一大串，不是 policy 要求的只有 `repo`。用 repo-only 身份模拟时，其余全局门全部通过；#1222、#1227 深采没有读不全的字段，判定合理（not-clean / human-only-path）。
### 回放（out/merger/replay-2026-10-23.md）
- A. audit.log：**165/165**。
- B. state/audit.jsonl 877 轮：逐条比 verdict+reason，并比 settleFrom 和命中的人工路径规则，共 2467 项，**0 不一致**。覆盖全局 main-not-green 89、skill-sha-quiet 68、maintenance-window 45（用真实时间判，也核了其余轮次的真实时间都不在窗口内），以及 PR 级全部 9 种原因。
- 中途有 4 条 settling 对不上，原因是 round 行的 `at` 写在轮末，比判定时钟晚约 10–30 秒。改用同一轮 platform-sample 的 `at`（=g.now）后为 0；671 轮有采样，206 轮没有采样，用轮末时间。
- skill-sha-quiet 的回放只能证明“满足条件时判 WAIT”。日志里没有记 MIVO_REVIEW_SKILL_SHA 的更新时间，所以每一轮的条件本身是按 Grok 的结论设的。
### outbound
- 没再改。dry-run 和第二轮逐行相同，模拟 3 轮正常。日上限仍是 2。
### 待定（需要 Praise）
1. **身份门**：Pi 线用盒上的 gh（PraiseZhu，全权限），按 Grok 规则会一直 STOP identity，Pi 线永远不会合并。要真合，需要给 Pi 线一个只有 `repo` 权限的 PraiseZhu 令牌（我没读 Grok 的 secrets/token）。
2. **模式**：Grok 线 MIVO_AUTOMERGE_MODE=shadow。按对齐要求，Pi 线也只判不写，要改成 canary/on 才会出合并动作（canary 还需要 automerge:canary 标签，并且由维护者打）。
3. **STOP 熔断**：Grok 线遇到 STOP 会给状态 issue 打熔断标签。Pi 线只在报告里记、本轮不合，不做 gh 写。开闸前要不要补这个动作，请 Praise 定。
