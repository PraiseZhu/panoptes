# Mivo QA 运维 · Pi 线迁移验收（2026-10-23 12:30 北京时间）

只写文件；没开任何闸门，没恢复也没新建 Grok 定时任务，没开 issue，没往 mivo-canvas-plugin 推任何东西，没跑重型 QA。

## 职责表
| 职责（Grok 线） | Pi 线 | 时间（北京） | tier | 产出 |
|---|---|---|---|---|
| mivo-qa-on-merge（事件触发合并即测） | 不迁：由 panoptes catch-up cron 兜底 | 偶数小时 :07 | 无 | reports/merged/PR-N.json |
| panoptes-env-guard（每小时自检＋修复） | bin/opsenv.py（已重写） | 每小时 :25 | 无 | out/ops/env-<日>.md，有失败时另写 env-<日>.alerts.json |
| panoptes-daily-review 10:57（复盘＋P1 校准） | bin/opsreview.py（已扩展） | **11:15**（原来是 10:15） | review（luna） | out/ops/review-<数据日>.md/.json、review-<数据日>.post.md、out/ops/<数据日>.actions.json |
| 停摆补测 / 手动重跑 / 校准落盘 | bin/opsfix.py（新写）backfill / calibrate | 手动 | 无 | reports/merged/PR-N.json、history/PR-N.vK.json、manual-runs.jsonl、.bak-<日期>-calibration |

旧版留了备份：bin/opsenv.py.bak-20261023-1224、bin/opsreview.py.bak-20261023-1224、pi-fallback.cron.bak-20261023-1225、/etc/cron.d/.pi-fallback.bak-20261023-1225（文件名带点，cron 不会加载）。

## a) catch-up：不依赖 Grok，延迟能接受
- 已装、已启用：重置后 /etc/cron.d/panoptes-catch-up 存在，cron 守护进程在跑。12:07 那一轮正在测 #1222 和 #1223（pid 103576/104824），说明它确实在工作。
- 不依赖 Grok：整个流程是 cron → run-nightly.sh --catch-up → gh 加本地测试。在 panoptes 里 grep，没有 Grok API 或 routine 调用（llm-models.json 里只有模型目录）。AI 补测和代码审查走的是 panoptes 自己的模型网关，不经过 Grok Bot。
- 时间表：`7 */2 * * *`。盒子现在是 Asia/Shanghai 时区，所以实际在**北京偶数小时 :07** 触发。README 里写的“奇数小时”是盒子还在洛杉矶时区时的说法，已经不对了。原来的 opsreview 10:15 正好排在 10:07 catch-up 后面，所以挪到了 11:15。p2 12:20/18:20 和 prfix 14:20 也落在 catch-up 之后（prfix 遇到 panoptes 在跑会自己跳过），请 Jasper 统一看一下。
- 最坏延迟（关掉 on-merge 之后）：等下一轮最多 2 小时，再加测试本身的时间（中位 16 分钟，最长约 32 分钟，按 113 份报告算）。单个 PR 最坏约 **2.5 小时**。合并扎堆时要串行排锁，比如 12 个 PR 会再多出 3–6 小时（10-21 的实例：晚了 164 和 178 分钟）。扫描范围是昨天加今天，跨午夜不会漏。**结论：** 日报 08:35 只看前一天，所以对日报没影响，可以接受。但要注意：合并扎堆的那天，当天就看到结果要晚好几个小时。
- ⚠ 注意：catch-up 这条 cron 自带 `ARGUS_ALLOW_ISSUES=1 --create-issues`，会**绕开 Pi 线外发闸自己去开 GitHub issue**（条件是有测试佐证的 P0/P1）。Grok 线本来也是这样，我没有改，因为这是 panoptes 仓库自己管的。要不要改成 --no-issues，请 Praise 定。

## b) opsenv.py
- 检查项：bootstrap-box.sh --check（chromium、bubblewrap、node/npm、venv、catch-up cron 加守护进程、apt 依赖），另外抽查 chromium、node、npm、venv、cron 文件、cron 守护进程、仓库完整性；还查 panoptes HEAD 和 origin 是否一致（用 ls-remote）、有没有改过的已跟踪文件、gh 登录、secrets.env 是不是 600 权限。
- 修复：要同时满足三条才会执行 /workspace/panoptes/bootstrap-box.sh——`/workspace/pi-fallback/ops-repair-enabled` 存在、fleet-mode=pi、panoptes 没在跑。修完会再查一遍。**这个开关文件我没建**，所以现在只查不修。
- 修掉的 bug：旧版的 pgrep 会把自己的 shell 也算成“panoptes 运行中”（10-23 日志里有一行是这样）。
- 和 Grok 比：Grok env-guard 在盘上没有记录，没法逐行对比。检查范围按 routine 的描述（chromium/cron/node/npm/venv/catch-up/repo）全部覆盖了。12:24 实跑结果：全部就绪，panoptes 运行中（catch-up）。

## c) opsreview.py
- 流程：用 collector（review-ops/bin/daily_review.py --out）写 out/ops/review-<数据日>.md，格式是 cross-bot-review 的 ①–⑦。① 产出对齐（漏测和触发方式）、② 测试分配/分级、⑦ 审查机日检，这些都由 collector 判断。之后用 review tier 重查当天代码审查的 P0/P1（最多 4 次调用）。群消息只写成 review-<日>.post.md，再加一条 action（type=grok.group，幂等 key `ops-review:<日>`）。outbound.py 没有这个类型的执行器，所以就算开闸也发不出去。
- 现在**从不改** reports/merged/。校准只作为提议附在文末，要落盘得人工确认后跑 `opsfix.py calibrate --apply`（会先留 .bak）。
- 只写文件的实跑：`OPS_CAP=0 python3 bin/opsreview.py 2026-10-22`，collector rc=0，25 个 PR。post 和 actions 都生成了。跑完后把原来的 review-2026-10-22.md/.json 换了回去。
- 和 Grok 的 10-21 版（review-ops/daily/2026-10-21.md）对比：
  - 一致：#1145 P1 是误报。#1144 的 P1 偏高：Pi 说降到 P2，Grok 说倾向 P2/P3，没有校准。
  - **不一致：** #1137。Pi 说误报（生产链路在 conversationGenerateRefs.ts:76 先校验了）；Grok 说成立，而且 #1161 已经修了。这就是校准只提议不自动落盘的原因。
  - collector 对 #1140/#1149/#1161 的触发说法是错的，Grok 主持人手工改过（原因是重启丢了排队，#1161 首测其实是 event）。Pi 版只能列 collector 原话，这类判断还是要人看。
  - Pi 版缺 Grok 版那些手工内容：测试分配逐条审（27 个里 24 个正确）、issue 规则核对、跟进事项。
- 10-22 的 Grok 复盘文件**还没有**（review-ops/daily/2026-10-22.md 不存在；照理是 10-23 10:57 写，盒子重置加 Grok 暂停）。Pi 版 out/ops/review-2026-10-22.md 有 4 条校准提议：#1215→P2、#1196→误报、#1192→P2、#1191→P3。Praise PR 判 #1191 是误报，两边方向一致。

## d) 补测
- `python3 bin/opsfix.py backfill --from 2026-10-22 --to 2026-10-23 --reason "停摆补测" [--pr N] [--execute] [--rerun]`。默认只列出要跑的命令。--execute 会逐个跑 `run-nightly.sh --pr N --date D --write-merged --wait-lock 14400 --no-issues --trigger manual --out /workspace/mivo-qa/reports/merged/PR-N.json`（绝对路径），内存不足 3G 就跳过。原因记到 reports/merged/manual-runs.jsonl。--rerun 会先把旧报告复制成 history/PR-N.vK.json。
- 另一种做法（昨天加今天）：`/workspace/mivo-qa/run-nightly.sh --catch-up --no-issues`。
- 只列命令的实跑（12:25）：缺报告的是 #1221 #1223 #1227。#1223 正在被 catch-up 测，剩下的下一轮 14:07 会自己补上。我**没有 execute**。
- `opsfix.py calibrate --pr N --match "<标题片段>" --to P2|P3|误报 --reason ... --apply`：先备份成 .bak-<日期>-calibration，再改这条问题的级别，并写一条 calibrations 记录。**和 Grok 手工校准的差别：** Grok 还会重算 counts、PR 级 severity/verdict 和审查门禁字段（例如 PR-1145 的 gate failed→passed 加 calibrated_from）。这个工具只改单条问题，会提示 PR 级没有重算，剩下的要人手补，或者以后再补全。

## 未完成 / 待定
1. catch-up 的 `--create-issues` 会绕开外发闸（见 a），要不要改成 --no-issues，请 Praise 定。
2. 修复开关 ops-repair-enabled 没建。启用的话，自检会在 fleet-mode=pi 时自己 sudo 跑 bootstrap（可能装 apt 和 node）。
3. calibrate 不重算 PR 级字段（见 d）。
4. 10-22 复盘的正式版（review-ops/daily/2026-10-22.md）谁来写：Grok 主持人恢复以后写，还是 Pi 版人工核实后升级成正式版？我的 Pi 脚本只写 out/ops/。
5. 复盘里的手工判断（测试分配逐条审、触发原因、issue 规则），Pi 线没有自动化。
6. README 里“panoptes 在奇数小时”的说法要改成偶数小时（README 是共享文件，我没动）。

## 怎么启用（我没启用）
- 两个 cron 已经在 /etc/cron.d/pi-fallback 里：`25 * * * * opsenv`、`15 * * * * opsreview @11`（只写文件）。
- 要让自检真的修复：`touch /workspace/pi-fallback/ops-repair-enabled`，并且 fleet-mode=pi；同时要停掉 Grok 的 panoptes-env-guard，免得两边一起修。
- 群消息：Grok 群没有 API。Praise 定好替代渠道（Slack/飞书）以后，把 action 的 type 改成 slack.post 或 feishu.group，再由 Jasper 开闸。
- Grok 线的 mivo-qa-on-merge、panoptes-env-guard、panoptes-daily-review 在 Pi 线运行期间不要恢复。

## 2026-10-23 14:45 北京 · 任务 1–3 结论（Mivo QA 运维）
只写文件；panoptes 的改动只在本地分支，没推、没合；没开 issue；reports/merged/ 下的报告一个都没改。

### 1) 10-22 的 7 份 P1 报告 code_review.status=failed
- 涉及 #1156 #1174 #1176 #1191 #1192 #1196 #1215。它们都是 status=failed，gate={failed, blockingFindings:1, problems:["code-review-status-failed"]}，summary、findings、verdict 齐全，模型用量也正常。审查跑完了，只是发现了 P1。
- 根因（panoptes）：
  - argus/src/mingqa.mjs:154-156 `toCodeReviewDoc` 把审查结论写进 doc.status（有未排除的 P0/P1 就写 failed）。
  - nightly.mjs:752 再把 doc.status 原样抄进报告的 `code_review.status`。这个字段在其他情况下表示的是这一步的运行状态（error/capped/skipped/unavailable），同一个字段两种含义。
  - ming-qa 的 qa_agent.py:4401 看到 status=failed，又补一条 `code-review-status-failed`。mingqa.mjs:245 把真正的原因 `blocking-code-review-finding` 过滤掉了，problems 里只剩这条派生项，看起来就像审查失败了。
  - 这不是 10-22 才出现的：10-21 的 #1137/#1144/#1145 也一样。日志里没有异常（nightly-20261022T212342.log 显示 `#1215 done: needs-attention P1`）。
- 修复：本地分支 `fix/code-review-status-semantics`（7a960e8，基于 main b116d4e）。
  - `code_review.status` 改成运行状态 `completed`，结论留在 `verdict.status` / `gate.status`。
  - 新增 `summarizeGate()`：有阻断发现时，不再把派生的 code-review-status-failed 放进 problems；真正的文档缺陷（缺 status、缺 file/evidence）照常列出。
  - docs/report-schema.md 同步更新。
  - 测试 argus/tests/code-review-status.test.mjs 3 条通过；全套 395 条，394 过、1 跳过、0 失败；gitleaks 无泄露。
  - 已切回 main，cron 用的工作树没受影响。
  - 并行方案：`.worktrees/cr-ran`（c8b7bc4）加了 `ran` 字段，status 保持不变。两者选一个。
- 每份报告的字段更正提议（7 份都一样，**只是提议**）：`prs[0].code_review.status: "failed" → "completed"`，`prs[0].code_review.gate.problems: ["code-review-status-failed"] → []`。gate.status、blockingFindings、verdict、severity 都不动，P1 本身的校准另外走（opsreview 提议 #1215→P2、#1196→误报、#1192→P2、#1191→P3）。

### 2) #434 api-probe「按画布列出已挂资产」
- **探针过时了**：mivo main（86dd7d9）server/routes/projects.ts:440-475 里，引用只从节点 payload 现算，`attachAssetRef` 对没有指向该资产的节点只回 `reference-follows-node`，不建引用；`listAttachedAssetIds` 只遍历 payload。旧探针只调了 attach、没在节点里写 `mivo-sasset:<id>`，所以必然拿到 `{"assetIds":[]}`。产品自己的测试 app.assets-gate.test.ts:657-668 断言的就是这个新语义。
- **「重跑没跑完」不成立**：重跑 h2 实际跑完了，83 项里 82 过、1 挂，767 毫秒，日志里有完整的 ✔ 列表（evidence/nightly/2026-10-22/ef4b269a/pr-434/h2/）。首测、重跑、base 三次都正常结束（control.log:8035-8037，base 8be6e16 通过）。之所以标成「rerun result incomplete or failure type not comparable」，是因为 runner.mjs:566 `checkFailureType` 对 api-probe 的「字段…期望…实际」原因返回 `unknown`，decide.mjs:30/70 只承认 assertion/test_error 可比，于是 compareKey 返回 unknown，结果就是 unverified。这是分类器的缺口，不是运行中断。另外，报告文件 mtime 是 04:35，晚于 generated_at 00:01，有人后来写过这个文件，原因我没查到。
- **#434 唯一的 P2 是误报**（PM 的裁决成立）：失败只来自过时的探针，产品行为符合 #434 的设计。审查发现本身是 P3/PLAUSIBLE，说的就是「探针没适配新语义」。PR #18 上的新探针在 main 上全部通过（见 3）。

### 3) panoptes PR #18 验证：**通过**（没合）
- diff：mivo-canvas-plugin.json 加 3 步：在 n2 写一个引用 `mivo-sasset:${accAsset}` 的节点（POST，期望 200/rev1）→ attach 期望 `already-attached` → 原有的 list → detach 之后用 if-match 1 清掉引用（期望 rev2）→ 原有的「摘掉后不再列出」。SASSET_ID_RE（`^mivo-sasset:([0-9a-f]{64})$`）和 payload.asset.url 的抽取路径都核对过了，顺序测试也合理。
- CI：`test` SUCCESS（10-23 09:55 北京）。本地全套 393 条，392 过、1 跳过、0 失败。gitleaks 扫 b116d4e..96065f9 和整个 profiles 目录，都没有泄露。
- 复现（内存 7G、锁空闲，已记入 manual-runs.jsonl，kind=trial，产出在 /workspace/mivo-qa/scratch/pr18-trial/）：`trial-steps.mjs --only api-probe`，对 mivo main 86dd7d9 跑：
  - 旧版（b116d4e）：api-probe **fail**，只挂「按画布列出已挂资产」，和 10-22 h1/h2 的 82/83 一致。
  - 新版（96065f9）：api-probe **pass**，0 个失败。86/86 是按 83+3 推算的，trial 只输出了 pass，单项计数没有逐条看到。
- 小意见（不影响结论）：detach 那一步现在回的是 `reference-follows-node`（因为节点还引用着资产），探针只断言了 200，可以顺手断言 kind；n2 留在画布上，后面的删画布步骤会一起清掉。
