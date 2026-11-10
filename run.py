"""re_pa_agent 启动入口。

用法：
    python run.py --check     # 列出已注册闸门
    python run.py --demo      # 在空帧上运行已注册闸门
"""
from pa_engine.main import main

if __name__ == "__main__":
    raise SystemExit(main())
