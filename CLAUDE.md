# CLAUDE.md

本文件为 Claude Code 在本仓库工作时的项目指引。

## 项目概述

一个基于 OODA 决策循环的旅行规划智能体 Web 应用：用户输入出行需求，后端调用大模型完成意图理解、信息检索与行程编排，前端展示并交互式调整行程方案。

## 技术栈

- **后端**：Python 3.11, FastAPI, Pydantic v2, SQLite
- **前端**：Vue 3, Vite, Axios
- **AI**：DeepSeek API（默认模型 `deepseek-v4-flash`，或 Mock 模式）

## 目录结构约定

| 目录 | 职责 |
| --- | --- |
| `src/` | 业务逻辑（OODA 五模块），不含任何框架/路由代码 |
| `api/` | FastAPI 路由层，负责请求校验、调用 `src/`、异常转换 |
| `tests/` | 所有测试文件，文件名 `test_<module>.py` |
| `docs/` | 所有 SDD 设计文档 |
| `frontend/` | Vue3 前端代码 |
| `.claude/` | Claude Code 配置 |

依赖方向固定为 `api/ → src/`，禁止反向依赖；`src/` 不得 import FastAPI 或任何 Web 框架。

## 编码规范

- **Python**：遵循 PEP8，所有函数必须带 type hints，单个函数不超过 50 行
- **模型通信**：所有模块间通信使用 Pydantic 模型，禁止裸 `dict` 传递
  - 已确认的例外为四处：`ToolCenter.call(name, **params)` 的展开传参、`Bridge.parse_chat_request(payload: dict)` 的入参、`Bridge.to_api_response() -> dict` 的返回值、`api/routes.py` 中 `POST /api/chat` 以 `dict` 接收请求体
  - 前三处是系统边界，第四处是 HTTP 入口边界：该 `dict` 不经任何加工、原样交给 `Bridge.parse_chat_request()`，与第二处是**同一处边界**，不是新开的传参通道（若改用 `ChatRequest` 接收请求体，`Bridge` 的校验会沦为永不执行的死代码，且 1001 的产出点分裂成两处）
  - **不得**以上述例外为据，把裸 `dict` 扩散到其他接口；工具函数返回值仍必须是 Pydantic 模型
- **错误处理**：自定义异常类，统一在 `api/` 层捕获并转换为 HTTP 响应
- **日志**：使用标准库 `logging`，级别 INFO 及以上，禁止 `print` 调试

## 工作流（SDD 驱动）

1. 任何功能开发前，必须先有对应的 SDD 文档（存放于 `docs/`）
2. 模块级 SDD 四要素：**功能描述 / 接口签名 / 外部依赖 / 异常处理**
3. 代码生成分两阶段：先骨架（签名 + docstring）→ 确认后补全逻辑
4. 代码写完后必须对照 SDD 逐条验收

## 禁止事项

- 禁止跳过 SDD 直接写代码
- 禁止在业务逻辑中硬编码 API Key（必须从环境变量读取）
- 禁止删除或绕过测试代码
- 禁止提交 `.env` 文件到 Git

## 测试要求

- 每个模块必须有对应的 `test_<module>.py`
- 运行命令：`pytest tests/ -v`
- 覆盖率要求：核心模块 100%，整体 ≥ 80%

## 提交规范

- commit message 格式：`<type>(<scope>): <description>`
- type 取值：`feat` / `fix` / `refactor` / `docs` / `test` / `chore`
- commit message 结尾追加署名行：

  ```
  Co-Authored-By: Claude Code <noreply@anthropic.com>
  ```
