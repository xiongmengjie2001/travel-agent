"""FastAPI 路由层。

CLAUDE.md 规定的职责边界：本层只做**请求校验 / 调用 `src/` / 异常转换**三件事，
不含任何业务逻辑。依赖方向恒为 `api/ → src/` —— `src/` 不得 import 本层，
也不得 import FastAPI 或任何 Web 框架。

- `routes.py`    全部端点定义与 FastAPI 依赖装配
- 应用装配（CORS、日志、全局异常 handler）在仓库根目录的 `main.py`
"""
