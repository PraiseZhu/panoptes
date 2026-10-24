# Pi 线完整迁移 · 舰队任务书（2026-10-23 12:25 北京时间，Jasper 代 Praise 下发）

## 目标（Praise 原话要点）
除了 Grok Bot 这一层的调度器，舰队所有链路都要能一键切到 Pi agent 线（真实外发 Slack/飞书/GitHub 也算），切完要端到端测通才算完成。每个 bot 负责把**自己的职责**迁好、测通、写清验收证据。Jasper 只做统筹和独立验收，不替你们做。

## 现状
- 总开关 `/workspace/fleet-mode` = `pi`；切换脚本 `/workspace/pi-fallback/bin/switch.sh pi|grok [--with-outbound] [--dry-run]`。
- 外发闸**关着**（`/workspace/pi-fallback/outbound-enabled.off-1151`），Pi 线只写文件。个人飞书私信另有锁：`outbound-disabled-types` 含 `feishu.dm`。
- 代码 `/workspace/pi-fallback/bin/`，产出 `/workspace/pi-fallback/out/`，cron `/etc/cron.d/pi-fallback`，覆盖表和缺口见同目录 `README.md`，E2E 记录 `e2e-2026-10-23.md`。
- 模型只经 `~/tools/pi-call/pi-auto <tier>`；密钥在 `~/.pi-fallback-secret/env`（600，绝不进 git/聊天）。
- 已接管：Cindy 保活（takeover/cindy）、电脑体检（takeover/boxhealth）。

## 10-23 11:50 事故教训（必须遵守）
Pi 首轮真发重复了：一份带 SHADOW DRAFT 标记的 10-22 日报（已删）、3 条重复飞书私信。根因是没和 Grok 线已发记录对账。
1. 任何外发前，逐条和 Grok 线真实已发记录对账（如 `/workspace/mivo-qa/report/sent/<日>/`、Slack 历史、gh 现状），并检查草稿标记。
2. 两条线不能同时干同一件事：Pi 线在跑时，不要恢复你在 Grok 上的定时任务。
3. 开闸（真实外发）只有 Praise 能批，由 Jasper 统一操作；你们不要自己 mv 闸门文件。
4. 改 `/workspace/pi-fallback` 共享文件前先读最新版、只改自己的作业脚本，改前留 .bak，避免互相覆盖。
5. 安全规则照旧：不提交密钥、Cindy DB/Obsidian 只读、破坏性 git 操作先问 Praise。

## 每个 bot 要交付
1. 列出你的全部职责，逐条对应 Pi 线脚本/cron；缺的补上（只写文件模式）。
2. 用 `--dry-run` 或只写文件跑一遍，和你在 Grok 线的真实产出对比，差异要解释或修掉。
3. 外发类动作写进 actions.json，带幂等 key 和对账依据。
4. 写验收记录到 `/workspace/fleet-standards/pi-line/handoff/<你的名字>.md`：职责表、脚本、跑的命令、产出路径、和 Grok 对比结果、未解决问题、需要 Praise 提供的东西（如凭证）。
5. 做完回复 Jasper 一句话 + 文件路径。

## 第二阶段（13:55，Praise 原话："保证 2 条链路可以快速切换 且 pi agent 的所有链路机制和原先链路的机制要一致…保证 2 条链路的功能机制100% 对齐"）
每个 bot 交付两件事：
A. **机制对齐表** `handoff/<名字>.parity.md`：你在 Grok 线的每一个机制逐行列出（触发方式/时间、输入数据、判定规则与阈值、去重与幂等、产出格式与位置、外发渠道与对象、失败重试/退避、上限、人工闸门），对应 Pi 线的实现和证据（脚本行号、dry-run 输出、同一天数据的对比结果）。每行标 一致 / 已修 / 无法对齐（原因+需要什么）。目标是除"需要 Praise 提供凭证或拍板"之外全部一致。
B. **一键切换**：把你在 Grok 上的每个 routine 的开头改成先读 `/workspace/fleet-mode`：值是 `pi` 就立刻结束（不调模型、不发消息），值是 `grok` 才干活。Pi 线脚本已经反过来按同一个文件门控。改好后这些 routine 可以保持启用，切线只需要改这一个文件，不用再逐个暂停/恢复。暂时仍保持暂停状态，等 Praise 说恢复再启用。写明你改了哪些 routine。
规则不变：只写文件、不开闸、不发消息、不碰别人的脚本。
