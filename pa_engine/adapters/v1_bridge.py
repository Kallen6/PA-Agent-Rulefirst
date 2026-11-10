"""v1 适配桥：复用 PA_Agent 的数据层与特征层（P0 起使用）。

策略（对应 md/01 重构建议第三节「直接复用清单」）：
- 优先 `import pa_agent`（已安装或 PYTHONPATH 已包含）；
- 否则把同级 `../PA_Agent` 加入 sys.path（开发期免安装）；
- vendoring（复制进 pa_engine/data、pa_engine/features）留到打包阶段再决定。

禁止事项：
- 不得从 v1 导入决策编排（orchestrator）与提示词组装（prompt_assembler）；
  v2 的决策路径必须走 pa_engine.engine / pa_engine.gates。
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

_BRIDGE_FILE = Path(__file__).resolve()
_REPO_ROOT = _BRIDGE_FILE.parents[2]          # re_pa_agent/
_WORKSPACE_ROOT = _BRIDGE_FILE.parents[3]     # paagent/


def _v1_root() -> Path:
    """优先读 `config/settings.json` 的 `v1.path`，默认同级 `../PA_Agent`。"""
    from pa_engine.config.settings import load_settings

    raw = load_settings().v1.path
    if raw:
        path = Path(raw)
        return path if path.is_absolute() else _REPO_ROOT / path
    return _WORKSPACE_ROOT / "PA_Agent"


def _install_pyqt_stub() -> None:
    """v1 的 util 包在 import 时会拉 PyQt6；回放/对照只需要纯函数，装个最小桩。"""
    if "PyQt6" in sys.modules:
        return
    try:
        from PyQt6 import QtCore  # noqa: F401

        return
    except ImportError:
        pass
    pyqt6 = types.ModuleType("PyQt6")
    qtcore = types.ModuleType("PyQt6.QtCore")

    class _QObject:
        def __init__(self, *args, **kwargs) -> None:
            pass

    def _pyqt_signal(*args, **kwargs):
        return object()

    qtcore.QObject = _QObject
    qtcore.pyqtSignal = _pyqt_signal
    pyqt6.QtCore = qtcore
    sys.modules["PyQt6"] = pyqt6
    sys.modules["PyQt6.QtCore"] = qtcore


def _ensure_v1_on_path() -> None:
    _install_pyqt_stub()
    try:
        import pa_agent  # noqa: F401

        return
    except ImportError:
        pass
    root = _v1_root()
    if root.is_dir() and str(root) not in sys.path:
        sys.path.insert(0, str(root))


def v1_available() -> bool:
    _ensure_v1_on_path()
    try:
        import pa_agent  # noqa: F401

        return True
    except ImportError:
        return False


def load_data_layer():
    """返回 v1 data 层关键对象：KlineBar / KlineFrame / build_display_frame。"""
    _ensure_v1_on_path()
    from pa_agent.data.base import KlineBar, KlineFrame  # noqa: F401
    from pa_agent.data.snapshot import build_display_frame

    return {
        "KlineBar": KlineBar,
        "KlineFrame": KlineFrame,
        "build_display_frame": build_display_frame,
    }


def load_feature_layer():
    """返回 v1 特征计算函数：单根几何 + 结构特征。"""
    _ensure_v1_on_path()
    from pa_agent.ai.kline_features import (  # noqa: F401
        bar_candle_direction_label,
        compute_kline_geometry_features,
    )
    from pa_agent.ai.market_features import (  # noqa: F401
        build_program_features_dict,
        compute_simple_market_features,
        render_simple_market_features,
    )

    return {
        "compute_kline_geometry_features": compute_kline_geometry_features,
        "bar_candle_direction_label": bar_candle_direction_label,
        "compute_simple_market_features": compute_simple_market_features,
        "render_simple_market_features": render_simple_market_features,
        "build_program_features_dict": build_program_features_dict,
    }


def load_prefill_layer():
    """返回 v1 方向/AlwaysIn/趋势背景判定（只读，不导入编排层）。"""
    _ensure_v1_on_path()
    from pa_agent.ai.decision_nodes import judge_always_in, judge_direction
    from pa_agent.ai.trend_context import build_trend_context

    return {
        "judge_direction": judge_direction,
        "judge_always_in": judge_always_in,
        "build_trend_context": build_trend_context,
    }


__all__ = ["v1_available", "load_data_layer", "load_feature_layer", "load_prefill_layer"]
