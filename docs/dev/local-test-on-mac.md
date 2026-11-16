# 在 Mac 上本地跑 panoptes 测试

`scripts/dev/local-test.sh` 一次跑完 Pi 测试（`pi/test/test_*.py`）和 Argus 测试（`argus/tests/*.test.mjs`），再把失败分成两类：

- **已知环境失败**：Mac 环境本来就跑不过、原因已查清的测试，登记在 `scripts/dev/known-env-failures.json`。不算失败。
- **意外失败**：清单外的失败。有一条，退出码就是 1。

审查和 e2e 只需要看「意外失败」那一节，不用每轮重新确认同一批环境问题。

本脚本只给本地用。Linux CI（`.github/workflows/ci.yml`）不跑它，CI 的测试命令和判定不变。脚本不跳过任何测试，只做归类。

## 前提

- `python3.13` 在 PATH 里（`brew install python@3.13` 或 `pyenv install 3.13`）。系统自带的 python3 是 3.9，解析不了 Pi 的代码。
- node ≥ 24。
- `argus/node_modules` 不存在时，脚本先跑 `npm ci`（同 CI，不下载浏览器）。不想联网就加 `--no-install`。

## 用法

```sh
scripts/dev/local-test.sh                     # 全部
scripts/dev/local-test.sh --only pi           # 只跑 Pi；--only argus 只跑 Argus
scripts/dev/local-test.sh boxhealth sandbox   # 只跑路径里含这些字的测试文件
scripts/dev/local-test.sh --jobs 1 --timeout 120 --keep
```

| 选项 | 默认 | 含义 |
|---|---|---|
| `--jobs N` | 4 | 同时跑几个测试文件 |
| `--timeout SEC` | 300 | 单个测试文件的时限。超时的文件整组进程被停掉，记为「超时」，接着跑下一个 |
| `--no-install` | | 缺 `argus/node_modules` 也不跑 `npm ci` |
| `--keep` | | 成功时也保留日志目录 |

退出码：`0` 没有意外失败和意外超时；`1` 有意外失败或意外超时；`2` 准备阶段出错（缺 python3.13、清单格式错、选项错）。

脚本固定了这些环境，结果不随调用它的 shell 变化：

- `python3` 和 `python` 指向 python3.13。
- 环境变量用白名单（同 `pi/bin/selftest.sh`），测试看不到你的 token。
- `TMPDIR` 是 `/private/tmp/panoptes-localtest.XXXXXX` 下的物理路径，没有空格，也不经过 `/tmp` 软链接。每个测试文件有自己的临时目录和 Pi 状态目录。
- 没有可用的 bubblewrap 时，Argus 测试按 CI 的退路跑：`ARGUS_SANDBOX=none ARGUS_ALLOW_UNSANDBOXED=1`。

## 看输出

```
== 本地测试汇总（darwin） ==
测试文件 249 个：通过 227，失败 22，超时 0
已知环境失败 98 条（在清单里，不算失败）
超时 0 个（单文件超过 300s，已停掉并接着跑下一个）
清单过期 0 条（已不再失败；提醒，不算失败，请从清单删掉）
意外失败 0
```

（2026-11-16 在 Mac 上对 main 跑的结果。）

- **意外失败**：逐条列出用例和日志路径。先在 main 上跑同一条确认是不是本 PR 引入的。
- **清单过期**：清单里的用例这次跑过了（修好了，或测试文件已删除）。只提醒，不影响退出码；请把条目删掉。
- **没跑到**：被筛掉或所在文件超时的清单条目，不算过期。

失败时日志和 `summary.json` 留在输出最后一行写的目录里，每个测试文件一份日志。

## 清单怎么维护

`scripts/dev/known-env-failures.json` 的每一条：

```json
{"id": "pi/test_janitor.py::TestRemote.test_16_rollback_hold_keeps_remote_branch", "platforms": ["darwin"],
 "reason": "macOS 没有 /proc（janitor 用 os.listdir(\"/proc\") 判断进程是否还在）", "tracking": "环境限制"}
```

- `id`：`pi/<文件>::<类>.<方法>`，或 `argus/tests/<文件>::<测试名>`。只写文件（不带 `::`）表示整个文件的失败都算，只在文件没有用例级输出时用（如脚本式测试）。用例级条目不会盖住同一文件里新出现的失败。
- `platforms`：只在这些平台生效（`darwin` 是 macOS）。
- `reason`：查清的原因，写到别人能复核的程度（缺什么命令、读哪个文件、哪一行）。
- `tracking`：上游 issue（`PraiseZhu/panoptes#<号>`），或 `环境限制`。

加条目的条件，三条都要满足：

1. main 上在 Mac 同样失败（切到 main 跑 `scripts/dev/local-test.sh <文件名>`）。
2. Linux CI 上通过。
3. 原因已查清，写进 `reason`。查不清的不要加，按意外失败处理。

不把清单外的失败归为环境问题。测试修好后脚本会报「清单过期」，届时删掉条目。改完清单跑 `node --test scripts/dev/local-test.test.mjs`，最后一条测试检查清单格式和文件是否存在。

## 目前清单里的几类原因（2026-11-16）

| 原因 | 涉及的测试 |
|---|---|
| macOS 没有 `/proc`（`/proc/meminfo`、`/proc/<pid>`、`/proc/self/status`） | Pi：prfix_unit、prfix_parallel、prfix_allow_protected、janitor、cindy_guard、mergewatch_resume、memguard、boxhealth_cleanup、pi_test_isolation；Argus：pr-kinds、umask-pin |
| macOS 没有 `flock` 命令 | Pi：catchup_hourly_gate；Argus：catch-up-lock-log、gh-app-token-after-slot-wait、qa-slot、qa-slot-wait |
| 仓库路径含空格（`Project Panoptes`），测试里路径没加引号 | Pi：gh_app_fixes；Argus：catch-up-sla-queue、qa-slot、qa-slot-wait |
| GNU 专用命令选项（`date -d`、`mv -T`） | Pi：boxhealth_catchup、gh_restore |
| macOS 的 `/bin/bash` 是 3.2（不支持 `declare -A`） | Argus：sweep-tmp |
| macOS 上 `multiprocessing` 默认用 spawn | Pi：rollback_hold |

仓库路径没有空格的机器上，路径类条目会报「清单过期」，这是预期的提醒，不影响结果。
