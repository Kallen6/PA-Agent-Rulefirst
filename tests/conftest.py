"""pytest 直跑入口：把仓库根加入 sys.path（与 tests/run_tests.py 等效）。

日常测试仍推荐 `python tests/run_tests.py`（零依赖）；
此文件只为已安装 pytest 的环境提供 `pytest` 命令直跑能力。
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
