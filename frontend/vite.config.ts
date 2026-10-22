import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

/** 后端开发服务器地址（uvicorn 默认监听端口，见 main.py 的 DEFAULT_API_PORT）。 */
const BACKEND_ORIGIN = 'http://localhost:8000'

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    // 把 /api 转发到后端：浏览器看到的是同源请求，开发期完全不涉及 CORS。
    // 前端源码里因此没有任何后端地址字面量（client.js 的 BASE_URL 留空）。
    proxy: {
      '/api': {
        target: BACKEND_ORIGIN,
        changeOrigin: true,
      },
    },
  },
})
