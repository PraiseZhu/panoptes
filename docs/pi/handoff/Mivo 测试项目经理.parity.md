# 机制对齐表 · Mivo 测试项目经理（2026-10-26 更新，北京时间）

取代 10-23 第二阶段版（旧版写的 `/workspace/pi-fallback/...` 已废弃：`bin/` → `/workspace/panoptes/pi/bin/`，`out/` 和闸门文件 → `/workspace/mivo-qa/pi/`）。
状态：**一致** / **已修**（本 PR 或之前改过）/ **不一致**（写原因）。现状：fleet-mode=pi，外发闸开，Pi 线在跑，Grok 线 routine 读到 pi 即退出。

## 1. 作业对照
| Grok 线（原岗位） | Pi 作业（`/etc/cron.d/pi-fallback`，`run.sh <job>`） | 产出 | 状态 |
|---|---|---|---|
| 10:30 核对 G1–G5，写 pm-daily | `pm @10`（10:35）→ pm.py + pm_extra.py | `out/pm/<数据日>.md/.json`；pi 模式另写 `fleet-standards/pm-daily/<数据日>.md`（已有 Grok 版不覆盖） | 一致 |
| 白天催办 10–20 双数点 | `pmnudge @10,12,14,16,18,20`（:05） | 追加「白天催办」节；`<当天>.nudges.actions.json`；20 点写 `<当天>.chief.md`（给 Jasper） | 一致 |
| P2 裁决 | `p2`（12:20、18:20），只出提议 | `out/p2/` | 不一致：没有 Praise PR 会签，提议要人用 `p2.py record` 入表 |
| 名单 | `roster`（08:23） | roster 产出 | 一致 |
| 群复盘 / 给 Chief 一句 / 催 Praise PR | `pm-retro:` `pm-cos:` `pm-chief:` `nudge:` actions → outbound.py | 按 Praise 10-23 要求改为转 Praise 私信（relay），ledger 记 done | 已修（渠道是转私信，不是原群/agent） |

## 2. 口径
| 机制 | 现在 | 状态 |
|---|---|---|
| G1 | 首份报告（含 history 旧版）合并后 2h 内 / 合并数 | 一致（runbook 口径） |
| G2a | 新报**成立的** P0/P1 24h 内认领 / 同集合 | 已修（本 PR：只取 `finding_policy.author_findings`，pending_verification 不进） |
| G2b | 成立的 P0–P2 有修复 PR 或结论 / 同集合；p2-rulings 判「不修」= 不成立，剔除；待核实条数单列 | 已修（本 PR；删掉「P2 只算修，Praise 未确认」，按 10-23 统一分级） |
| G3 | 日报原文：Grok `report/sent/<日>/group-content.txt`，没有就读 Pi `out/daily/<日>.md`；发送时间取 `report/slack/sent.json` | 已修（本 PR：10-24 「日报合并 None 个」是只读 Grok 路径，pi 模式下文本为空） |
| G3 日报没发 | 无发送记录且次日 `out/logs/daily-<次日>.log` 以 rc≠0 结束 → 记「daily 崩溃」缺口 | 已修（本 PR） |
| G4 / G5 | audit.log bot 合并数；最终提交 review-pr | 一致 |
| 第三轮未上报、交接逾期未提醒 | pm_extra §12「第三轮未报出」、§11「超 72h 未见逾期提醒」进遗漏清单 | 一致（10-24 已报出 #966 #1077、aj0928-handoff#2）；20 点 `chief.md` 也列（`chief_missed.py`，按此刻台账 / gh 复核，已修且复测通过的不列，如 #1077） |
| chief.md 漏项（Praise 10-26） | `pmnudge` 20 点追加 `chief_missed.sections()`：「系统漏跑」（看门狗当天 missed:/failed: + daily 崩溃）、「工作逾期」（pm 遗漏清单的第三轮未报出、交接超 72h）；只列已核实 + 负责人 + 期限 + 证据，待核实来源不列，日报已写的不重复；数据日 pm 没跑就用最近一份 | 已修（本 PR） |
| 判断性内容 | Pi 零模型，只给数字和清单 | 不一致：有意设计 |

## 3. 切换
- 切到 Pi：`switch.sh pi`（或改 `/workspace/fleet-mode` 为 pi）。下一个 :05/:35 起 Pi 写 pm-daily；外发另需 `/workspace/mivo-qa/pi/outbound-enabled`。
- 切回 Grok：`switch.sh grok`。Pi 只写 `out/`，不写 pm-daily。
- 补跑：`run.sh pm <数据日>` 会写 pm-daily 并排 `pm-retro/pm-cos` 外发（闸开时 10 分钟内转私信）。只想看数字用 `PM_OUT_DIR=<目录> pm.py <数据日>`（不写 pm-daily、不排外发）。

## 4. 待 Praise 定（只列，不定）
1. 整机/看门狗停摆监控（哨兵 + GitHub Actions 心跳）：已提出，未批准。
2. P2 模型提议能否不经 Praise PR 会签直接入表。
3. 日检是否加一次模型调用补判断性内容。
4. ~~第三轮/逾期遗漏是否也写进 `chief.md`~~：Praise 10-26 已定，见上表「chief.md 漏项」。
5. 停机错过的 pm（如数据日 10-25）是否允许带外发补跑。
