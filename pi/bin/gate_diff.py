#!/usr/bin/env python3
"""Differential test: real gate.mjs (via test/gate_fuzz.mjs) vs gate_port.py on the same snapshots."""
import json, os, subprocess, sys, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import gate_port as G
P = json.load(open("/workspace/mivo-merger/app/scripts/loops/automerge/policy.json"))
seed, n = (sys.argv[1] if len(sys.argv) > 1 else "1"), (sys.argv[2] if len(sys.argv) > 2 else "3000")
out = subprocess.run(["node", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "test", "gate_fuzz.mjs"), seed, n], capture_output=True, text=True, timeout=300)
if out.returncode: print(out.stderr[-800:]); sys.exit(2)
bad = []; seen = collections.Counter(); errs = 0
for line in out.stdout.splitlines():
    c = json.loads(line)
    if "error" in c["out"]: errs += 1; continue   # JS threw (malformed snapshot) -> not comparable
    try: r = G.evaluate(c["g"], c["p"], P); mine = {"verdict": r["verdict"], "reason": r["reasons"][0], "settleFrom": r.get("settleFrom")}
    except Exception as e: mine = {"error": repr(e)[:80]}
    seen[c["out"]["verdict"] + ":" + c["out"]["reason"]] += 1
    if mine != c["out"]: bad.append((c["i"], c["used"], c["out"], mine))
print(f"cases={len(out.stdout.splitlines())} js-throw={errs} diffs={len(bad)} distinct-outcomes={len(seen)}")
for b in bad[:15]: print(" DIFF", b)
print(" outcomes:", ", ".join(f"{k}×{v}" for k, v in sorted(seen.items())))
sys.exit(1 if bad else 0)
