#!/usr/bin/env python3
"""opsfix retest --max-model default is 12 (Praise 11-04). Parses args only via opsfix.ap; runs nothing.
Run: python3 pi/test/test_opsfix_max_model_default.py"""
import os, sys, importlib.util
B = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin")
sys.path.insert(0, B)
spec = importlib.util.spec_from_file_location("opsfix", os.path.join(B, "opsfix.py")); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

def test_max_model_default_is_12():
    a = m.ap.parse_args(["retest"])
    assert a.max_model == 12, a.max_model
    assert m.ap.parse_args(["retest", "--max-model", "5"]).max_model == 5
    assert "[--max-model 12]" in m.__doc__

if __name__ == "__main__":
    test_max_model_default_is_12(); print("ok")
