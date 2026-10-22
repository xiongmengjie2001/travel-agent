# Config SDD

> 文件：`main.py`（加载入口）、各模块的 `ENV_*` 常量
> 版本：v1.0 / 2026-10-03
> 对应遗留项：implementation.md T10「遗留」中的 `.env` 未加载、`USE_MOCK_LLM` 未读取

## 1. 功能描述

规定本应用的配置来源与优先级，并把散落在各模块的环境变量收成一份可核对的清单。

**背景**：`.env.example` 自骨架阶段起就存在，但仓库里从未有任何代码加载它 —— 所有模块一律直接 `os.getenv()`，读的是**进程环境**。结果是「按 `.env.example` 复制出 `.env` 再填 Key」这条最自然的路径完全不生效，且失败是静默的（不报错，只是 LLM 永不调用、一路走模板降级）。本 SDD 消除这个缺口。

**优先级（高 → 低）**：

1. 进程环境变量（shell 里 `export` / `setx` 设的）
2. 项目根目录的 `.env` 文件
3. 各模块代码内的默认值（如 `DEFAULT_MODEL`）

第 1 条优先于第 2 条，即 `load_dotenv()` 以**不覆盖**模式调用（其默认行为）：已经存在的真实环境变量不会被 `.env` 里的同名项顶掉。这样 CI / 容器注入的配置永远压过开发机上的本地文件。

## 2. 接口签名

```python
# main.py —— 模块级，且必须是本文件最早执行的语句之一
from dotenv import load_dotenv

load_dotenv()          # 无参数：从当前工作目录向上查找 .env
```

```python
# src/reasoning.py —— USE_MOCK_LLM 的解析
ENV_USE_MOCK_LLM = "USE_MOCK_LLM"

def _env_flag(name: str) -> bool: ...
```

### 2.1 `load_dotenv()` 的位置约束

**必须早于任何 `os.getenv()` 的执行**。当前代码里没有模块级的 `os.getenv` 调用（全部发生在函数 / 构造函数体内），故放在 import 块之后即可；但 [main.py](../../main.py) 自身的 `logging.basicConfig(level=os.getenv(ENV_LOG_LEVEL, ...))` 是模块级调用，`load_dotenv()` **必须排在它前面**，否则 `LOG_LEVEL` 从 `.env` 设置无效。

`load_dotenv()` 无参数时从当前工作目录**逐级向上**查找 `.env`，因此从仓库根目录启动（`python main.py`）与从任意子目录启动都能命中同一个 `.env`。

### 2.2 `_env_flag` 的解析规则

环境变量是字符串，布尔语义需自行约定。取**常见真值**，其余一律为假：

| 输入（去空白、忽略大小写） | 结果 |
| --- | --- |
| `1` / `true` / `yes` / `on` | `True` |
| 其他任意值（含 `0` / `false` / 空串 / 未设置） | `False` |

**非法值不抛异常** —— 配置错误不该让应用启动失败，归为「未启用」即可。这是与 §4「异常处理」一致的取舍。

### 2.3 `USE_MOCK_LLM` 的语义

`USE_MOCK_LLM=true` 表示**强制不发起真实 LLM 调用**，直接走本地模板方案（对应 NFR-2 / AC-5 的 Mock 模式）。

实现位置是 `_call_deepseek()` 的**第一道判断**，早于「有没有 API Key」：

```python
if self._mock_llm:
    LOGGER.info("USE_MOCK_LLM 已开启，跳过真实 LLM 调用")
    return None
if not self._api_key:
    return None
```

结果与「未配置 Key」完全一致：`generate_itinerary` 照常降级为模板，`degraded=True`，`code` 仍为 `0`。**开关不影响接口契约**，只影响是否出网。

它与 Key 的关系是**独立**的：设了 Key 又开 `USE_MOCK_LLM=true` 仍不出网 —— 这是「我不想花钱 / 我在离线环境」的表达，不应被 Key 的存在推翻。

## 3. 外部依赖

| 依赖 | 版本 | 用途 |
| --- | --- | --- |
| `python-dotenv` | `>=1.0` | 解析并注入 `.env` |

### 环境变量总表

| 变量 | 读取位置 | 默认值 | 未设置时 |
| --- | --- | --- | --- |
| `DEEPSEEK_API_KEY` | `src/reasoning.py:123` | 无 | 纯模板模式，不出网 |
| `DEEPSEEK_BASE_URL` | `src/reasoning.py:124` | `https://api.deepseek.com` | 用默认端点 |
| `DEEPSEEK_MODEL` | `src/reasoning.py:125` | `deepseek-v4-flash` | 用默认模型名 |
| `USE_MOCK_LLM` | `src/reasoning.py`（v1.1 新增） | `False` | 视为 `false` |
| `AMAP_MCP_URL` | `src/tools.py`（构造 ToolCenter 时） | 无 | `weather` / `attractions` / `route` 全部走 Mock，不出网 |
| `AMAP_MCP_KEY` | `src/tools.py`（构造 ToolCenter 时） | 无 | 若 `AMAP_MCP_URL` 已含 `key` 参数则无需设置；否则请求会被高德拒绝并降级 Mock |
| `MEMORY_DB_PATH` | `api/routes.py:65` | `./data/travel_agent.db` | 用默认路径 |
| `LOG_LEVEL` | `main.py:53` | `INFO` | 用默认级别 |
| `CORS_ALLOW_ORIGINS` | `main.py:98` | `localhost:5173` / `127.0.0.1:5173` | 用默认白名单 |
| `API_HOST` / `API_PORT` | `main.py:168-169` | `127.0.0.1` / `8000` | 用默认地址 |

**高德 MCP 配置语义**（详见 [mcp.md](./mcp.md)）：

- `AMAP_MCP_URL` 是**总开关**：留空 = 纯 Mock 模式，四个工具都不出网；
- `AMAP_MCP_KEY` 可留空 —— 客户端只在 URL 未自带 `key` 查询参数时追加；
- MCP 调用失败不会导致启动失败或接口 5xx，只会让对应工具降级 Mock；
- 旧的 `MCP_WEATHER_URL` 已在 v4.0 废弃，代码不再读取。

⚠️ 前端的 `VITE_API_BASE_URL` **不在上表**：Vite 只读 `frontend/` 目录下的 `.env*`，项目根目录的 `.env` 对它无效。前端默认留空走 Vite 代理（见 [frontend.md](./frontend.md) §3），需要直连时写进 `frontend/.env.local`。

## 4. 异常处理

| 情形 | 处理 | 理由 |
| --- | --- | --- |
| `.env` 不存在 | 静默跳过，继续读进程环境 | 「没有 `.env`」是正常状态（CI / 生产靠环境变量注入），不该报错 |
| `.env` 存在但某变量未定义 | 该模块用自身默认值 | 各模块默认值已保证可运行 |
| 变量值非法（如 `USE_MOCK_LLM=maybe`） | 归为未启用，不抛异常 | 配置错误不应导致启动失败 |
| `DEEPSEEK_API_KEY` 为空串 | 等同于未设置 → 纯模板模式 | `os.getenv` 返回 `""`，构造上为假值，无需特判 |

**错误处理的一致性**：本模块不引入新的异常类型。配置缺失一律表现为**能力降级**（模板方案 / 静态天气表），而非启动失败 —— 与 AC-5「降级是预期内设计」同调。

## 5. `.env` 与 Git

- `.env` 已在 `.gitignore` 中（`.env` 与 `.env.*` 两条规则，`!.env.example` 例外），**不得提交**（CLAUDE.md 禁止事项）；
- `.env.example` 是提交的模板，**只写字段名与说明，不含真实值**；
- 换机器时的步骤：复制 `.env.example` → `.env` → 填值 → 重启后端。

## 6. 验收

| # | 验收项 | 判定 |
| --- | --- | --- |
| 1 | `.env` 里的 `DEEPSEEK_API_KEY` 能被 `ReasoningEngine` 读到 | 单测覆盖 |
| 2 | `main.py` 导入时调用了 `load_dotenv()` | 单测覆盖（reload + 打桩） |
| 3 | 真实环境变量优先于 `.env` | `load_dotenv()` 不覆盖模式，单测覆盖 |
| 4 | `USE_MOCK_LLM=true` 时不出网，返回 `degraded=True` | 单测覆盖（打桩 `httpx.post`，断言未被调用） |
| 5 | `USE_MOCK_LLM` 非法值不抛异常 | 单测参数化覆盖 |
| 6 | 未配置任何 LLM 变量时应用照常启动 | 既有用例已覆盖 |
