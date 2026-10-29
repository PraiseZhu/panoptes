"""疑似回滚外发暂停（10-28）：/workspace/box-ops/ROLLBACK-SUSPECTED 存在时（grokbot-box/sync.sh rollback_suspect() 写），
本机状态可能是旧快照，外发会重复或发错。outage 补跑跳过外发型作业、outbound 只出计划不发；标记删掉后自动恢复。
路径可用 PI_ROLLBACK_MARK 覆盖（测试用）。每次调用都重新看文件，不缓存。"""
import os

DEFAULT_MARK = "/workspace/box-ops/ROLLBACK-SUSPECTED"


def mark_path(): return os.environ.get("PI_ROLLBACK_MARK") or DEFAULT_MARK


def active(): return os.path.exists(mark_path())
