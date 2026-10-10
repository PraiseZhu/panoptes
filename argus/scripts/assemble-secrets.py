#!/usr/bin/env python3
"""在 Mini 上生成 secrets.env（0600）。

模型网关两行从 stdin 读入（ARGUS_LLM_BASE_URL / ARGUS_LLM_API_KEY），
飞书 Doki 凭证从 Doki 项目的 .env 读，主人 open_id 从 Doki 成员缓存按姓名查。
全程不打印任何凭证值。

用法：printf 'ARGUS_LLM_BASE_URL=...\nARGUS_LLM_API_KEY=...\n' | python3 scripts/assemble-secrets.py <doki目录> <输出文件> <主人姓名>
"""
import json
import os
import re
import sys


def parse_env(text):
    out = {}
    for line in text.splitlines():
        m = re.match(r"\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$", line)
        if m:
            out[m.group(1)] = m.group(2).strip().strip("\"'")
    return out


def main():
    doki_dir, out_path, owner_name = sys.argv[1], sys.argv[2], sys.argv[3]
    llm = parse_env(sys.stdin.read())
    for key in ("ARGUS_LLM_BASE_URL", "ARGUS_LLM_API_KEY"):
        if not llm.get(key):
            sys.exit(f"stdin 缺少 {key}")
    with open(os.path.join(doki_dir, ".env"), encoding="utf-8") as f:
        doki = parse_env(f.read())
    for key in ("FEISHU_APP_ID", "FEISHU_APP_SECRET"):
        if not doki.get(key):
            sys.exit(f"Doki .env 缺少 {key}")
    with open(os.path.join(doki_dir, "data/members_cache.json"), encoding="utf-8") as f:
        cache = json.load(f)
    ids = [k for k, v in cache.items() if v == owner_name]
    if len(ids) != 1:
        sys.exit(f"成员缓存里「{owner_name}」匹配到 {len(ids)} 个 open_id，期望 1 个")
    lines = [
        f"ARGUS_LLM_BASE_URL={llm['ARGUS_LLM_BASE_URL']}",
        f"ARGUS_LLM_API_KEY={llm['ARGUS_LLM_API_KEY']}",
        f"ARGUS_FEISHU_APP_ID={doki['FEISHU_APP_ID']}",
        f"ARGUS_FEISHU_APP_SECRET={doki['FEISHU_APP_SECRET']}",
        f"ARGUS_FEISHU_OPEN_ID={ids[0]}",
    ]
    tmp = out_path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, out_path)
    os.chmod(out_path, 0o600)
    print("已写入 secrets.env：" + "、".join(line.split("=", 1)[0] for line in lines))


if __name__ == "__main__":
    main()
