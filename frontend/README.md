# 旅行规划智能体 · 前端

基于 Vue 3 + TypeScript + Vite + Pinia 的旅行规划智能体界面。页面采用
「顶部栏 + 历史侧边栏 + 主对话区」三栏布局，所有业务状态集中在 Pinia store，
组件只负责展示与事件转发。接口契约与组件契约见
[`docs/module-sdd/frontend.md`](../docs/module-sdd/frontend.md)。

## 技术栈

- Vue 3.5（`<script setup lang="ts">`）
- TypeScript 5.9 + vue-tsc（SFC 类型检查）
- Pinia 4（集中式状态管理）
- Axios 1.20（统一实例 + 响应拦截器）
- Vite 8

## 常用命令

```bash
npm install          # 安装依赖
npm run dev          # 启动开发服务器（/api 代理到 http://127.0.0.1:8000）
npm run build        # 类型检查 + 生产构建
npm run type-check   # 仅执行 vue-tsc --noEmit
npm run preview      # 预览构建产物
```

开发期请先启动后端（在仓库根目录执行 `uvicorn app.main:app --reload --port 8000`）。

## 目录结构

```text
src/
├── api/
│   └── client.ts          # 统一 axios 实例、错误归一化、四个接口方法
├── config/
│   └── storage.ts         # 本地配置读写、校验与后端地址规范化
├── stores/
│   ├── chat.ts            # 对话 / 行程 / 历史 / 错误 / Toast / 横幅
│   └── config.ts          # 主题 / API Key / 模型 / 后端地址
├── types/
│   └── index.ts           # 前后端共享的 TypeScript 类型
├── components/
│   ├── AppHeader.vue      # 顶部栏
│   ├── BannerBar.vue      # 顶部横幅（错误重试 / 降级提示）
│   ├── HistorySidebar.vue # 历史行程侧边栏
│   ├── ChatPanel.vue      # 主对话区
│   ├── MessageList.vue    # 消息列表（自动滚动）
│   ├── UserMessage.vue    # 用户气泡
│   ├── AssistantMessage.vue # 助手 / 错误气泡
│   ├── TypingIndicator.vue  # 打字指示动画
│   ├── ChatInput.vue      # 输入框、快捷提问、出发日期
│   ├── TripCard.vue       # 行程卡片（折叠日程 / 降级徽标）
│   ├── WeatherForecast.vue # 多日天气
│   ├── CostBreakdown.vue  # 分项花费
│   ├── ToastStack.vue     # Toast 通知队列
│   └── SettingsPanel.vue  # 设置弹层
├── App.vue                # 组装组件并接线事件
├── main.ts                # 创建应用、注册 Pinia
└── style.css              # 全局样式与主题变量
```

## 关键约定

- **后端地址不硬编码**：默认走 Vite 的 `/api` 代理；也可在设置面板或
  `frontend/.env.local` 的 `VITE_API_BASE_URL` 中指定，修改后下一次请求即生效。
- **动作分发只依据后端 `action`**：前端不做关键词意图识别。
- **错误统一处理**：所有异常归一化为 `ApiError`（`business` / `network` / `timeout`）；
  超时只弹 Toast，网络失败额外显示红色横幅并支持重试。
- **降级提示**：`degraded` / `source === 'template'` 时卡片显示「模板降级」徽标，
  顶部黄色横幅 5 秒后自动消失。
- **Toast 上限**：同时最多 5 条，支持手动关闭与自动消失。
- **API Key 仅存本地**：设置面板中的 Key 保存在浏览器 `localStorage`，不会上传后端。
