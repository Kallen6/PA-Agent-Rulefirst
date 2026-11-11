"""re_pa_agent 启动入口。

用法：
    python run.py --check     # 列出已注册闸门
    python run.py --demo      # 在空帧上运行已注册闸门
    python run.py --web       # 启动本地网页决策台（默认 127.0.0.1:8788）
"""
from pa_engine.main import main

if __name__ == "__main__":
    raise SystemExit(main())
