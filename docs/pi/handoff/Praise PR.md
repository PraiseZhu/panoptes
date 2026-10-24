# Praise PR · Pi 线交接（2026-10-23，北京时间）

只写文件：没开 PR、没推送、没写 GitHub、没改 cron/routine。旧版留了备份 `bin/prretro.py.bak-20261023-1235`、`bin/prfix.py.bak-20261023-1235`。

## 职责 → 脚本
| 职责（Praise-PR.md） | 脚本 | cron（已有，未改） | 产出 |
|---|---|---|---|
| 09:41 复盘 + P0–P2 覆盖核对 | `/workspace/pi-fallback/bin/prretro.py` | 09:45 | `out/praise-pr/<日>.retro.md` |
| 草稿 → ready | prretro.py 第 2 节 | 同上 | `out/praise-pr/<日>.actions.json`（key `ready:<PR>`） |
| 22:23 清理 | prretro.py 第 3 节，**只列不删** | 同上 | retro.md |
| 修 P0/P1/判「修」的 P2 | `/workspace/pi-fallback/bin/prfix.py`（heavy） | 14:20 | `out/praise-pr/<日>-QA<N>-<fp>.patch/.md`，actions `fixpr:<N>:<fp>` |

## prretro.py
- 覆盖：扫 `reports/merged/PR-*.json` 里所有 P0–P2 finding，按以下顺序找对应：PraiseZhu 的 PR 正文 `QA: #N`（任何状态）→ `praise-pr/notes/` 文件名带 N（文件名带 p1/p2 时级别要对上）→ QA 台账最新日的 fix_pr / concluded_reason（别人修的）。都没有就标 ❌。
- 转 ready：只看 open、草稿、PraiseZhu、正文有 `QA: #N` 的 PR；`gh pr checks` 去掉 review-pr 后所有项为 pass/skipping 且不为空才写 `gh pr ready` 动作。ready PR 只记录「不碰」。
- 清理：本地分支对应已合并的修复 PR（按 headRefName），且分支 tip 是合并 head 的祖先、worktree 干净 → 「可清理」；其余「保留」/「待核」。分离 HEAD 的 worktree 只列出来，由人判断。
- 运行：`python3 prretro.py --dry-run`（打印，不写文件）；不加参数则写 retro.md + actions.json。

## prfix.py
- 选题：P0/P1 + p2-rulings.md 里裁决为「修」的 P2；跳过：#923（闸）、已有修复 PR、已有 note、台账已结论、已满 2 轮（第 3 轮转人工）。`--list` 只列候选。
- 门槛：MemAvailable ≥3G、panoptes 不在跑、PATH 以 /usr/local/bin 开头；只用 `timeout` 管自己的子进程，不做全盒 kill。
- 流程：worktree 建在 base → Pi(heavy) 只写失败测试 → 脚本复跑，**必须失败**（否则写 NOREPRO，不修）→ Pi 写最小修复 → 脚本复跑通过 → 撤掉非测试改动后失败 → 恢复后通过 → Pi 按 `docs/pr/_template` 写 `docs/pr/<预测号>/review.md+qa.md`（和修复同一提交）。
- 正式模式：新分支 `fix/qa-<N>-<fp8>` + worktree `/workspace/praise-pr/pi-wt-<N>`，本地 commit（hooks 照跑，不用 --no-verify）；push 和 `gh pr create --draft`（正文 `QA: #N`）只写进 actions.json，由 outbound 闸门决定。不 force、不 push main。
- dry-run：临时 worktree 在 /tmp/pi-prfix（分离 HEAD，不建分支、不 commit、不写 actions），结束后删掉。
- 运行：`python3 prfix.py --list`；`python3 prfix.py --dry-run [--qa N --fp FP --base SHA]`；cron 不带参数。

## dry-run 证据
### prretro（12:3x 跑，`out/praise-pr/2026-10-23.retro-dryrun.txt`）
- 117 条 P0–P2；对不上的 13 条：#1156 P1、#1196 P1（新报告，确实还没修）、#1156 P2、#1124 P2、#1098 P2、#1216 P2×2（AI 测试失败）、性能基准/接口测试类 P2（#1018 #1028 #1029 #907 #928 #434）。
- 转 ready：open 修复 PR 只有 #1166，已 ready → 不碰；0 个动作。
- 可清理：wt-1143(#1169)、fix-1215-missing-at(#1216)、wt-892(#1148)、wt-1146c(#1173)；保留 wt-nodeps、backup/1141-short-copy；分离 HEAD worktree 8 个。
- 对比：任务里说的 `/workspace/praise-pr/review-2026-10-23.md` **盒子上不存在**（全盘查过），所以没法逐行对比 Grok 线。改为和 backlog-progress.md、notes/ 和 gh 现状对照：上面的结果都对得上（#1216 已合并、#1166 已 ready、#1156/#1196 没有修复 PR）。旧 Pi 版用的是 QA 台账，只列出 8 条（漏掉 #1156、#1196 这类台账里还没有的条目，#923/#943 已有 note 却仍列为未修）。
### prfix（QA #1176，base 8ad51b7ef = #1215 的 base）
见下方「prfix 结果」。

## 已知缺口
- p2-rulings.md 目前是空表，所以 prfix 不会选 P2。按 10-21 新规，所有 P2 都要修，这一点和本次交代的「只修判了『修』的 P2」有冲突，需要 Praise 定。
- 本地仓 `git config user.name` = `Praise`，不是交代里说的 PraiseZhu（正式 commit 前要核实 user.email）。
- 预测 PR 号可能撞号（以前出过 1193→1194）：动作里写了说明，需要改目录名再推一次，不能 force。
- 轮数按「正文写了 QA: #N 的 PR 个数」算。#926 那几个覆盖率 PR 也会被算进去，所以显示 3 轮、转人工。
- panoptes 的 worktree（praise-pr/panoptes-wt-*）属于 panoptes 仓，清理那一节没扫。
- npm ci 每题要跑几分钟；盒子根目录的 /node_modules 问题（covtool/nonm.sh）没处理，碰上会表现为测试失败。
- 旧版 prfix 今天 08:59 已为 #923 生成过 patch（`2026-10-23-PR923-*`），违反闸门；新版硬编码跳过 #923。旧 patch 留着没删。
