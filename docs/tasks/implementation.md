# 实现任务追踪

> 状态说明：pending=未开始 | skeleton=骨架完成 | done=完成 | blocked=阻塞

## 后端模块

### T1: Scheduler 骨架
- 对应SDD: docs/module-sdd/scheduler.md
- 状态: done
- 文件: src/scheduler.py

### T2: ReasoningEngine 骨架
- 对应SDD: docs/module-sdd/reasoning.md
- 状态: done
- 文件: src/reasoning.py

### T3: ToolCenter 骨架
- 对应SDD: docs/module-sdd/tools.md
- 状态: done
- 文件: src/tools.py
- 说明（v2.0）: `weather` 升级为双数据源 —— 设了 `MCP_WEATHER_URL` 调 MCP 天气
  服务（HTTP，契约见 tools.md §0），未设则回落顶部 `WEATHER_PATTERN` 静态表。
  至此 tools.md §3 声明的 `httpx` / `MCP_WEATHER_URL` 成为实际依赖，验收报告中
  `weather` 的 partial 消除（40/40 pass）

### T4: Bridge 骨架
- 对应SDD: docs/module-sdd/bridge.md
- 状态: done
- 文件: src/bridge.py

### T5: MemoryStore 骨架
- 对应SDD: docs/module-sdd/memory.md（第18节产出，本课复用）
- 状态: done
- 文件: src/memory.py

### T6: Scheduler 业务逻辑
- 状态: done
- 文件: src/scheduler.py
- 依赖: T2, T5 完成
- 说明: OODA 循环（Observe→Orient→Decide+Act）+ 四个 `_handle_*` 处理器；
  `plan_trip` 一律先落库再返回（AC-2）；下游异常原样冒泡，本层不捕获

### T7: ReasoningEngine 业务逻辑
- 状态: done
- 文件: src/reasoning.py
- 依赖: T3 完成
- 说明: parse_intent 正则解析 + generate_itinerary 三级降级
  （LLM → 模板 → LLMUnavailableError）；HTTP 客户端用 `httpx`（与 SDD §3 一致，
  非偏差）；`_call_deepseek` 返回 `str | None` 已回写 SDD v1.1（§4 要求该方法不外抛，
  失败只能以空值表达）
- 说明（v2.0，对应代码审查 F1）: `_parse_llm_plan` 天数不足时整体降级为模板方案，
  并按最终 `daily` 重算 `total_cost`；回填改用 `model_validate`，配合 `TripPlan`
  新增的「`len(daily) <= days`」校验器堵住 `model_copy` 不触发校验的漏洞

### T8: Bridge 业务逻辑
- 状态: done
- 文件: src/bridge.py
- 无依赖（纯转换）
- 说明: `to_trip_card` 相对 SDD 增加可选 `trip_id` 参数 —— trip_id 由落库时才
  生成，SDD 原签名无处取得

### T9: MemoryStore 业务逻辑
- 状态: done
- 文件: src/memory.py
- 无外部依赖（仅sqlite3标准库）
- 说明: WAL + busy_timeout，逐操作连接，模块级写锁；JSON 以未转义 UTF-8 落库；
  空结果返回 `[]`，删除不存在的 ID 抛 `TripNotFoundError`

### T10: FastAPI路由层
- 对应Schema: docs/schemas/
- 状态: done
- 文件: api/routes.py + main.py
- 依赖: T6, T7, T8, T9 完成
- 说明: 三个端点（`POST /api/chat`、`GET /api/trips`、`DELETE /api/trips/{id}`）
  + 依赖单例 + lifespan 启动建表
- **异常口径**：`api/` 层不写 try/except，业务异常冒泡到 main.py 的 handler，
  统一按 docs/schemas/api-response.md §3 错误码表转换 —— 删除不存在的行程返回
  `3001`/HTTP 200（非 500），msg 只用异常自身的业务文案，`str(e)` 不外泄
- **遗留**：`GET /api/trips/search`、`GET /api/health` 未实现（见 system-design.md
  接口表）
- **已解决（2026-10-03）**：`.env` 此前从未被加载（仓库内无任何 `load_dotenv`
  调用），`USE_MOCK_LLM` 只在 `.env.example` 里挂着、代码不读 —— 即「把 Key 粘进
  `.env` 不生效」。现由 `main.py` 导入期调用 `load_dotenv()`（不覆盖模式，真实环境
  变量优先），`src/reasoning.py` 读取 `USE_MOCK_LLM` 并在 Key 判断**之前**短路。
  见 [config.md](../module-sdd/config.md)，单测 `tests/test_main.py` +
  `tests/test_reasoning.py::test_use_mock_llm_*`

## 前端模块

> 对应 SDD: [docs/module-sdd/frontend.md](../module-sdd/frontend.md)（功能描述 / 接口签名 / 外部依赖 / 异常处理四要素，含消费的后端数据契约与验收表）

### T11: Vue3项目初始化
- 状态: done
- 文件: frontend/package.json + vite.config.js + index.html
- 说明: `npm create vite@latest frontend -- --template vue` 生成（create-vite 9.2.1 /
  Vite 8.3.2 / Vue 3.5.42）；`npm install axios` 装入 `^1.20.0`；
  `vite.config.js` 配置 `/api` → `http://localhost:8000` 代理，前端源码因此不含
  任何后端地址字面量；`index.html` 标题改为「旅行规划智能体」（`lang="zh-CN"`）。
  脚手架自带的 `HelloWorld.vue`、`src/assets/`、`public/icons.svg` 已删除

### T12: 对话界面组件
- 状态: done
- 文件: frontend/src/App.vue + src/components/ChatInput.vue + src/components/MessageList.vue
- 说明: `App.vue` 是全应用唯一状态持有者（`messages` / `loading`），`onMounted`
  拉历史行程；`ChatInput` 回车发送（Shift+Enter 换行）、发送后清空、请求中禁用；
  `MessageList` 三种气泡 —— `user` 靠右蓝色 / `assistant` 靠左灰色（可挂行程卡片）/
  `error` 靠左红色（后端非 0 code 时用）。**不在前端做意图识别**，输入原样上行

### T13: 行程卡片组件
- 状态: done
- 文件: frontend/src/components/TripCard.vue
- 说明: 目的地 / 天数 / 总预算 + 逐日折叠面板（默认展开第 1 天）+ 天气标签；
  `weather` 为 `null` 时显「天气暂无」（后端工具失败时会置空，对应 AC-5）；
  `degraded` 为真时加降级提示条；`trip_id` 为空串时不渲染删除按钮（契约见
  `src/models/trip.py`）

### T14: API客户端封装
- 状态: done
- 文件: frontend/src/api/client.js
- 说明: axios 实例 + 响应拦截器，三个方法（`sendMessage` / `getTrips` /
  `deleteTrip`）**只 resolve 业务数据**，非 0 code 一律 reject `ApiError`。
  ⚠️ 拦截器必须**读 body 的 `code` 而非 HTTP 状态**：后端把 3001 这类业务错误
  放在 HTTP 200 里、把 1001 放在 HTTP 400 里（api-response.md §5），只看状态码
  会把「行程不存在」误判为成功、把「参数校验失败」误判为网络故障。
  `baseURL` 取 `VITE_API_BASE_URL`，开发期留空走 Vite 代理

## 测试

### T15: 各模块单测
- 状态: done
- 运行: pytest tests/ -v
- 覆盖率目标: ≥80%
- 结果: **232 passed**（T16 补齐配置加载后，新增 21 例）；`src/` 与 `api/` 全部模块
  100%（含 v2.0 新增的 MCP 天气通路与 `TripPlan` 校验器分支）
- 文件: tests/test_tools.py、test_memory.py、test_reasoning.py、test_bridge.py、
  test_scheduler.py、test_routes.py、test_itinerary.py、**test_main.py**

### T16: 配置加载（.env 接线）
- 对应 SDD: [docs/module-sdd/config.md](../module-sdd/config.md)
- 状态: done
- 文件: main.py + src/reasoning.py + pyproject.toml + .env.example + .env（本地，未入库）
- 说明: `main.py` 导入期调用 `load_dotenv()`（不覆盖模式）；`src/reasoning.py` 新增
  `ENV_USE_MOCK_LLM` / `_env_flag()` / `self._mock_llm`，`_call_deepseek` 中 Mock 短路
  置于 Key 判断**之前**（配了 Key 也要求不出网时，Key 不该推翻该意图）；依赖加
  `python-dotenv>=1.0`
- 验证: 见下方「配置加载验证（2026-10-03）」

### 前端验证（2026-10-03，T11–T14 完成后）

| # | 验证项 | 方法 | 结果 |
| --- | --- | --- | --- |
| 1 | 构建 | `npm run build` | 通过，75 modules，dist 120.92 kB / gzip 46.31 kB |
| 2 | 代理链路 | 同时起 `python main.py` 与 `npm run dev`，经 5173 打接口 | `/api/chat`、`/api/trips`、`DELETE` 均正常转发，与直连 8000 结果逐字一致 |
| 3 | 业务分支 | 经代理实测 | 删除成功 code 0 / 重复删除 code 3001（HTTP **200**）/ 空 body code 1001（HTTP **400**）/ 无目的地输入回落到 `action=chat` 引导语 |
| 4 | 客户端判码 | 由 dev server 编译出 `client.js`，在 Node 中直连后端跑真实代码 | 8 项断言全通过（剥壳、`TripCard` 字段、删除成功、3001、1001、后端不可达 NetworkError） |
| 5 | 数据契约 | 比对真实响应与组件读取的字段 | 一致：`trip_id` / `destination` / `days` / `total_cost` / `degraded` / `daily[].{day_index,date,spots[],estimated_cost,weather}` |

**局限（未覆盖）**：项目未引入 vitest / playwright，故无浏览器端 e2e —— Vue 组件只经构建期 SFC 编译校验，未做运行时交互测试；第 4 项是在 Node 中复用编译产物打后端，不经浏览器（故不覆盖 CORS、渲染与响应式）。第 3 项探测产生的行程已全部删除，`data/` 目录（SQLite 开发库）一并清理。

### 配置加载验证（2026-10-03，T16 完成后）

| # | 验收项（config.md §6） | 方法 | 结果 |
| --- | --- | --- | --- |
| 1 | `.env` 的值能到达 `ReasoningEngine` | `test_dotenv_values_reach_the_engine` | 通过（Key / model / base_url / mock 四个字段） |
| 2 | `main.py` 导入时调用 `load_dotenv()` | `test_main_calls_load_dotenv_at_import`（reload + 打桩 `dotenv.load_dotenv`） | 通过，且断言**恰好一次**、**无参调用** |
| 3 | 真实环境变量优先于 `.env` | `test_real_environment_wins_over_dotenv` | 通过（若改成 `override=True` 会失败） |
| 4 | `USE_MOCK_LLM=true` 时不出网且 `degraded=True` | 记账 spy 替换 `httpx.post` | 通过，spy 调用记录为空 |
| 5 | `USE_MOCK_LLM` 非法值不抛异常 | 参数化 8 个非法值 | 通过，一律归为 false |
| 6 | 未配置任何 LLM 变量时应用照常启动 | 既有 `test_routes.py` 用例（fixture 清 `DEEPSEEK_API_KEY`） | 通过 |

**变异测试**（确认用例不是空话）：把 `_env_flag` 改成恒返回 `False`、删掉 `main.py`
的 `load_dotenv()` 调用后重跑 —— 8 个用例失败（含 6 个真值参数化），且 spy 如实记录了
`https://api.deepseek.com/chat/completions`，即「开关失效 → 真的会出网」被捕获。
第一版用例曾用「抛异常的桩」验证不出网，因 `_call_deepseek` 的 `except Exception`
会把异常吞成 `None` 而**无法区分两种情形**，已改为记账 spy。

**测试隔离**：`main.py` 的 `load_dotenv()` 是导入期副作用，而 `test_routes.py` 导入
`main`，于是开发机 `.env` 会渗进整个测试进程。已在 `test_reasoning.py` 加 autouse
fixture 清 `USE_MOCK_LLM` / `DEEPSEEK_API_KEY`，`test_main.py` 用 `clean_env` fixture
清同一组变量 —— 否则本机把 `USE_MOCK_LLM` 设成 true 会让「成功取回内容」等用例无辜挂掉。

### 代码审查与修复（2026-10-03）
- 报告: docs/tasks/code_review_report.md
- 结果: 发现 7 项（1 高 / 2 中 / 4 低）；F1（高层级数据一致性缺陷）、F2（类型标注
  失真）、F3（第 4 处裸 dict 例外未登记）已修复并补回归用例；F4–F7 为「建议」级，
  保留待定
- 回归验证: F1/F2 修复后 196 → 203 passed；v2.0 天气通路后 → **211 passed**，
  覆盖率维持 100%（`src/` 与 `api/`）

## 进度汇总

| 阶段 | 完成数 | 总数 | 进度 |
|------|--------|------|------|
| 骨架生成 | 5/5 | 5 | 100% |
| 逻辑补全 | 5/5 | 5 | 100% |
| 前端搭建 | 4/4 | 4 | 100% |
| 配置加载 | 1/1 | 1 | 100% |
| 测试 | 1/1 | 1 | 100% |
| **总计** | **16/16** | **16** | **100%** |

前后端链路已可端到端运行。两个终端分别启动：

```bash
python main.py                      # 后端 :8000（首次启动自动建表）
cd frontend && npm run dev          # 前端 :5173（/api 由 Vite 代理到 8000）
```

浏览器打开 <http://localhost:5173>，输入「帮我规划去北京玩3天」即可生成并落库
行程卡片，刷新页面由 `GET /api/trips` 取回历史，卡片上的删除按钮走
`DELETE /api/trips/{trip_id}`。
