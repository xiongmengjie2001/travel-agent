"""旅行规划智能体 —— 业务逻辑层。

子模块划分见 docs/system-design.md：
- `models/`   跨模块共享的 Pydantic 模型
- `scheduler/`    主控调度（OODA 循环）
- `reasoning/`    推理引擎
- `tools/`        工具调用中心
- `memory/`       记忆管理
- `bridge/`       前后端桥接

`exceptions.py` 定义全部业务异常，供上述模块共同使用。
"""
