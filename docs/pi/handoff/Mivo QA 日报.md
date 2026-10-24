# Mivo QA 日报 · Pi 线迁移验收（2026-10-23 13:10 北京时间）

只写了文件：没有外发，没有开闸，没有改 Grok routine，没有改 /workspace/panoptes，也没有打印密钥。

## 职责 → Pi 脚本
| 职责 | Pi 脚本 | 状态 |
|---|---|---|
| 08:30 日报草稿 + 私信 / 交接 issue 动作 | bin/daily.py（**已重写**）+ conf/daily-calibrations.json（新增） | ✅ 10-22 与 Grok 已发稿结构对齐 |
| 交接仓回执扫描 / 15:20 逾期 | bin/handoff.py | 未改动（原样） |
| 23:30 台账 | bin/ledger.py（调用 build-ledger.mjs --dry-run） | 未改动；状态只有 fixed（复测通过后才算）/ fix_merged_pending_retest / fix_pr_open / concluded / unfixed |
| 09:56 复盘 | bin/dailyretro.py（takeover/dailyretro） | 未改动；只写 out/daily/<日>.retro.md；不补发、不回填 |

写入的文件：`bin/daily.py`（备份在 `bin/daily.py.bak-20261023-1315`）、`conf/daily-calibrations.json`、`out/daily/2026-10-22.render.md`、`out/daily/2026-10-22.diff`、本文件，并在 resume 文件末尾追加了一节。旧的 `out/daily/2026-10-22.md/.json/.actions.json` 保持原样，没有重新生成。

## 25/4 和 24/3 的差异原因
- #434（zhongxingtian-ai）的 mergedAt 是 2026-10-22T15:38:39Z，即北京时间 10-22 23:38，**落在** 10-22 的窗口 [10-21T16:00Z, 10-22T16:00Z) 里。另外，用 gh 按窗口搜索，除 #434 外的 24 个 PR 和 Grok 那份完全一致。所以 Pi 的时区和窗口边界都没错。（Pi 10-22 复盘里说 #434「在 UTC 合并清单之外」，这个说法是错的。）
- 是 Grok 漏了 #434。原因有三：#434 的 QA 报告到北京时间 10-23 00:01 才生成；Grok 写 resume 清单的时间是 10-22 22:40；运维发给日报的「10-22 已测完」清单里也没有 #434（resume/Mivo QA 运维.md:38）。Grok 08:37 那份是按这份冻结的清单写的，run-notes 里写的是「合并 PR（24）」。按规则（数据日取 merged_at 所在的北京日）应该把 #434 算进去，所以**集合层面 Pi 是对的**。
- 不过 #434 唯一的 P2 是 api-probe「按画布列出已挂资产」，status=unverified，原因是重跑结果不完整、无法比较，疑似探针没跟上新契约（resume/Mivo QA 运维.md:40）。它按「待确认」处理更合适。
- 修法：Grok 已经发出当天日报（`sent/<日>/group-content.txt` 存在）时，daily.py 以已发正文为准做对账。窗口里有、但正文里没有的 PR 不会渲染进日报，也不会产生私信，而是记进 `<日>.json` 的 `carry_over`，下次补报。10-22 的 #434 另在 calibrations 的 hold 里写明了原因。
- 同时修了 4 处：
  - #1211 只有 AI 补测的 P2，现在算「待确认线索」：不 @、不私信、不开 issue。
  - P2 开头一句改成「其中 N 个需要修复，M 个只是待确认线索」。
  - 新代码覆盖率改读 `coverage_gate.values.new_code`。原来读的是维度覆盖，所以 #1174 显示成 67%，实际是 87%。
  - 排序改成：非 Praise 的按合并时间在前，Praise 的按编号倒序；P2 里待确认线索放最后。

## 私信一节
新增「已私信通知」。如果 Grok 当天已发，按 `sent/<日>/dm-<PR>.txt` 列：Praise 本人的写「已飞书私信」，其他人写「日报发出后飞书私信」。没有已发记录时，按 notify_via_feishu=true、非 PraiseZhu、非 bot、非待确认线索的条件来规划。10-22 列出的 9 行，PR、收件人、状态都和 Grok 一致。

注意：Grok 10-22 用 `--allow-owner` 私信了朱赞 6 封。Pi 的 actions **永远不生成**给 PraiseZhu 的私信，因为规则是不私信 Praise。这和 10-22 的实际做法不一样，请 Praise 确认按哪个来。

## 外发对账规则（actions.json）
1. 日报 `mivo-daily-report:<日>`：先查 report/slack/sent.json 和 sent/<日>/slack-sent.json，有记录就算 done。
2. 私信 `dm:<日>:<PR>`：feishu-sent.json 里有这个 key，或者有 `<repo>#<PR>:*`，或者 sent/<日>/dm-<PR>.txt 存在，都算 done。同一个问题只发一次；PraiseZhu、bot、待确认线索不发。
3. 交接 issue `handoff:<PR>:<pr_fingerprint>`：查 handoff/<owner>/issues.json 和 sent/<日>/created.tsv。只给路由里有的作者（aj0928/lubinyu-lab → aj0928-handoff，zhongxingtian-ai → handover-mivo）开。覆盖率-only、AI 线索、P3 都不开。
4. 已发正文为准：sent 里没有的 PR 进 carry_over，不补发、不回填。复盘只写文件。
5. 正文以 `<!--`/SHADOW 开头的草稿禁止外发（11:50 事故）。另外 Pi 已在 11:50 发出 `dm:2026-10-22:434`（feishu-sent.json 里有记录），以后不会重发。

## dry-run 结果
命令：`cd /workspace/pi-fallback/bin && python3 daily.py 2026-10-22 --render-only --diff-sent`。只读 gh 查询，写 `.render.md` 和 `.diff`。
- 输出：窗口 25 个，hold 掉 #434 → P1 7 / P2 3 / P3 14，故障 0。
- 和 Grok 已发稿**一致**的部分：开头一句逐字相同；各节标题和个数；24 个 PR 的顺序和分级；@ 的集合（严健 1、钟行天 2、朱赞 6，#1211/P3 不 @）；私信 9 行的 PR、收件人、状态；P3 的三组分法；更正 4 行；全仓 82% 和 #1174 87%；系统状态；分级说明。
- 仍有差异（diff 共 112 行，都是措辞）：
  - Grok 用模型把标题和问题改写成了大白话，Pi 用的是报告原文。这部分无模型模板做不到逐字一致，要么接受，要么改成用 pi-auto 改写。
  - Pi 没有「测试覆盖情况」里那段把场景计数汇总起来的长句。
  - 纯文档 PR 的顺序：Pi 是 #1124、#1190，Grok 是 #1190、#1124。

## 未完成 / 待定
- 09:56 复盘因为额度用尽失败了，需要对数据日 10-22 重跑（只写文件，不补发）。
- #434：等运维判断 api-probe 是不是旧探针。如果是误报，以后的报告不写它；如果属实，下次日报补报，并按 P2 @钟行天（私信已经发过，不重发）。
- #1196 的 P1 撤回私信（给 zhongxingtian-ai）要等运维重新校准。handover-mivo #3 保持打开。
- 从 resume 带过来的：3 天逾期通报要不要只含 Praise 自己的 PR；逾期脚本是否认 Slack 记录；#926/#927/#893 的上报；#1132 的负责人；REPORT-STYLE 的提交；覆盖率-only P2 要不要开交接；#978 补私信。
- 10-23 已对 handoff.py、ledger.py、dailyretro.py 做过 E2E（见 e2e-2026-10-23.md）。这次没改它们，也没重跑。
- 开闸、给 Praise 发私信都需要 Praise 批准。

---
## 第二轮（13:15 北京时间）：handoff.py / ledger.py / dailyretro.py 迁移 + 补跑复盘
这一轮仍然只写文件：没有发群，没有私信，没有写 gh，没有开闸，也没有改 Grok routine 或 panoptes。改动前的原文件都备份成了 `bin/<名>.py.bak-20261023-1325`。

### 各脚本与 Grok 线的一致性
| 脚本 | 移植内容 | 跑的命令（只读 / 只渲染） | 和 Grok 对比 |
|---|---|---|---|
| handoff.py | 按 handoff-routes.json 两个仓路由（aj0928/lubinyu-lab→aj0928-handoff，zhongxingtian-ai→handover-mivo，cc 规则照搬）。新增 `plan <数据日>`，一条问题对应一个 issue，key 用 pr_fingerprint。去重查三处：issues.json、sent/*/created.tsv、gh issue 正文里的 `mivo-qa-handoff: key=` 标记。作者用例按 PR+用例 id 去重；用例 failed 但未确认的，以及 unverified 的检查项，都算待确认，不开 issue。覆盖率类、AI 线索、P3、pre_existing、flaky、qa-error 都不开。Grok 当天已发日报时，没写进日报的 PR 列为 carry_over，不补开。回执扫描（已修 #N / 不修 / 误报）和 3 天逾期动作沿用原逻辑。 | `handoff.py plan 2026-10-22 --render-only --diff-sent`；`handoff.py scan --render-only` | ✅ 计划开 4 个 issue，和 Grok 的 created.tsv 完全相同（handover-mivo #2 #3、aj0928-handoff #6 #7，都标为已开）。#434 列为 carry_over。 |
| ledger.py | 直接调用 Grok 原工具 `build-ledger.mjs --dry-run <日>`。它只写 scratch/ledger-dry，只读 gh，状态沿用原工具：fixed 要复测通过才算，另有 fix_merged_pending_retest / fix_pr_open / concluded / open（即 unfixed）。跑完逐条和 ledger/daily/<日>.json 对比。 | `ledger.py 2026-10-22` | ✅ 共 78 条。concluded 13、open 8，两边一致。有 2 条不同：#1143、#1146 Grok 记 fix_pr_open，Pi 记 fix_merged_pending_retest。原因是时间差：Grok 的台账 15:38Z 生成，修复 PR #1169 在 16:42Z、#1173 在 17:18Z 才合并。Pi 是现在跑的，所以对。 |
| dailyretro.py | 对照已发日报做复盘：只读，绝不重发、补发或回填，不记录任何外发动作。加了 `--force` 用于手动补跑。 | `dailyretro.py 2026-10-22 --force`（pi-auto work） | ✅ 已补跑。 |

产出：`out/handoff/plan-2026-10-22.render.md`、`out/handoff/2026-10-23.render.md`、`out/ledger/2026-10-22.{json,md,diff.md}`、**`out/daily/2026-10-22.retro.md`**（补跑的 09:56 复盘；旧版改名为 .prev-*）。

### 补跑复盘的要点（已人工核实标注）
- 核对结果：级别、@、私信、覆盖率、更正这几项都和报告一致。
- 漏写：#1124 有 8 条作者交接用例，日报里少了「有交接说明一行」。
- 措辞：#1174 的覆盖率 87% 应写「差一点」，而不是「偏低」。
- **已核实**：feishu-sent.json 里数据日 10-22 只有 Pi 在 11:50 写的 4 个 key（dm:2026-10-22:1196/1156/1124/434）。Grok 08:35 和 08:38 发的 9 封私信没有写进 feishu-sent.json，只留在 sent/2026-10-22/dm-*.txt 和 run-notes 里。这就是 11:50 重复私信的根因之一：Pi 的去重当时只查了 feishu-sent.json。现在 daily.py 会同时查 dm-*.txt。outbound.py 的 h_feishu_dm 本来就会查 dm_files，这部分我没改，因为 outbound.py 是共享文件。
- 交给运维核实：
  - 7 个 P1 的报告里 code_review.status 写的是 "failed"，但审查结论齐全，需要确认这个字段的含义。
  - #434 的 api-probe 问题：探针是不是旧的，重跑为什么没跑完（incomplete）。
- 规则建议：
  - 私信统一写进 feishu-sent.json 并回读 message_id。
  - 合并太晚、赶不上当天日报的 PR 一律 carry_over。
  - 覆盖率离门槛不到 5 个点时写「差一点」。

### 第二轮新增的待办 / 阻塞
- Grok 开的 issue 没回写本地状态：handover-mivo #2 #3、aj0928-handoff #6 #7 都不在 handoff/*/issues.json 里。Pi 去重已经把 created.tsv 和 gh 标记一起算上，但 Grok 线的 issues.json 需要有人补，我没有写 Grok 线的文件。
- 回执：aj #7 回了「误报」（issue 还开着）；zxt #3 回了「误报」，但这条按要求保持打开，等运维重新校准，撤回私信也在等；zxt #1 回了「已修 #1196」，要复测通过才能算 fixed。
- 正式模式下的 actions：ledger 和 handoff plan 这次只渲染，没有写 actions.json。正式运行会按上面的去重规则生成 would-do，同样不会自动发出。

---
## 第三轮（14:00）
- 机制对齐表和一键切换都写在 `Mivo QA 日报.parity.md`。
- 已回填 issues.json（4 条 + #434 那条）和 feishu-sent.json（9 封私信）。
- 新增发送记录脚本：report/lib/jsonlog.mjs、send-dm-recorded.mjs、open-handoff-issue.mjs，用 mock 验证过。
- 新增 Pi 线 followup.py。
- 更正：上一轮误改了 panoptes 里的 send-feishu-dm.mjs（scripts/ 是指向 panoptes 的软链接），已恢复，git status 干净。
