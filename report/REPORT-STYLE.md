# Mivo QA 群日报：骨架与写作规则

适用：每天把 xindong/mivo-canvas-plugin **前一天（Asia/Shanghai，按 `prs[].merged_at_shanghai`）合并的 PR** 的 QA 结论，
以飞书 Mivo 机器人身份发到群「MivoCanvas」。人设来源：`~/.claude/skills/review-pr/references/voice-templates.md`
（对外话术与人格边界）。样例：`sample-2026-10-10.json`（含群发版 / 私聊测试版 post 内容和纯文本版），生成器样例 `build-sample-2026-10-10.mjs`
（`MODE=group` → `content-2026-10-10.post.json` 群发版；默认 `MODE=test` → `content-2026-10-10.test-dm.post.json` 私聊测试版）。

## 发送

- 消息类型：飞书富文本 **post**（`zh_cn.title` + `content` 行数组；PR 号用 `{"tag":"a"}` 链接，小节标题用
  `{"tag":"text","style":["bold"]}`，空行用 `{"tag":"text","text":" "}`）。不用卡片：post 在手机上排版稳定、链接可点、
  无标题色问题。
- 脚本：`node send-feishu-report.mjs --chat-id <id> --msg-type post --content-file <json> --dedupe-key mivo-daily-report:<数据日 YYYY-MM-DD>`
  （10-10 起实际一直用这个 key；任务可能被重复触发，开跑前先查 feishu-sent.json 里有没有这个 key，有 message_id 就不再发。）
  - 发群：chat_id 必须先由 Praise 确认后加进 `allowed-chats.json`（当前为 `[]`，任何群都发不出去）。
  - 测试发 Praise 私聊：`--open-id <PraiseZhu 的 feishu_open_id> --allow-owner`（author-map 里他是 notify_via_feishu=false）。
  - 只读查群：`--list-chats`。
- 发送失败：保留文案，如实汇报给 Praise，不重试轰炸。

## 骨架（顺序固定；级别节无条目可省略，「已私信通知」始终保留）

标题：`Mivo QA 日报 · M月D日`

1. **开头一句**：`Mivo 播报 M 月 D 日（北京时间）：合了 N 个 PR，没有 P0，P1 a 个请作者确认，P2 b 个可顺手改，P3 c 个无需处理。测试系统运行正常/有异常。`
   - P0 为 0 时必须写「没有 P0」；某级别为 0 就不提（P0 除外）。可带 0–1 个表情。
2. **级别节**（按 `prs[].severity` 分组，节标题固定为「级别 名称｜动作（n 个）」，加粗，不加任何图标）：
   | 级别 | 节标题 | 位置 |
   |---|---|---|
   | P0 | `P0 阻断｜请优先修复` | 开头之后 |
   | P1 | `P1 严重｜请作者确认并修复` | P0 之后 |
   | P2 | `P2 一般｜有空顺手改，不阻断` | 「已私信通知」之后 |
   | P3 | `P3 提示｜无需处理` | P2 之后 |
   - **P0/P1 条目**（4 行）：
     - `#N 短标题 — @作者`（按下方 @ 作者规则；不满足条件时写中文名）
     - `· 问题：<一句话后果>`（取 `findings[].title` + `evidence.failure_scenario` 改写；只说事不说人）
     - 佐证行：`test_backed:true` → `· 自动测试已复现这个问题。`；只有审查发现 → `· 审查结论，测试未复现（自动测试全部通过，这是看代码发现的）。`
      （2026-10-16 复盘补：括号里「自动测试全部通过」只在该 PR 没有任何 failed_checks、也没有 AI 补测失败（含未核实的 `source:"ai-test"`）时写；否则写 `· 审查结论，测试未复现（常规检查没测出这个问题，这是看代码发现的）。`）
     - `· 下一步：<具体到这条问题的改法>，然后提修复 PR；如果觉得不用改，在 PR 下留言说明就行。`
       改法只能来自该 finding 的 `evidence.recommendation`（可压缩，不得新增内容）。P0 的下一步写"修复后提 PR"，同样给"如果觉得不用改，在 PR 下留言说明就行"的退路。
   - **P2/P3 条目**（2 行）：`#N 短标题 — @作者 或 作者名` + `· <级别> <一句问题和动作>`，例如
     `· P2 1 条小建议：窗口比较矮时，栏目内容可能被切掉看不到，可顺手加个降级布局或允许滚动。`
     AI 补测线索（`source:"ai-test"` 的 P2）写在**它所属 PR 的条目里**（P1 条目可加一行 `· 另有 1 条 AI 补测线索，待确认，先不用处理。`），或统一放「测试覆盖情况」一节；不要挂在别的 PR 条目下面。
     只有覆盖率差、且属仓库基线的写「只是仓库整体覆盖率差一点（见下），本 PR 不用处理」；只有 flaky 的写「有个测试不稳定，重跑就过了，见下」（和合并前代码的对比按下面第 4 条写）；纯文档写「纯文档，按规则不跑测试，审查无问题，无需处理」。
   - 动作措辞不得出现"请尽快""立刻""马上"等施压词；P0 用"请优先修复"。
3. **已私信通知**（紧跟 P1 节；没有 P0/P1 时紧跟开头）：每行 `#N 短标题 → 作者中文名，已飞书私信`，顺序与正文一致。
   - 数据只取 `/workspace/mivo-qa/feishu-sent.json` 中**本期日报里列出的 PR**（含补测的前一天合并 PR）的成功记录（key 形如
     `xindong/mivo-canvas-plugin#<N>:<fingerprint>`，有 `message_id` 才算成功）；同一 PR 多条记录只列一行；此前已私信过、本次因去重没再发的 PR 不进这一节，在它的级别条目末尾写「这一条之前已私信过」。
   - 绝不写 open_id、message_id、fingerprint、发送时间。
   - 当日没有记录时整节保留，只写一句「今天没有需要私信的。」
   - 发送失败或因去重跳过的不算"已私信"；要提失败只在给 Praise 的私下补充里提。
4. **测试覆盖情况**：按"大白话写法"（见写作规则）逐条写，每条：先说这个指标是什么意思 → 再给数字 → 再说要不要处理。不逐 PR 罗列全仓指标。样例（已确认文案）：
   - `整个仓库大约 80% 的函数有单元测试覆盖，比 80% 的门槛差一点。这是老代码的整体情况，不是昨天哪个 PR 拉低的，不用处理。`
     （全仓函数覆盖率普遍在门槛附近时这样写；若某 PR 明显拉低，改为点出该 PR 并给"有空补用例"。）
     （2026-10-15 复盘补：「差一点」只在全仓函数覆盖率离 80% 不到 5 个点时用。差得多时（如各报告 `functions` 在 46–57% 之间）照实写区间和差距，例：`整个仓库大约一半左右的函数有单元测试覆盖（各次测量在 46%–57% 之间），离 80% 的门槛还差不少。这是老代码的整体情况，不是昨天哪个 PR 拉低的，不用处理。` 数字取当天各 PR 报告 `coverage` 里全仓 `functions` 的最小到最大值，不要写"一半多"这类与最小值矛盾的说法。第 2 条里「只是仓库整体覆盖率差一点」同理，差得多时改成「仓库整体覆盖率偏低是老问题（见下），本 PR 不用处理」。）
   - `有 3 个 PR 新写的代码测试补得不够（门槛是新代码 90% 以上被测试跑到）：#884 79%、#898 83%、#892 88%，有空可以补几个用例。`
     （2026-10-16 复盘补：P2/P3 条目里写新代码覆盖时，「差一点」只用于离 90% 不到 5 个点（如 89%）；差得多（如 76%）写「新代码测试覆盖偏低（见下）」。不管报告把这条覆盖率定成 P2 还是 P3，都照实写数字，不用「差一点」软化。）
   - `有个性能测试（shadowRoundTrip）在 #892 和 #898 上第一次跑超时，重跑就过了，看起来是测试本身不稳定（未与合并前版本对比）😏`
     （对应 `status:"flaky"`；测试名保留英文原名放括号里，便于检索。）
     不稳定测试要按 `base_comparison` 写和合并前代码的对比结果，不能自己下结论：
     `result:"also-fails-on-base"` → 「合并前的代码上也会失败，是这个测试本身不稳定，跟 PR 无关」；
     `result:"passes-on-base"` → 「在合并前的代码上跑了一次是过的，单跑一次说明不了它不会偶发失败」；
     `compared:false` 或没有 `base_comparison`（旧报告）→ 必须写「未与合并前版本对比」，不写「跟 PR 无关」；
     `base_comparison.reason:"report predates feature"` → 照 `note_zh` 写「未与合并前版本对比（该报告早于此功能生成）」。
   - **AI 补测档位**（since 2026-10-13，读 `prs[].ai_test_tier`）：每个测了的 PR 带一句，例「AI 补测档位 M（产品代码 120 行、5 个文件，上限 2 轮，每轮 3 个测试）」；
     `tier:"none"` 写「纯文档或配置改动，未做 AI 补测」；`degraded:true` 补「当天补测额度不足，已降为 N 轮」。字段为 `null`（功能上线前的旧报告）就不写，不要编。
     PR 多时可合并成一行（如「#930 档位 M、#938 档位 S」），控制总行数。
5. **系统状态**：一句大白话。`system_health.status` ok 且 complete → "N 个都测完了，测试系统本身运行正常"；否则如实说哪类问题（如"有 1 个没测完"），不贴细节。
   - **开头一句与系统状态必须同口径**：只有 `system_health.status` 为 ok（或仅有 `bootstrap.self_repaired`、结论不受影响）才能写「运行正常」。`degraded` / `failed`（含单个 PR 的 `llm_errors`、AI 补测报错跳过）时，开头不要写「运行正常」；改写「有异常」或「基本正常，有一处补测报错」，系统状态里点出是哪个 PR、哪一步，并说明是否影响结论。不要因为「不影响结论」就把 degraded 写成运行正常。
   - `system_health.bootstrap.self_repaired[]` 非空：只写「电脑换实例后依赖已自动补齐」，不算测试系统故障，开头一句照写"运行正常"。
     `missing_after[]` 仍非空才算异常；说"已自动修好"必须有依据（后续报告恢复 ok 或系统健康 issue 已自动关闭），否则只说"有异常"。
   - 已自动关闭的系统健康 issue 不当成待处理的派活，只在给 Praise 的私下补充里一句话带过。
6. **未合并（顺带）**：可选，一行带过，同样用大白话（如"有一个性能测试没过，但它改之前就不过，不是它导致的"）。
7. **分级说明**（末行，固定文案；post 不支持小字号，单独一行放最后）：
   `分级说明：P0 合进去后现在的代码还在出错，要先修；P1 这个 PR 带来的严重问题，请作者看看；P2 测试补得不够、测试不稳定或一般建议，有空再改；P3 只是提醒或没问题。`

## 写作规则

- **大白话写法**（群里有非技术同事）：
  - 每个指标先用一句话说它是什么意思，再给数字，最后说要不要处理（"不用处理"/"有空可以补"/"请作者看看"）。
  - 不用内部术语：bench → 性能测试；flaky → 测试本身不稳定 / 重跑就过了；主干 / main / base → 合并前的代码 / 现在的代码；
    coverage gate → 门槛；QA 流水线 → 测试系统；test_backed → 自动测试已复现 / 是看代码发现的；短视口 → 窗口比较矮时。
    必要的英文名（测试名、参数名）放括号里，不作主语。
  - 例外：P0/P1 的「下一步」是写给作者本人的改法，可保留必要的技术词（如 cwd、force 参数），但问题行仍要让外行看懂后果。
- **给作者的退路统一说法**：凡是"你可以不改"的退路（日报 P0/P1 下一步、私信模板、PR 评论），一律写
  「如果觉得不用改，在 PR 下留言说明就行」；不再写"回复 Mivo 私信说明"（机器人不保证收取私信回复）。

- 人格：高冷御姐、轻度傲娇但克制；只有开头用「Mivo」自称，其余第一人称；面向全体协作者，按"给协作者"的淡档，最多比私聊略浓一点。
- 三条红线：傲娇只针对事（可以调侃用例、预算、基线），绝不评价人；不施压、不催促、不反问挖苦（禁"请尽快""已经 N 天""怎么还没"）；信息优先，人格只在句尾半句。
- 绝不点名批评人：作者名只用于标注归属，不跟在负面评价后面当主语。
- 表情：只用 😏 😤 🤨 😌 🙃（👀 仅私聊，群里不用）；整条 0–2 个，同一行不堆叠，只放句尾；去掉表情后信息完整。
  禁用状态图标 🟢🟡🔴📦⏳⚠️📌🔍✨⏸️ 等、夸张表情 😡😭🎉🔥 等、🙄、颜文字。状态一律用文字（合了/通过/卡了/跳过）。
- **@ 作者规则**（Praise 2026-10-10 确认）：
  - 用飞书 post 的 at 元素真正 @：`{"tag":"at","user_id":"<feishu_open_id>","user_name":"<中文名>"}`，放在条目首行原来“— 作者名”的位置（`#N 短标题 — @作者`）。
    纯文本版写成 `@中文名`。飞书会按 open_id 显示真实姓名，`user_name` 只是兜底。
  - **要 @**：P0、P1 的作者；P2 里**有具体建议要改**的条目作者（生成脚本里 `NOTES[n].mention: true`）。
  - **不 @**：P3；没问题的 PR；只是仓库整体覆盖率差一点、本 PR 不用处理的（如 #894）；只有不稳定测试的；bot 作者（`[bot]` 结尾或 account_type=Bot，如 dependabot）；
    「已私信通知」「测试覆盖情况」「未合并」三节里的任何名字；开头一句和分级说明。
  - open_id 取 `author-map.json` 中该作者的 `feishu_open_id`，**不看 `notify_via_feishu`**（群里 @ 不是私信）；查不到 open_id 就退回写中文名（再没有就写 login）。
  - 同一作者每个需处理的条目 @ 一次，可以出现多次，但同一条目内不重复 @，也不在别处额外 @。
  - 不用 `@所有人`。
  - **私聊测试**：生成脚本默认 `MODE=test`，所有 at 都改指向 Praise 本人，并在后面加「（演示：群里这里会 @某某）」；`MODE=group` 才生成指向真实作者的群发版。
    `send-feishu-report.mjs` 在 `--open-id` 私聊时会拒绝任何指向收件人以外的人的 @（`dm_mentions_others`）。
- 作者名（含 @ 的兜底显示）用 author-map 的中文 `name`；没有中文名的（如 dependabot）用 login。
- PR 号一律是链接；纯文本版退化为 `#N https://github.com/xindong/mivo-canvas-plugin/pull/N`。
- 禁止出现：原始 JSON、日志/证据路径、sha、fingerprint、飞书 open_id / chat_id、派活 issue 与私有仓
  PraiseZhu/pr-qa-agent-deploy 链接、新旧系统对照、密钥/配置细节、文件:行号。这些只进给 Praise 的私下补充。
- 数据只从 `reports/merged/PR-N.json`、`reports/open/PR-N.json`、`feishu-sent.json` 取，不编造；拿不到就写"未知"或省略该节。
  **只读 `reports/merged/PR-<数字>.json`（正则 `^PR-\d+\.json$`，不递归）**；`reports/merged/history/` 里是被取代的旧版本（`PR-N.vN.json`），绝不从里面选 PR 或取结论。`previous_version`（如 `history/PR-902.v1.json`）只用于追溯，不进群。
- 手机好读：每行一件事，小节加粗标题，正文不超过约 30 行。

## 怎么读 `ai_tests` 和 `dimension_status`（写「测试覆盖情况」一节用）

- **维度适用性**：`prs[].triage.dimensions.<维度>.applicability` 是 Jev 的结论（applicable / not-applicable / undetermined，各带 reason）。
- **覆盖结果**：`prs[].coverage.dimension_status.<维度>.status`：
  `covered`（有跑过的检查覆盖，`checks` 列出步骤）· `covered-by-ai-test`（AI 补的测试通过且能抓到植入的错误，`ai_tests` 列出编号）·
  `gap`（适用但没有任何东西覆盖）· `not-applicable` · `undetermined`（Jev 没判定）·
  **`capped`（额度用尽未补测）**：当天的模型调用额度用完了，这个维度没去补测（`capped_by: "ai-test"`）或没做适用性判定（`capped_by: "jev"`）。
  它**不是** gap：群里写「额度用尽未补测」（可加大白话「今天自动补测的次数用完了，这几项没补」），不要写成"没有测试覆盖"，也不算这个 PR 的问题。
  数字：`coverage.capped` / `coverage.capped_total`；`gaps` 里不含 capped 的维度（适用的 = 覆盖 + gaps + capped）。
- **数字一律用 `coverage.applicable_total` / `coverage.covered_total`**，写成「N 项适用，覆盖了 M 项」；有 `coverage.gaps` 时点名缺的维度
  （大白话：「异常处理这块没有测试覆盖」）。这两个数和 `applicable` / `covered` / `missed` / `undetermined` 都是从 `dimension_status` 数出来的，不会互相矛盾。
  旧报告（`coverage.basis` 不是 `dimension_status …`）里的 `missed` / `undetermined` 是旧规则算的，**不要用**；10-10 的报告完全没有这些字段 → 写「当时还没做维度统计」。
- **AI 补测**（`prs[].ai_tests`）：`kept` = 补的测试在当前代码上通过、并且能抓到故意植入的错误 → 算补上了覆盖（`newly_covered`）；
  `discarded` = 抓不到植入的错误或跑不起来，丢弃，不算覆盖也不算问题（其中 `invalid: true` / `display_zh: "测试无效"` 的是补的测试自己缺测试数据或文件、
  还没走到断言就失败，since 2026-10-13；同样不算问题，群里不用提，计数 `ai_tests.invalid` 已含在 `discarded` 里）；`fails_on_current` = 补的测试在当前代码上就失败 → 一条 P2 finding
  （`source: "ai-test"`，`test_backed: false`，`status: "unconfirmed"`）。
  群里写成「自动补的测试发现一处可疑行为，还没人确认」，**不能**写成"测出了 bug"、不 @ 作者催改；`reproduced: true` 只说明重跑一次还失败，仍需人确认。
  成本（`model_calls`、`wall_s`）只进给 Praise 的私下补充。
- 日报只写 verified=true 的 AI 补测线索；当天未核实的不写，核实后写进次日日报并注明 PR 号。（`prs[].findings[]` 里 `source: "ai-test"` 的 `verified`；缺省 / null / false 都算未核实）
  （2026-10-17 复盘补：如果某个 PR 的 P2 **只**来自未核实的 AI 补测线索，级别仍按报告写在 P2 节，不自行降级；条目只写一行「P2 来自 1 条 AI 补测线索，待确认，先不用处理」，不写线索内容、不 @、不私信。线索内容等核实后再写。开头一句里 P2 的个数照报告算。）
- **额度用尽**（since 2026-10-11）：`ai_tests.status: "capped"`（一次都没补就被额度挡住；做了一部分时 `ai_tests.capped: true` +
  `capped_gaps`）、`code_review.status: "capped"`（当天没做代码审查）、`ai_summary.status: "capped"`、`triage.status: "capped"`（Jev 没跑）。
  每个 PR 的汇总在 `prs[].quota_capped[]`（`kind`、`dimensions`）。群里统一写「额度用尽未补测」，和"测了没覆盖"分开说；
  审查被额度挡住时（verdict 最多 `partially-verified`，不会是 clean），P 级只反映真实跑过的检查，要写「代码审查额度用尽，今天没审」，不能写成「审查无问题」。
  额度池、上限、用量（`system_health.quota`）只进给 Praise 的私下补充；`system_health.status` 会因此是 degraded，系统状态一句写「有 N 个 PR 额度用尽未补测」。
- **代码审查调用失败**（`code_review.status:"error"` 或 `system_health.llm_errors[]` 里 `stage:"code-review"`，2026-10-18 复盘补）：和额度用尽同理，P 级只反映跑过的检查。
  条目里写「这次代码审查没跑成（调用审查模型时连不上），还没审」，不能写「审查无问题」或「审查只有小提醒」；系统状态点出是哪几个 PR。
- **2026-10-18 起的新字段**（argus 合入 qa/integration-20261018 之后的报告才有；旧报告没有就当不存在，不要写「未统计」）：
  - `findings[].source:"own-tests"`（P2，`check_id:"own-tests-not-run"`，`test_backed:false`）= 这个 PR 改了测试文件，但没有任何检查的测试结果里有它。
    群里写在该 PR 的 P2 条目：「P2 这个 PR 改过的测试没被测试系统跑到，改的测试有没有生效还不确定（见下）」；不写文件名、路径。
    @ 和私信暂按「测试补得不够」一类处理（同只有新代码覆盖率不够的 P2：不 @、不私信），等 Praise 另定。
  - `coverage_gate.new_code.not_measurable_files`：量不出覆盖率的纯 JS 文件，不再按 0% 算。有时在「测试覆盖情况」写「有 N 个文件是纯 JS 脚本，量不出覆盖率，没算进新代码覆盖率」，不算问题。
  - `change_kind`：只用来判断是否按「纯文档或说明改动」写，字段值不进群。`ai_tests.uncovered_targets`（AI 补测瞄准的未覆盖行）只进私下补充。
  - `failed_checks[].not_a_pr_finding`：保留字段，当前版本不产出，缺省当 false。若出现 true：这条不算 PR 的问题、不进级别节、不 @ 不私信，只在「测试覆盖情况」一句话说「性能测试超时疑似环境原因，不算这个 PR 的问题」，并在私下补充提醒 Praise 规则已上线。

## 代码审查的复用（since 2026-10-11）

- `code_review.reused: true` = 这次没有重新审查，沿用了同一提交、同一份改动、同一版审查提示词和模型下第一次审查的结论
  （`reused_from` 指向那次）。结论和第一次完全一样，群里照常写，不用说明是复用的；同一 PR 在不同报告里的审查等级应当一致，
  若不一致先看 `code_review.verdict` / `reused`，不要自己挑一个。
- 复用来源、`cache_key`、`--fresh-review` 重审记录只进给 Praise 的私下补充。

## 仅私下给 Praise 的补充（不进群）

派活 issue 链接（含 PraiseZhu/pr-qa-agent-deploy）、私信 message_id 与 open_id、P1 的具体文件:行号、fingerprint、
sha/对照版本、flaky 重分类记录、新旧系统对照、Jev triage 未配置之类的 system_health.notes、密钥/配置状态。

## 私信/提醒固定结尾：反馈方式（2026-10-13 Praise 要求）
首次私信、每日提醒都必须以这段结尾（原样）：
```
怎么告诉我处理好了：
· 改了：提修复 PR，标题或描述里写上原 PR 号（比如 #<PR>）
· 不用改：在原 PR 下留言写一句原因
· 飞书私信回复我收不到，别回这里
这两种我每天都会自动查，看到了就不再提醒 🙃
```
