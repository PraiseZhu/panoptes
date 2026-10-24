# Mivo QA 日报 · Grok ↔ Pi 机制对齐表（2026-10-23 14:00 北京时间）

范围：整个过程只写文件。没有外发、没有开闸、没有改 Grok routine、没有改 panoptes，也没有打印密钥。
- Grok 线 routine：开头读 `/workspace/fleet-mode` 的门控由 Praise 本人加，我没有动 routine。
- Pi 线：由 `bin/run.sh` 和 `lib.mode()` 读同一个文件；真实外发另外还要 `outbound-enabled` 这个闸门文件。
- 状态标记：一致 = 机制相同且有证据；已修 = 这次改过之后才一致；无法对齐 = 写明原因和需要什么。

## 一键切换
| Grok routine（北京时间） | 门控（Praise 正在改） | Pi 等价作业（cron /etc/cron.d/pi-fallback） | 切线 |
|---|---|---|---|
| mivo-qa-daily-report 08:30 | 先读 /workspace/fleet-mode，值为 pi 就立即退出（不调模型、不发消息），值为 grok 才干活；目前仍暂停 | `run.sh daily @08`（08:35）→ daily.py | 只需改 fleet-mode |
| mivo-roster-refresh 每月 1 日、15 日 08:23 | 同上 | `run.sh roster @08`（:23）→ roster.py（只在 1/15 日运行；另需 takeover/roster） | 同上 |
| mivo-qa-daily-retro 09:56 | 同上 | `run.sh dailyretro @09`（:56）→ dailyretro.py（另需 takeover/dailyretro） | 同上 |
| mivo-qa-followup 15:17 | 同上 | **新写 followup.py remind；cron 还没加**（建议 `17 * * * * box $R followup @15`，由 Jasper 统一加） | 加 cron 之后 |
| mivo-qa-followup-scan 06:17 | 同上 | **新写 followup.py scan；cron 还没加**（建议 `17 * * * * box $R followup @06`） | 同上 |
| mivo-qa-ledger 23:30 | 同上 | `run.sh ledger @23`（23:40）→ ledger.py | 只需改 fleet-mode |
| mivo-qa-handoff-scan-1830 | 同上 | `run.sh handoff @18`（18:35）→ handoff.py scan（另在 06:20、15:20 也跑） | 同上 |
| mivo-qa-handoff-scan-0000 | 同上 | `run.sh handoff @00`（00:05）→ handoff.py scan | 同上 |

两线都还在暂停或只写文件，等 Praise 说恢复。Pi 的外发闸门由 Jasper 统一操作（需要 Praise 批准）。

## 机制逐行对齐
| # | 机制 | Grok 线 | Pi 线实现与证据 | 状态 |
|---|---|---|---|---|
| 1 | 数据日与时间窗 | 取前一个北京自然日，按 merged_at 判定 | lib.py:14 utc_window、:34 merged_prs，用 gh 搜索后再精确过滤。10-22 窗口里有 25 个 PR，和 gh 结果一致 | 一致 |
| 2 | 输入 | reports/merged/PR-N.json，schema 1.0 | lib.load_report；缺失、无效或不是 1.0 都按「测试系统故障」处理（daily.py build） | 一致 |
| 3 | 当天发得晚的 PR | Grok 按冻结的清单写日报，10-22 漏了 #434 | daily.py:70-78：如果当天日报已发，就以已发正文为准，没写进去的进 carry_over；同时看 conf/daily-calibrations.json 的 hold。#434 的漏报是 Grok 侧的问题（详见 Mivo QA 日报.md） | 已修 |
| 4 | 分级 | 按报告的 prs[].severity 分；P2 当 P1 处理；只有 AI 补测的 P2，或作者用例失败但未确认的，算「待确认线索」，不 @、不私信 | daily.py classify（:47 lead_only）。P1 7 / P2 3（其中 1 条线索）/ P3 14，逐条和 Grok 一致 | 已修 |
| 5 | @作者 | P0、P1、P2 都 @；P3、待确认线索、bot 不 @ | 10-22 的 @ 名单逐个一致：严健 1、钟行天 2、朱赞 6 | 一致 |
| 6 | 群日报版式 | REPORT-STYLE：开头一句、各级别节、已私信通知、P3 分组、更正、覆盖率、系统状态、分级说明 | `daily.py 2026-10-22 --render-only --diff-sent`：开头一句逐字一致，节标题、顺序、个数都一致；diff 共 112 行，全是标题和问题的白话改写 | 已修；措辞部分无法对齐，因为 Grok 用模型改写，Pi 走模板，要对齐只能接 pi-auto 改写 |
| 7 | 群里不写内部信息 | 不出现交接仓、issue、私有链接、fingerprint、open_id、sha、额度，也不提 Praise PR 的修复 | daily.py 的渲染里不输出这些字段 | 一致 |
| 8 | 更正 / 校准 | 运维校准和 Praise 的裁决写进「补测结果」 | conf/daily-calibrations.json 的 corrections，**要人工维护**（Grok 线由 bot 从运维复盘里读出来） | 无法自动对齐：需要运维把校准落成机器能读的文件 |
| 9 | 私信对象 | 条件：notify_via_feishu=true、不是 PraiseZhu、不是 bot、不是线索；每个问题一次 | daily.py 生成的 actions 里永远不含 PraiseZhu | 有分歧：10-22 Grok 用 --allow-owner 私信了朱赞 6 封，**需要 Praise 拍板** |
| 10 | 私信去重 key | 原来是 `<repo>#<pr>:<fp>` 或干脆不记；10-22 的 9 封都没写进 feishu-sent.json | 统一成 `dm:<数据日>:<PR>`，已回填（见下文）。Grok 线新加 report/send-dm-recorded.mjs，每发一次都在锁内记一次 | 已修 |
| 11 | 交接 issue | 按 handoff-routes 分两个仓；一个问题开一个 issue；不开的情况：只有覆盖率、AI 线索、P3、pre_existing、flaky、qa-error | `handoff.py plan 2026-10-22 --render-only --diff-sent`：4 个 issue 和 created.tsv 完全一致；#434 进 carry_over | 一致 |
| 12 | 交接去重 | key 用 pr_fingerprint；作者用例按 PR+用例 id；10-22 新开的 issue 没回写 issues.json | Pi 去重查三处：issues.json、created.tsv、gh 上的 marker。Grok 线新加 report/open-handoff-issue.mjs，开 issue 后在锁内回写，GitHub 上已有的会补记。已回填 4+1 条 | 已修 |
| 13 | 回执扫描 | 00:00、18:30 两次；识别「结论：已修 #N / 不修 / 误报」 | handoff.py scan（00:05、06:20、15:20、18:35，gh 只读） | 一致（Pi 多扫两次，只读，不影响） |
| 14 | 逾期 | 满 3 天无回执就私信并发群（发群要 Praise 定） | handoff.py 15 点的扫描生成 overdue:* 动作 | 一致；发群的渠道等 Praise 定 |
| 15 | followup 提醒与感谢 | 06:17、15:17 跑 panoptes track-followups.mjs，并写 followup/state.json | 新写 followup.py：跑同一个工具的 --dry-run，状态用副本（MIVO_QA_FOLLOWUP_STATE），线上状态不动（mtime 仍是 04:32）。scan 跑出来：handoff_resolved 2、held 1、errors 0 | 已修（只写文件）；还缺 cron。Pi 线真实执行时需要非 dry-run 模式，等开闸 |
| 16 | 台账 | 23:30 跑 build-ledger.mjs，状态有 fixed（复测通过才算）/ fix_merged_pending_retest / fix_pr_open / concluded / open | ledger.py 跑同一个工具的 --dry-run，再和 ledger/daily 逐条对比。10-22 共 78 条，有 2 条不同，原因是修复 PR 在 Grok 生成台账之后才合并 | 一致 |
| 17 | 复盘 | 09:56，在 Grok 群发复盘；不补发、不回填 | dailyretro.py 用模型写复盘，只写文件；**发 Grok 群无法迁移**（属于 Grok 内部）。10-22 已补跑，见 out/daily/2026-10-22.retro.md | 写文件这部分一致；发群无法对齐：要 Praise 指定一个 Slack 或飞书渠道 |
| 18 | roster | 每月 1 日、15 日跑 refresh-roster.py，更新 author-map | roster.py 只写 out/roster/，不覆盖线上的 author-map | 无法对齐：真实替换需要 Praise 批准 |
| 19 | 失败重试与上限 | routine 失败就人工补；不补发 | run.sh 有 flock，内存 MemAvailable 低于 3G 就跳过；outbound 里状态为 done 的 key 永不重做；复盘失败（比如 10-23 额度用尽）手动用 --force 补跑 | 一致 |
| 20 | 人工闸门 | 只有 Praise 的话算批准；补发前要问 | 外发闸门 outbound-enabled 加 feishu.dm 锁；Pi 不会补发 | 一致 |

## 上一项任务的收尾（回填 + 发送记录）
- 发现并纠正：我上一轮改了 `/workspace/mivo-qa/scripts/send-feishu-dm.mjs`，没注意到 `scripts/` 是指向 /workspace/panoptes/scripts 的软链接。**已从备份恢复成和 HEAD 一模一样，panoptes 的 `git status` 是干净的。**
- 新的 Grok 线脚本都在 /workspace/mivo-qa/report/ 下，是真实目录，不是软链接：
  - `lib/jsonlog.mjs`：用 O_EXCL 锁文件（60 秒算过期），锁内重新读，再写临时文件后 rename；遇到已存在的 key 不覆盖。
  - `send-dm-recorded.mjs`：包一层 panoptes 原来的发送脚本。key 按顺序取 --dedupe-key，否则 `dm:<day>:<pr>`，否则 `dm:adhoc:<sha16>`；检查、发送、记录都在锁里完成。
  - `open-handoff-issue.mjs`：依次查 issues.json 和 GitHub 上的 marker；创建 issue 后在锁内回写；GitHub 上已有的只补记，不重开。
  - **Grok routine 以后要改成调用这两个包装脚本**。routine 由 Praise 改，我没动。
- 回填（改之前都备份成 `.bak-20261023-1330`）：
  - handoff/zhongxingtian-ai/issues.json：补了 #2、#3（来自 created.tsv），还有 #4（Pi 在 11:50 事故时开的 #434 那条，正文没有 marker）。
  - handoff/aj0928/issues.json：补了 #6、#7。
  - feishu-sent.json：补了 6 个 key `dm:2026-10-22:{1215,1198,1192,1191,1176,1174}`，标 by=grok-line，时间按 run-notes 估的，没有 message_id。另外 1124、1156、1196 这 3 个 key 已经有 Pi 在 11:50 的发送记录，我在这 3 条里加了 grok_line_send 字段。
  - 第二次跑回填脚本时，误给我新加的 6 条也写上了 grok_line_send，已经清掉。
- 验证全部用 /tmp/mivo-verify 下的副本，飞书 fetch 和 gh 都是 mock，凭证用假值，secrets 文件没读：
  - 同一个 key 发两次：只真正发 1 次，第二次 skipped。
  - 5 个 key 各并发 2 次：只发 5 次，记录 5 条，没有残留锁。
  - adhoc key：第二次 skipped。
  - 重放 10-22 的 9 个 key：全部 skipped，mock 发送次数保持 7。
  - 开 issue：第一次 create，第二次因为已在 issues.json 而跳过；删掉本地记录再跑，从 GitHub marker 补记，create 总数还是 1；对 #2、#3 的 key 直接跳过。
- Pi 去重**只看 JSON 文件**也能识别：5 个 issue key 全部命中，9 个 dm:2026-10-22:* 全部命中。

## 还没解决 / 要 Praise 定
1. 能不能私信 Praise 本人（第 9 行）。
2. 复盘和逾期通报发到哪个渠道（第 14、17 行）。
3. 措辞要不要接模型改写（第 6 行）。
4. 运维的校准要落成文件（第 8 行）。
5. followup 的两条 cron 由 Jasper 加。
6. Grok routine 改成调用 send-dm-recorded.mjs / open-handoff-issue.mjs。
7. Pi 的 outbound.py 现在直接调 panoptes 的发送脚本，不经过锁。要改它用 send-dm-recorded.mjs，但 outbound.py 是共享文件，我没动。
