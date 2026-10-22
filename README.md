# 旅行智能体 Travel Agent v2

一个基于大模型的自主决策旅行规划系统。用户用自然语言描述旅行需求（如"帮我规划北京3日游，预算3000"），系统自动完成目的地识别、行程编排、天气查询、景点推荐、路线规划与费用估算，最终输出一份可交互的完整行程卡片。

## 技术栈

**后端**
- Python 3.12 + FastAPI + Uvicorn
- Pydantic v2 数据校验
- httpx HTTP 客户端
- SQLite（内置行程记忆）

**前端**
- Vue 3 + TypeScript + Vite
- Pinia 状态管理
- axios 统一 API 层

**AI 与数据**
- DeepSeek（openai 兼容接口）
- 高德地图 MCP（天气 / POI / 路线 / 地理编码）
- 48 城目的地知识库（景点门票 / 游玩时长 / 城市消费单价）

## 功能特性

- Function Calling 多轮工具调用：模型自主决定调哪些工具、调几轮
- MCP 优先 + Mock 三级降级：高德 → 心知天气 → 本地静态表，任意数据源故障不中断行程生成
- 48 城真实知识库：覆盖一线 4 城 + 新一线 15 城 + 二线 / 强二线 29 城，含门票、游玩时长、城市消费单价
- 目的地有效性校验：经高德地理编码验证，无效城市友好引导
- 三栏前端：顶部栏 + 历史行程侧边栏 + 对话区
- 可折叠行程卡片、多日天气展示、分项花费明细、Toast 通知、错误横幅
- 亮 / 暗双主题、设置面板

## 快速开始

### 后端

```bash
cd travel-agent2
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

pip install -e .
cp .env.example .env    # 填入你的 API Key

python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

后端启动后访问 http://localhost:8000/docs 查看 Swagger。

### 前端

```bash
cd travel-agent2/frontend
npm install
npm run dev
```

前端运行在 http://localhost:5173。

### 环境变量

在 `.env` 中配置（参考 `.env.example`）：

| 变量 | 必填 | 说明 |
|---|---|---|
| `DEEPSEEK_API_KEY` | 是 | DeepSeek API Key，留空则走纯模板降级 |
| `DEEPSEEK_BASE_URL` | 否 | 默认 `https://api.deepseek.com` |
| `DEEPSEEK_MODEL` | 否 | 默认 `deepseek-v4-flash` |
| `USE_MOCK_LLM` | 否 | 设为 `true` 强制不出网，始终走模板 |
| `AMAP_MCP_KEY` | 推荐 | 高德地图 Web 服务 Key，配了天气 / POI / 路线走真实数据 |
| `AMAP_MCP_URL` | 否 | 默认 `https://mcp.amap.com/mcp` |
| `SENIVERSE_API_KEY` | 否 | 心知天气 Key，高德失败时的二级天气源 |

### 48 城知识库

`data/cities/` 下按城市分级存放，每城一个 JSON 文件：

```
data/cities/
├── tier1/       北京 / 上海 / 广州 / 深圳（4 城）
├── tier1_5/     新一线 15 城
└── tier2/       二线 / 强二线 29 城
```

每个文件结构：

```json
{
  "cost_per_day": {"lodging": 400, "food": 150, "transit": 50},
  "spots": [
    {"name": "故宫博物院", "ticket": 60, "duration_min": 240, "type": "古迹"}
  ]
}
```

后端启动时自动扫描全部 JSON 加载到内存。新增城市只需在 `data/cities/` 对应目录丢一个 JSON 文件，重启生效。

## 数据链路

```
天气  → 高德 MCP → 心知天气 → 本地静态表（三级）
景点  → 高德 POI 搜索 → 知识库补门票 / 时长 → Mock 池
路线  → 高德地理编码 + 驾车规划 → 估算距离
花费  → 知识库 cost_per_day × 天数 + 真实门票累加 → 档位估算
```

## 项目结构

```
travel-agent2/
├── api/                  FastAPI 路由
├── src/                  核心业务逻辑
│   ├── bridge.py         意图 / 动作桥接
│   ├── reasoning.py      LLM 推理与 Function Calling
│   ├── tools.py          工具中心 + 高德 MCP + 知识库 loader
│   ├── mcp_client.py     高德 MCP 客户端
│   ├── memory.py         SQLite 行程记忆
│   ├── scheduler.py      动作调度
│   └── models/           Pydantic 领域模型
├── data/cities/          48 城知识库
├── frontend/             Vue 3 前端
├── tests/                pytest 测试
└── docs/                 SDD 与设计文档
```
