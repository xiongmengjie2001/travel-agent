"""FastAPI 应用入口：应用装配，不含任何业务逻辑。

对应 docs/tasks/implementation.md 的 T10。装配内容：

- CORS —— 前端 Vite 开发服务器跨域访问后端；
- 路由挂载 —— `api/routes.py`；
- 全局异常 handler —— CLAUDE.md 要求「统一在 api/ 层捕获并转换为 HTTP 响应」，
  转换规则见 docs/schemas/api-response.md §3 错误码表、§4 异常体系、§5 状态码策略；
- 启动事件 —— 初始化 SQLite 表（`MemoryStore` 的建表脚本）；
- 日志 —— 标准库 `logging`，级别取 `LOG_LEVEL`（CLAUDE.md 禁止 `print` 调试）；
- 配置加载 —— `load_dotenv()`，把项目根目录的 `.env` 注入进程环境
  （规则见 docs/module-sdd/config.md）。真实环境变量优先于 `.env`。

`api/` 层不写 try/except，业务异常沿调用栈冒泡到本文件的 handler —— 这是
「统一在 api/ 层转换」的落点，也是错误码表唯一被实现的地方。

启动方式：

    uvicorn main:app --reload          # 或 python main.py

host / port 取自 `.env` 的 `API_HOST` / `API_PORT`。
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.routes import get_memory_store, get_scheduler, router
from src.exceptions import AgentError, InvalidRequestError
from src.models import ApiResponse

# 配置加载的唯一入口（见 docs/module-sdd/config.md）。
#
# 位置约束：必须早于本文件任何 `os.getenv()` 的**执行** —— 下面第一处是
# `logging.basicConfig(level=os.getenv(ENV_LOG_LEVEL, ...))`，故放在这里而非
# 文件更靠后的位置。此处所有 import 已完成，而 `api/` 与 `src/` 全部模块的
# `os.getenv` 都发生在函数 / 构造函数体内（无模块级调用），所以放在 import
# 块之后即可覆盖全局。**后续若有模块在导入期读环境变量，必须把本行上移。**
#
# 不覆盖模式（load_dotenv 的默认行为）：真实环境变量优先于 .env，
# 使 CI / 容器注入的配置压过开发机上的本地文件。
load_dotenv()

ENV_CORS_ORIGINS = "CORS_ALLOW_ORIGINS"
ENV_LOG_LEVEL = "LOG_LEVEL"
ENV_API_HOST = "API_HOST"
ENV_API_PORT = "API_PORT"

DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_API_HOST = "127.0.0.1"
DEFAULT_API_PORT = 8000

# 前端 Vite 开发服务器的默认地址。必须**显式列出**来源：allow_origins=["*"] 与
# allow_credentials=True 互斥（浏览器会直接拒绝），且通配会把接口暴露给任意站点。
DEFAULT_CORS_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]

logging.basicConfig(
    level=os.getenv(ENV_LOG_LEVEL, DEFAULT_LOG_LEVEL),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

LOGGER = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """启动事件：初始化 SQLite 表；关闭事件：释放进程内单例。

    建表动作在 `MemoryStore.__init__` 内（执行 `CREATE TABLE IF NOT EXISTS` 脚本），
    而 `MemoryStore` 由 `api/routes.py:get_memory_store()` 以 `lru_cache` 持有，
    故这里「取一次单例」即完成初始化 —— 不在本文件重复写 DDL，避免两处表结构
    漂移。好处是库路径不可写之类的故障在**启动阶段**就抛出并终止进程，
    而不是等第一个请求进来才 500（那时前端只能看到一个无差别的 5000）。

    另一个效果：空库上 `GET /api/trips` 直接返回 `[]`，满足 AC-2，不必依赖
    首次写入顺带建表。

    关闭时清缓存：`lru_cache` 的实例常驻进程，清掉可让「重启」语义在
    `TestClient` 等反复拉起应用的场景下依然成立，避免残留指向已删库文件的实例。
    """
    get_memory_store()
    LOGGER.info("启动完成：SQLite 表已就绪")
    yield
    get_scheduler.cache_clear()
    get_memory_store.cache_clear()


app = FastAPI(
    title="旅行规划智能体",
    version="0.1.0",
    description="基于 OODA 决策循环的旅行规划智能体 API",
    lifespan=lifespan,
)


def _cors_origins() -> list[str]:
    """解析允许的跨域来源。

    Returns:
        环境变量 `CORS_ALLOW_ORIGINS`（逗号分隔）的解析结果；未配置时返回
        `DEFAULT_CORS_ORIGINS`。
    """
    raw = os.getenv(ENV_CORS_ORIGINS, "")
    origins = [item.strip() for item in raw.split(",") if item.strip()]
    return origins or DEFAULT_CORS_ORIGINS


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.exception_handler(AgentError)
async def handle_agent_error(request: Request, exc: AgentError) -> JSONResponse:
    """业务异常 → 统一响应体。

    `code` 与 `http_status` 由异常类自身携带（docs/schemas/api-response.md §3）。
    除 1001（HTTP 400）外，其余业务错误一律返回 HTTP 200 —— 前端只读 `code`
    判定业务结果，不必同时处理 HTTP 层与业务层两套判定。
    """
    LOGGER.info("业务异常 code=%s: %s", exc.code, exc.msg)
    body = ApiResponse.fail(code=exc.code, msg=exc.msg)
    return JSONResponse(status_code=exc.http_status, content=body.model_dump())


@app.exception_handler(RequestValidationError)
async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """框架级校验失败 → 1001 / HTTP 400。

    走到这里的两种情形（`/api/chat` 的请求体由 `Bridge` 自行校验，
    见 api/routes.py 顶部的分歧说明，故不再是主要来源）：

    1. 请求体不是 JSON / Content-Type 不符 —— 在进入端点函数之前就被拒；
    2. 查询参数越界，如 `/api/trips?limit=0` 触发 `Query(ge=...)`。

    FastAPI 默认抛 HTTP 422，此处转换为 api-response.md 规定的 1001 ——
    不转换则错误码会落进兜底的 5000，与契约不符。

    文案固定为「请求参数校验失败」：`exc.errors()` 可能包含请求体原文，
    细节只进日志，不回传响应体。
    """
    LOGGER.info("请求校验失败: %s", exc.errors())
    body = ApiResponse.fail(code=InvalidRequestError.code, msg="请求参数校验失败")
    return JSONResponse(status_code=InvalidRequestError.http_status, content=body.model_dump())


@app.exception_handler(Exception)
async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    """兜底异常 → 5000 / HTTP 500。

    响应体只给通用文案 —— 异常细节（路径、SQL、堆栈）不得外泄；细节进日志。
    """
    LOGGER.exception("未预期异常: %s", exc)
    body = ApiResponse.fail(code=AgentError.code, msg="内部错误")
    return JSONResponse(status_code=AgentError.http_status, content=body.model_dump())


def main() -> None:
    """按环境变量启动 uvicorn（开发入口）。

    以字符串 `"main:app"` 而非 `app` 对象启动：uvicorn 需要能自行导入应用，
    传对象则失去 `--reload` 能力。`reload` 保持关闭 —— 它在 Windows 下会重复
    导入模块，且生产部署应由进程管理器（systemd / supervisor）负责重载。
    """
    uvicorn.run(
        "main:app",
        host=os.getenv(ENV_API_HOST, DEFAULT_API_HOST),
        port=int(os.getenv(ENV_API_PORT, str(DEFAULT_API_PORT))),
    )


if __name__ == "__main__":
    main()
