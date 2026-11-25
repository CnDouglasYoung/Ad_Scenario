#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""无 pytest 环境下的自检：逐个跑 tests/ 里的 test_* 函数。"""
import importlib.util, os, sys, traceback
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tests", "test_checksums.py")
spec = importlib.util.spec_from_file_location("t", path)
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
fails = 0
for name in sorted(n for n in dir(mod) if n.startswith("test_")):
    try:
        getattr(mod, name)(); print(f"PASS  {name}")
    except Exception:
        fails += 1; print(f"FAIL  {name}"); traceback.print_exc()
print(f"\n{len([n for n in dir(mod) if n.startswith('test_')]) - fails} passed, {fails} failed")
sys.exit(1 if fails else 0)
