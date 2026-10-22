# 代码审查清单

配合 [SKILL.md](./SKILL.md) 使用。每条给出**判定标准**与**核对方式** —— 能跑命令的
就不要靠眼睛看，把命令结果作为结论依据。严重度取值见 SKILL.md。

---

## 0. 审查前置

| 检查项 | 判定标准 | 核对方式 |
| --- | --- | --- |
| 范围明确 | 知道改了哪些文件、改了什么 | `git status` / `git diff --stat`（本仓库当前无 commit 时改用文件 mtime） |
| SDD 存在 | 新功能有对应 `docs/module-sdd/*.md`，且接口签名可对照 | 打开对应 SDD 的 §2 |
| 测试基线 | 改动前 `pytest tests/ -v` 全绿 | 先跑一遍，避免把旧问题算到本次改动头上 |
| 契约面 | 是否触碰错误码表 / `ApiResponse` / 前端契约 | `docs/schemas/api-response.md` §3 |

---

## 1. 编码规范

| 检查项 | 判定标准 | 核对方式 | 严重度 |
| --- | --- | --- | --- |
| PEP8 / 行宽 | 行宽 ≤ 100（本项目 ruff 配置） | `python -m ruff check .`；未安装则人工看长行 | 低 |
| type hints | 所有函数参数与返回值均有标注 | 逐个函数看 `def` 行 | 中 |
| 函数长度 | **≤ 50 行**（CLAUDE.md 硬要求） | 见下方脚本 | 中 |
| 命名 | 模块/函数 `snake_case`，类 `PascalCase`，常量 `UPPER_SNAKE`；名字说得出用途 | 人工 | 低 |
| 导入顺序 | 标准库 → 第三方 → 本项目，各组内字母序 | `python -m ruff check .`（isort 规则） | 低 |

函数长度核对脚本（无需额外依赖）：

```bash
python - <<'PY'
import ast, pathlib
for p in sorted(pathlib.Path('.').rglob('*.py')):
    if any(s in p.parts for s in ('.venv', 'node_modules', '__pycache__')):
        continue
    for node in ast.walk(ast.parse(p.read_text(encoding='utf-8'))):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            length = node.end_lineno - node.lineno + 1
            if length > 50:
                print(f"{p}:{node.lineno} {node.name} = {length} 行")
PY
```

---

## 2. 逻辑正确性

| 检查项 | 判定标准 | 核对方式 | 严重度 |
| --- | --- | --- | --- |
| 边界条件 | 上下界都处理（`limit` 越界、天数 1–30、空列表） | 找 `>=` / `<=` / `min` / `max` 处，对照 SDD 的范围声明 | 中 |
| 空值处理 | `None` / 空串 / 空列表各有明确分支，不与「取默认值」混淆 | 看 `if not x` / `is None` 分支是否语义正确 | 中 |
| 空结果 ≠ 异常 | 查询无结果返回 `[]`，不是抛异常 | `src/memory.py` 的 list/search 路径 | 中 |
| 终止条件 | 循环必然收敛；无 `while True` 无出口 | 人工 | 高 |
| 异常完整 | 每个 `try` 都有对应 `except`，且不裸 `except Exception: pass` | `grep -n "except" -A2` | 高 |
| 异常再抛 | 转换异常时用 `raise ... from exc` 保留因果链 | `grep -n "raise" src/` | 中 |
| 幂等 | 同一操作重复执行的结果可预期（如重复删除应报 3001） | 对照 SDD 语义 | 中 |

---

## 3. 架构合规

| 检查项 | 判定标准 | 核对方式 | 严重度 |
| --- | --- | --- | --- |
| 依赖方向 | `api/ → src/` 单向；**`src/` 内不得出现 FastAPI** | `grep -rn "fastapi" src/` 应无输出 | 高 |
| 分层职责 | `src/` 无路由/请求对象；`api/` 无业务逻辑（只做校验/调用/异常转换） | 打开 `api/routes.py` 看是否有分支业务 | 中 |
| 无循环依赖 | 模块级互引会炸 | `python -c "import main"` | 高 |
| 接口与 SDD 一致 | 签名逐字对齐 `docs/module-sdd/*.md` §2 | 见 `docs/tasks/acceptance_report.md` 的核对命令 | 中 |
| 无硬编码 | 路径、URL、模型名、城市表等外部可变值应来自环境变量或模块顶部常量 | `grep -rn "http://\|https://\|\.db\"" --include=*.py src/ api/` | 中 |
| 模型边界 | 模块间通信只用 Pydantic 模型 | `grep -rn ": dict\|-> dict" --include=*.py src/ api/`，命中后比对下方**豁免清单** | 中 |
| SDD 偏差有记录 | 代码偏离 SDD 时，偏差写进 docstring **且**回写 SDD | 找代码里的「⚠️」块 | 中 |

**裸 `dict` 豁免清单**（命中即跳过，不要报）：

1. `ToolCenter.call(name, **params)`
2. `Bridge.parse_chat_request(payload: dict)`
3. `Bridge.to_api_response() -> dict`
4. `api/routes.py` 的 `POST /api/chat` 请求体（HTTP 入口边界，原样交给第 2 处）

---

## 4. 安全性

| 检查项 | 判定标准 | 核对方式 | 严重度 |
| --- | --- | --- | --- |
| 无硬编码密钥 | API Key / Token / 密码一律 `os.getenv` | `grep -rn "sk-\|api_key *= *[\"']\|password" --include=*.py .` | **高** |
| 密钥不入日志 | `logger` 不打印 Key、请求头、用户原话中的敏感内容 | 审 `LOGGER.*` 调用 | 高 |
| 输入校验 | 每个外部入口都有校验：`/api/chat` 走 `Bridge.parse_chat_request`，查询参数有 `ge` / `le` | 打开 `api/routes.py` | 高 |
| SQL 注入 | 一律参数化（`?` 占位），禁止 f-string / `%` / `+` 拼接 SQL | `grep -n "execute(\|executescript(" src/memory.py`，逐行确认无拼接 | **高** |
| LIKE 通配符 | 用户输入进 `LIKE` 前转义 `%` `_` | 看 `_escape_like` 是否被调用 | 中 |
| 路径遍历 | 文件/库路径不直接来自请求参数 | `MEMORY_DB_PATH` 只从环境变量读；`DELETE` 的 `trip_id` 只进参数化 SQL | 高 |
| 错误信息不泄露 | 对外 `msg` 不含 SQL / 文件路径 / 堆栈；细节只进日志 | 看 handler 与异常 `msg` 构造处 | 中 |
| 依赖最小 | 新增依赖有理由，且不引入未使用项 | 比对 `pyproject.toml` 与 `import` | 低 |

---

## 5. 可维护性

| 检查项 | 判定标准 | 核对方式 | 严重度 |
| --- | --- | --- | --- |
| docstring | 公开函数/类有；含 `Args` / `Returns` / `Raises` | 人工；`pytest --doctest-modules` 可选 | 中 |
| 有单元测试 | 每个 `src/*.py` 有 `tests/test_<module>.py`，覆盖正常 + 边界 + 异常路径 | `ls tests/`；`python -m pytest --cov=src --cov-report=term-missing` | 中 |
| 测试未被削弱 | 无新增 `skip` / `xfail` / 删除断言 / 放宽断言 | `grep -rn "skip\|xfail" tests/` | **高** |
| 覆盖率 | 核心模块 100%，整体 ≥ 80% | 同上 coverage 命令 | 中 |
| 日志合理 | 关键分支有 INFO 日志（Observe/Orient/Act 至少一处）；无 `print` | `grep -rn "^\s*print(" --include=*.py src/ api/ main.py` 应无输出 | 中 |
| 错误信息友好 | 面向用户的中文文案，说明「发生了什么 + 怎么办」 | 读异常构造处 | 低 |
| 注释解释「为什么」 | 不复述代码，只写决策理由与反直觉处 | 人工 | 低 |
| 提交规范 | `<type>(<scope>): <description>`，type ∈ feat/fix/refactor/docs/test/chore；结尾署名行 | `git log --format=%B -n 5` | 低 |
| `.env` 未入库 | 只有 `.env.example` 被跟踪 | `git status --porcelain \| grep "\.env$"` 应无输出 | **高** |

---

## 6. 常见误报（本仓库特有，报之前先排除）

| 看起来像问题 | 实际 | 依据 |
| --- | --- | --- |
| `src/tools.py` 里天气/景点是写死的表 | 阶段内计划内的 Mock，替换点集中在顶部常量区，第 20 节接 MCP | `docs/module-sdd/tools.md` §0 |
| `api/routes.py` 里没有 `try/except` | 设计如此：异常冒泡到 `main.py` 的 handler 统一转换，错误码表只有一处实现 | `docs/schemas/api-response.md` §3 |
| `to_trip_card(plan, trip_id="")` 比 SDD 多一个参数 | 已回写 SDD v1.1 并写明理由 | `docs/module-sdd/bridge.md` §2 |
| `save_trip(itinerary, *, intent_raw="")` 比 SDD 多一个参数 | 同上（`intent_raw` 在 §3 表结构中为 `NOT NULL`） | `docs/module-sdd/memory.md` §2 |
| `_call_deepseek -> str \| None` 与 SDD 原签名不同 | 已回写 SDD v1.1：SDD §4 要求该方法不外抛，失败只能以空值表达 | `docs/module-sdd/reasoning.md` §2 |
| `use of bare dict` 出现在上述四处 | 经确认保留的边界例外 | CLAUDE.md |
| `src/models/` 与 `src/scheduler.py` 都有 Pydantic 模型 | `AgentResult` 按 scheduler SDD 就地声明，上移与否待确认，非违规 | `src/scheduler.py:58` |
| 默认值 `600.0` / `300` / `1200` 之类数字 | 有具名常量承载，属业务常量而非魔术数字 | `src/tools.py` 常量区 |
