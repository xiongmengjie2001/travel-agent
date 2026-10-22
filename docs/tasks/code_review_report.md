# 代码审查报告

> 审查日期：2026-10-03
> 审查范围：`src/scheduler.py`、`src/reasoning.py`、`src/tools.py`、`api/routes.py`
> 审查依据：`docs/module-sdd/{scheduler,reasoning,tools,bridge,memory}.md` §2、根目录 `CLAUDE.md`、`.claude/skills/code-review/checklist.md`
> 使用技能：`.claude/skills/code-review/`（五维：编码规范 / 逻辑正确性 / 架构合规 / 安全性 / 可维护性）
> 修复状态：2026-10-03 当日完成 F1/F2/F3；F4–F7 为「建议」级，保留待定

## 审查基线（实跑结果）

| 核对项 | 命令 | 结果 |
| --- | --- | --- |
| 测试 | `pytest tests/ -q --cov=src --cov=api` | 196 passed（审查时）→ **211 passed**（修复后），四文件覆盖率均 100% |
| 函数 ≤ 50 行 | AST 脚本（checklist §1） | 0 违规 |
| 行宽 ≤ 100 | 逐文件按 Unicode 计字符 | 仅 `tests/test_reasoning.py:205`（不在本次范围） |
| `src/` 无 FastAPI | `grep -rn "fastapi\|starlette" src/` | 仅注释命中，通过 |
| 无 `print` | `grep -rn "print(" src/ api/` | 无输出 |
| 无硬编码密钥 | `grep -rn "sk-\|api_key *= *[\"']"` | 无命中 |
| 无 `skip`/`xfail` | `grep -rn "skip\|xfail" tests/` | 无输出 |
| `.env` 未入库 | `git status --porcelain` | 仅 `.env.example` |

---

## 问题清单

| # | 维度 | 位置 | 严重度 | 问题 | 建议 | 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| F1 | 逻辑 | `src/reasoning.py:468` | **高** | LLM 返回天数 < 需求时产出 `days != len(daily)` 且 `degraded=False` 的行程并入库；截断路径下 `total_cost` 不重算 | 天数不足即降级；按最终 daily 重算 total_cost；`model_copy` 换 `model_validate`；`TripPlan` 加 `len(daily) <= days` | **已修复** |
| F2 | 规范 | `src/scheduler.py:180,186` | 中 | `_to_card(record: object)` 抹掉真实类型，靠 `# type: ignore[attr-defined]` 压制 | 导入 `TripRecord` 作真实标注，删除 ignore | **已修复** |
| F3 | 架构 | `api/routes.py:86` | 中 | 第 4 处裸 `dict` 边界未登记进例外清单，与 CLAUDE.md「例外仅三处」冲突 | 在 CLAUDE.md 补第 4 条并说明它是 HTTP 入口边界 | **已修复** |
| F4 | 可维护性 | `src/tools.py:112` / `src/reasoning.py:59` | 低 | 同一兜底时长（120 分钟）两模块各定义一次；`src/tools.py:111` 注释误写「票价」 | 收口到一处常量；订正注释 | 待定 |
| F5 | 架构 | `src/scheduler.py:32` | 低 | `TRIP_ID_PATTERN` 复制了 memory 的 ID 格式知识，格式变更会静默失配 | 格式定义收口到 `src/memory.py` 并复用 | 待定 |
| F6 | 逻辑 | `src/reasoning.py:66` | 低 | `(\d{3,5})` 对 6 位以上预算截取前 5 位（「预算 200000」→ 20000） | 放宽为 `\d{3,7}` 并补用例 | 待定 |
| F7 | 安全 | `src/tools.py:180,186` | 低 | 对外 2002 的 `msg` 内嵌原始异常文本，接 MCP 后可能带 URL / 路径 | `msg` 只留工具名，细节进日志 | 待定 |

## F1 的失败场景（修复前实测复现）

| 场景 | 输入 | 修复前结果 | 修复后结果 |
| --- | --- | --- | --- |
| 模型给的天数**少于**需求 | 用户「去北京玩 5 天」，LLM 返回 `days=3, daily=[3 条]` | `plan.days=5`、`len(daily)=3`、`degraded=False` → 错误数据入库，前端按 5 天渲染但只有 3 天内容 | `degraded=True`，模板方案给出自洽的 5 天 |
| 模型给的天数**多于**需求 | 用户「玩 3 天」，LLM 返回 `days=5, total_cost=5000`，每日 `estimated_cost=1000` | `daily` 截断为 3 条，但 `total_cost` 仍为 **5000**（明细之和 3000） | `total_cost=3000`，与明细一致 |

根因：原实现用 `model_copy(update=...)` 回填字段，而 **`model_copy` 默认不触发校验器**；
且 `TripPlan` 当时没有 `daily` / `days` 的跨字段校验，任何一层都拦不住。

## F3 的处置说明

第 4 处裸 `dict` **不是新开的传参通道**：`api/routes.py:86` 的请求体 `dict` 不经任何加工、
原样交给 `Bridge.parse_chat_request()`，与 CLAUDE.md 第 2 处例外是**同一处边界**。
若改用 `ChatRequest` 作请求体，`Bridge` 的校验会沦为永不执行的死代码，1001 的产出点
也会分裂成两处 —— 故选择**登记**而非改代码。已在 `CLAUDE.md`、`.claude/skills/code-review/SKILL.md`、
`.claude/skills/code-review/checklist.md` 三处同步补记。

---

## 审查范围外但一并记下的观察

| # | 位置 | 说明 |
| --- | --- | --- |
| O1 | `src/scheduler.py:138` `_handle_list_trips(text)` 未用 `text` | SDD §2 规定的统一签名，刻意保留，非问题 |
| O2 | `src/tools.py:369` `_cost` 的 `spots` 未参与计算 | SDD §2 签名所定，docstring 已说明 |
| O3 | `src/reasoning.py:85-86` 景点池与 tools 重复 | 刻意重复：回退源不得与被回退对象同源 |
| O4 | `src/scheduler.py:98` 用户原话进 INFO 日志 | 本地单机不构成缺陷（`intent_raw` 本就按设计入库）；多租户部署前再评估 |
| O5 | `src/reasoning.py:394-397` `_safe_days` 的 `int(raw)` 无 try | 唯一调用点入参来自 `(\d+)` 分组，构造上必可转 int |
| O6 | `api/routes.py:155` `Path(min_length=1)` 不校验 ID 格式 | 畸形 ID 走 3001、聊天入口走 1001，两处口径不一致；自然修复与 F5 同源 |
| O7 | `.env.example:12` `USE_MOCK_LLM` | **声明了但无任何代码读取** —— 实际只有 `DEEPSEEK_API_KEY` 为空才走模板模式。已在 `docs/tasks/implementation.md` 的 T10「遗留」中登记，属已知未完成项，非本次新发现 |

## 未覆盖

- `main.py`（handler / CORS / lifespan）、`src/bridge.py`、`src/memory.py`、`src/models/*` 未逐条审查，仅在交叉引用处查看。
- `ruff` / `mypy` 未安装在本机：规范维度靠人工 + AST 脚本核对，`pyproject.toml` 的 `select = ["E","F","I","N","UP","B","SIM"]` 未实际执行。
- 前端 `frontend/`、并发压测、各 SDD 的 §3–§7 未覆盖。
