"""无 pytest 的测试收集器（保持 pytest 可收集）。

用法：
    python tests/run_tests.py            # 运行 tests/unit 全部 test_ 函数
    python tests/run_tests.py -k parity  # 只跑名称包含 parity 的用例
"""
from __future__ import annotations

import argparse
import importlib
import sys
import traceback
from pathlib import Path
from unittest import SkipTest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

UNIT_DIR = Path(__file__).resolve().parent / "unit"


def iter_test_modules():
    for path in sorted(UNIT_DIR.glob("test_*.py")):
        module_name = f"tests.unit.{path.stem}"
        yield module_name, path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("-k", default="", help="名称过滤（子串）")
    args = parser.parse_args(argv)

    passed = 0
    failed: list[tuple[str, str]] = []
    skipped = 0

    for module_name, path in iter_test_modules():
        if args.k and args.k not in module_name:
            continue
        try:
            module = importlib.import_module(module_name)
        except Exception:
            failed.append((module_name, traceback.format_exc()))
            continue
        for name in sorted(dir(module)):
            if not name.startswith("test_") or not callable(getattr(module, name)):
                continue
            func = getattr(module, name)
            display = f"{module_name}.{name}"
            try:
                func()
            except SkipTest:
                skipped += 1
            except Exception:
                failed.append((display, traceback.format_exc()))
            else:
                passed += 1

    for name, tb in failed:
        print(f"\nFAIL {name}\n{tb}")
    print(f"\nRESULT passed={passed} failed={len(failed)} skipped={skipped}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
