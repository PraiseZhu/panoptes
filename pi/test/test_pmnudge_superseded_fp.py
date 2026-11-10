"""python3 test/test_pmnudge_superseded_fp.py — 11-10：「已被取代」按问题指纹比，不按 QA 单号。
真实数据（gh pr view -R xindong/mivo-canvas-plugin --json number,headRefName,body 只读，11-10 取；正文只留 QA 行与 pi-line 标记行，原样）：
  #2056 OPEN   qa-fix/fixpr-2012-mq1-d966f7350abf35ef12aa      QA #2012 指纹 mq1-d966f7350abf35ef12aa
  #2063 MERGED qa-fix/fixpr-2012-mq1-3a05907ee9f7afb0b17c-a2   QA #2012 指纹 mq1-3a05907ee9f7afb0b17c
修前：11-10 09:05 白天催办写了「范围外：#2056 被已合并的 #2063 取代」（同单号不同指纹，误判）。"""
import os, sys, tempfile, importlib.util
os.environ.setdefault("PI_STATE", tempfile.mkdtemp())
B = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"); sys.path.insert(0, B)
s = importlib.util.spec_from_file_location("pmnudge", f"{B}/pmnudge.py"); M = importlib.util.module_from_spec(s); s.loader.exec_module(M)

PR2056 = {"number": 2056, "headRefName": "qa-fix/fixpr-2012-mq1-d966f7350abf35ef12aa",
          "body": "QA: #2012\n\n- 关联 Issue / 需求：QA 报告 PR-2012 P2，指纹 mq1-d966f7350abf35ef12aa\n\n"
                  "mq1-d966f7350abf35ef12aa\n<!-- pi-line fixpr:2012:mq1-d966f7350abf35ef12aa -->\n"}
PR2063 = {"number": 2063, "headRefName": "qa-fix/fixpr-2012-mq1-3a05907ee9f7afb0b17c-a2",
          "body": "QA: #2012\n\n- 关联 Issue / 需求：QA 报告 PR-2012 P2，指纹 mq1-3a05907ee9f7afb0b17c\n\n"
                  "mq1-3a05907ee9f7afb0b17c\n<!-- pi-line fixpr:2012:mq1-3a05907ee9f7afb0b17c:a2 -->\n"}
M.gh = lambda a: [PR2063] if "merged" in a else []
fixed = M.merged_refs()

# 1) #2056 与 #2063 同单号 #2012、不同指纹 -> 不算取代（修前误判为取代）
assert not M.superseded(PR2056, [2012], fixed), "#2056 (mq1-d966f735) 不应被 #2063 (mq1-3a05907e) 取代"

# 2) 同指纹仍判取代：open 侧是 #2063 同一指纹的另一次尝试（合成：#2063 分支去掉 -a2、标记去掉 :a2；已合并侧 #2063 为真实数据）
SAME = {"number": 2099, "headRefName": "qa-fix/fixpr-2012-mq1-3a05907ee9f7afb0b17c",
        "body": "QA: #2012\n<!-- pi-line fixpr:2012:mq1-3a05907ee9f7afb0b17c -->\n"}
assert M.superseded(SAME, [2012], fixed)
assert not M.superseded(PR2063, [2012], fixed)                 # 自己不算取代自己

# 3) open PR 没有指纹 -> 保守，不判取代（照催）
assert not M.superseded({"number": 2100, "headRefName": "fix/qa-2012-x", "body": "QA: #2012\n"}, [2012], fixed)
print("ok test_pmnudge_superseded_fp")
