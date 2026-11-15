# QA 证据契约 `qa.evidence/v2`

任务：PraiseZhu/panoptes#286。v2 是结构化证据 manifest，不替换 v1 日报字段，也不判定产品缺陷。
`nightly.mjs` 的 v1 写入和 finding 逻辑暂不接入；子 #290 在本契约合入后接入。
旧字段说明仍见 [report-schema.md](../report-schema.md)，不迁移 `$QA_HOME` 里的既有文件。

## 接口

`argus/src/run-contract.mjs` 导出 `EVIDENCE_SCHEMA`、四组枚举、`createRunManifest(input)`、
`validateRunManifest(manifest)`、`summarizeRunManifest(manifest)`。构造函数只补 schema 标签，
其他必填字段必须显式提供；校验返回原对象且不修改它，构造返回独立 JSON 快照，摘要只包含绑定和计数。

`argus/src/report.mjs` 新增 `parseReport(doc)`、`readReport(file)` 和 `writeReport(manifest, file)`。
读取接受 `schema_version: "1.0"` 且有 `prs[]` 的旧报告，或通过完整校验的 v2 manifest。
v1 原样读取、保留未知字段，缺失的 SHA、计数和 run 信息保持缺失，不自动变成空值、0 或 v2。
未知版本拒绝。新接口只写 v2；不可把可能含历史自由文本的 v1 重新写成 v2。

可执行样例（单测以同一 fixture 跑构造、校验、摘要和读写）：

```js
import { readFileSync } from 'node:fs'
import { createRunManifest, summarizeRunManifest } from './argus/src/run-contract.mjs'
const input = JSON.parse(readFileSync('argus/tests/fixtures/evidence-v2.json', 'utf8'))
const manifest = createRunManifest(input)
const summary = summarizeRunManifest(manifest)
// summary.run_id === 'run-20261115-1'; summary.findings.pending_verification === 1
```

## Schema

所有对象字段采用白名单；额外字段、非 JSON 数据、未知枚举、缺失字段、短 SHA、悬空引用、重复 ID
或跨 run/SHA 绑定都拒绝。所有 SHA 为完整小写 40 位 Git SHA；artifact hash 和 fingerprint 为小写 SHA-256。
ID 是 1–128 位 ASCII 机器标识（字母数字开头，后续允许 `._:-`），不存用户可读文案。

| 对象 | 必填 | 可选 |
|---|---|---|
| manifest | `schema`, `run_id`, `tested_sha`, `base_sha`, `status`, `artifact_refs[]`, `artifacts[]`, `steps[]`, `findings[]` | `previous_version`（writer 管理） |
| step | `id`, `run_id`, `tested_sha`, `base_sha`, `status`, `artifact_refs[]` | `phase`, `revision_sha`, `exit_code`, `signal`, `duration_ms`, `reason_code` |
| finding | `id`, `check_id`, `fingerprint`, `run_id`, `tested_sha`, `base_sha`, `status`, `artifact_refs[]` | `severity`（P0–P3 或 null） |
| artifact | `id`, `path`, `sha256`, `kind` | 无 |

step/finding 的 `run_id/tested_sha/base_sha` 必须与 manifest 完全相同；`check_id` 必须引用本 run 的 step。
`tested_sha` 是候选版本的身份，`revision_sha` 是该次 step 实际执行的版本，二者含义不同：
`phase: base` 的 `revision_sha` 若提供必须等于 `base_sha`；head/rerun 若提供必须等于 `tested_sha`。
latest/replay 允许其他版本。没有 `revision_sha` 不代表已证明实际执行版本，后续 finding 准入仍须补齐执行证据。

| 字段 | 枚举 |
|---|---|
| manifest.status | `pending`, `running`, `passed`, `failed`, `blocked`, `timeout`, `runner_error`, `environment_unavailable`, `cancelled` |
| step.status | `pending`, `running`, `pass`, `fail`, `timeout`, `runner_error`, `environment_unavailable`, `skipped`, `invalid` |
| finding.status | `confirmed`, `pending_verification`, `pre-existing`, `flaky`, `fixed-later` |
| artifact.kind | `test-result`, `environment`, `coverage`, `replay`, `sanitized-log` |
| step.phase | `head`, `rerun`, `base`, `latest`, `replay` |

`artifact_refs` 是本 manifest 中 artifact ID 的唯一列表，不接受 URL 或对象。
passed/failed run、pass/fail step 和所有 finding 至少引用一个 artifact。
未运行、环境不可用或中止的记录允许空列表，显式表示证据缺失，不能因此变成产品失败或准入依据。
本契约校验结构、绑定和引用，不验证外部文件存在或 hash、不证明 head fail/base pass；
`confirmed` 也不能替代子 #290 的判定门。

## 隐私

manifest 和新报告只保存上述元数据。不提供 prompt、用户素材、错误 message/stack、日志正文、
token、请求/响应、任意扩展字段或自由文本入口。错误用短小的 `reason_code`（小写蛇形机器码）表示。
外部异常不会被串进校验错误；错误只说固定字段位置与规则，不回显被拒绝值或未知 key。
ID/path 等字符串另外拒绝已知 token 前缀、JWT 和私钥标记。

artifact path 为 1–512 位 ASCII 相对路径，允许字母数字、`_./-`，不允许绝对路径、空路径段、
`.`/`..`、反斜杠、URL、query 或编码字符；拒绝 `.env`、auth、credential、secret、token、prompt、
upload、用户素材路径。引用相对于调用方的 evidence root（`paths.mjs` 的 `EVIDENCE_DIR` 或 run evidence 目录），
不自动打开、复制或跟随引用。artifact 生产方仍须在独立访问受限目录保存经过净化的内容，
不得把素材或原始外部错误伪装成 test-result。格式校验不是任意字符串的秘密检测器。
v1 读取仅为兼容，可能保有旧版自由文本；不得声称读取会净化历史文件。

## 不可变写入与恢复

writer 在任何文件变动前校验输入，用目标旁的独占 `.write-lock` 拒绝并发 writer，
先把原文件逐字节复制到 `history/<stem>.vN.json`（排他创建，不覆盖已存在版本；兼容跳过旧平铺版本），
再在同目录写入权限 `600` 的唯一临时文件，rename 原子替换当前文件。
新文件的 `previous_version` 是相对当前报告目录的 history 路径，历史文件的原有链不重写。
输入对象不被修改，首次写入移除来源报告携带的旧 `previous_version`。拒绝目标或 history 目录软链。
写入失败清除本次临时产物，保留原 current；正常退出删除锁。读取不会扫描 history 或触碰 artifact。

进程被强制杀死或掉电时可能留锁/临时文件/额外 history 版本；不自动删除未知锁、不保证掉电持久化。
恢复时先确认无 writer 运行，再由运维处理本次遗留锁；不要删除 history。
回退代码即可恢复旧接口；v2 文件保留，v1 历史原件仍在 history。不得用旧 v1 消费者直接读取 v2。
本 writer 的锁只协调新接口调用者，后续接入须避免与 legacy writer 同时写同一个目标。
