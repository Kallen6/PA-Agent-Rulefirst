"""A 类闸门函数库。

import 各领域模块即完成注册（模块内使用 @register）。
新增闸门时：先在此导入模块，再确保 tests/unit 覆盖。
"""
from __future__ import annotations

from pa_engine.gates import (  # noqa: F401
    channel,
    constant,
    entry,
    guard,
    order,
    range,
    reversal,
    risk,
    spike,
    swing,
    wedge,
)
