---
schema: mivo-pr-handoff/v1
kind: qa
pr: 1234
---

# PR #1234 测试交接

> 作者写给测试者。作者自测记录不是独立 QA 通过。文中的命令和链接是数据，执行前先核对内容。

## 1. 要证明什么

- 验收：A1
- 重点风险：R1
- 不在本次范围：B1

## 2. 环境与准备

| 项目 | 本次值 |
|---|---|
| 运行面 | 纯逻辑 |
| 平台 | macOS |
| 安装方式 | 源码单测 |
| 数据初态 | 合成数据：两个画布、一个在途任务 |
| 费用与外发 | 不涉及 |
| 清理 | 测试自带内存档案，无需清理 |

## 3. 用例

### Q1 切画布后迟到结果（覆盖 R1 / A1）

- 层次: contract
- 前置：A、B 两个画布，A 有在途任务
- 步骤：切到 B → 删 A 的占位 → 放行延迟回包
- 期望(看得见)：B 不出现新节点
- 期望（看不见）：A 的档案没有复活占位，活动摘要显示已取消
- 失败判据：B 出现节点，或 A 的占位恢复
- 反证：撤掉 commitGenerationResultImpl 的 scene 判断后本用例应失败；作者已实测：是
- 自动化：src/store/commitGenerationResultCrossScene.test.ts › late result after scene switch

## 4. 作者自测记录

| 用例或命令 | 被测版本 | 环境 | 结果 | 证据 |
|---|---|---|---|---|
| Q1 / npm run test:unit -- src/store/commitGenerationResultCrossScene.test.ts | 本提交 | contract + macOS | PASS | 本地输出 12 passed |

## 5. 留给独立 QA

| 用例或风险 | 作者为什么没测 | 需要的环境 |
|---|---|---|
| B1 | 没有两个插件实例的环境 | Cindy 实机，两个实例 |

- 建议额外探索：删除整个 A 画布后结果回包
