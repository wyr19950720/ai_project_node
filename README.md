# WorkMind Server (Python / FastAPI 版) 运行手册

`server-py/` 是 `server/`（Express + LangChain.js）的等价重写版本，技术栈换成 **Python + FastAPI + LangChain/LangGraph Python SDK**，功能与接口行为完全对齐，可作为原后端的直接替代。原 Express 后端保持不变，两者互不影响。

## 技术栈对照

| 模块 | Express 版 | FastAPI 版 |
|------|------------|------------|
| Web 框架 | Express | FastAPI + Uvicorn |
| 校验 | Zod | Pydantic |
| AI 框架 | LangChain.js / LangGraph.js | LangChain (Python) / LangGraph (Python) |
| 对话模型 | DeepSeek（OpenAI 兼容） | 同上 |
| Embedding | 智谱 AI / OpenAI 兼容(SiliconFlow) | 同上 |
| 向量库 | 内存向量库（余弦相似度） | 同上 |
| 文件上传 | multer | FastAPI `UploadFile` |
| PDF 解析 | pdf-parse | pypdf |
| 限流 | 自实现令牌桶 | 自实现令牌桶 |

## 目录结构

```
server-py/
├── app/
│   ├── main.py              # 入口：中间件、路由注册
│   ├── config.py            # 环境变量统一读取
│   ├── middleware.py        # 限流、日志、prompt 注入检测
│   ├── routes/               # health / chat / knowledge / agent / workflow / erp / prompt / monitor
│   ├── services/              # 对应业务逻辑（model / cache / rag / agent / erp / prompt / workflow）
│   └── utils/                 # 日志、错误处理、SSE 封装
├── requirements.txt
├── Dockerfile
├── .env.example
└── uploads/                  # 知识库文档上传目录（运行时生成，已 gitignore）
```

## 一、本地运行

### 1. 准备 Python 环境

要求 Python 3.11+（已用 3.11.9 验证）。

```bash
cd server-py
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env`，至少填入：

```
DEEPSEEK_API_KEY=sk-xxxx        # 必填，对话模型
ZHIPU_API_KEY=xxxx.xxxx         # 可选，RAG 知识库功能需要（优先于 OPENAI_API_KEY）
OPENAI_API_KEY=sk-xxxx          # 可选，没有智谱 Key 时的 Embedding 备选
PORT=3000
ALLOWED_ORIGINS=http://localhost:5173
```

> 与 Express 版共用同一套环境变量命名，可以直接复制 `server/.env` 内容过来。

### 3. 启动服务

```bash
# 开发模式（代码变更自动重载）
uvicorn app.main:app --reload --port 3000

# 或者直接用 python -m
python -m uvicorn app.main:app --port 3000
# $env:PYTHONIOENCODING="utf-8"
# .\.venv\Scripts\python.exe -m uvicorn app.main:app --port 3000
```

启动成功后会看到：

```
🚀 WorkMind Server (FastAPI) 已启动
   地址: http://localhost:3000
   健康检查: http://localhost:3000/health
```

### 4. 验证

```bash
curl http://localhost:3000/health/live
# {"status":"ok","uptime":xx}

curl http://localhost:3000/api/chat/roles
curl http://localhost:3000/api/agent/tools
curl http://localhost:3000/api/workflow/templates
```

FastAPI 自带交互式文档，浏览器打开 `http://localhost:3000/docs` 可以直接试调所有接口（流式 SSE 接口在 Swagger UI 里看不到实时流，建议用 `curl -N` 或前端联调）。

## 二、配合前端联调

前端默认请求地址在 `frontend/`（Vite 项目），其后端 base URL 通常通过 `.env` 或 `axios` 配置指向 `http://localhost:3000`。FastAPI 版接口路径、SSE 事件格式、JSON 响应结构均与 Express 版一致，**无需修改前端代码**，只要保证同一时刻只有一个后端（Express 或 FastAPI）监听 3000 端口即可：

```bash
# 用 FastAPI 版代替 Express 版
cd server-py && uvicorn app.main:app --port 3000

# 前端单独启动
cd frontend && npm run dev
```

## 三、Docker 运行

```bash
cd server-py
docker build -t workmind-server-py .
docker run -d --name workmind-server-py \
  -p 3000:3000 \
  -e DEEPSEEK_API_KEY=sk-xxxx \
  -e ZHIPU_API_KEY=xxxx.xxxx \
  -e ALLOWED_ORIGINS=http://localhost:5173 \
  -v $(pwd)/uploads:/app/uploads \
  workmind-server-py
```

容器内置健康检查，命中 `/health/live`。

如果要接入根目录的 `docker-compose.yml`，可以新增一个 service（与现有 `server` 二选一启用）：

```yaml
  server-py:
    build:
      context: ./server-py
      dockerfile: Dockerfile
    container_name: workmind-server-py
    restart: unless-stopped
    ports:
      - "3000:3000"
    environment:
      - DEEPSEEK_API_KEY=${DEEPSEEK_API_KEY}
      - ZHIPU_API_KEY=${ZHIPU_API_KEY}
      - ALLOWED_ORIGINS=http://localhost:5173,https://yourdomain.com
    volumes:
      - ./server-py/uploads:/app/uploads
    networks:
      - workmind-net
```

> 注意：FastAPI 版的 RAG 向量库是纯内存实现（与 Express 版行为一致），并未实际连接 `chromadb` 容器，所以不需要额外起 Chroma 服务。

## 四、接口一览（与 Express 版一致）

| 路由前缀 | 功能 |
|---------|------|
| `GET /health`、`/health/live` | 健康检查 |
| `/api/chat/*` | 流式对话、会话管理、用户画像、角色预设 |
| `/api/knowledge/*` | 文档上传/列表/删除、RAG 流式问答 |
| `/api/agent/*` | ReAct Agent 任务执行（流式）、工具列表、示例任务 |
| `/api/workflow/*` | 4 个内置工作流（周报/会议纪要/邮件润色/PRD），支持人工审核暂停/恢复 |
| `/api/erp/*` | 自然语言转结构化表单、Multi-Agent 审批流 |
| `/api/prompt/*` | Prompt 单测(流式)、A/B 测试、模板管理 |
| `/api/monitor/*` | 用量看板：调用统计、Token、成本、缓存命中率 |

所有 SSE 流式接口的事件格式均为：

```
event: <type>
data: <json>

```

错误统一返回：

```json
{ "error": { "code": "...", "message": "...", "retryable": false } }
```

## 五、常见问题

**Q: 启动时报错 `❌ 缺少 DEEPSEEK_API_KEY`？**
`.env` 里没填 `DEEPSEEK_API_KEY`，对话/Agent/工作流/ERP 等所有调用大模型的功能都依赖它，必须配置。

**Q: 调用 `/api/chat/stream` 等接口返回 401 / "服务配置错误"？**
说明 DeepSeek（或智谱）API Key 无效/过期，可以用以下命令直接验证 Key 是否可用：

```bash
curl https://api.deepseek.com/v1/chat/completions \
  -H "Authorization: Bearer $DEEPSEEK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"deepseek-chat","messages":[{"role":"user","content":"hi"}]}'
```

**Q: 上传 PDF 知识库文档失败？**
确认已安装 `pypdf`（在 `requirements.txt` 中），且文件后缀是 `.txt` / `.md` / `.pdf`，单文件不超过 10MB。

**Q: 想同时跑 Express 版和 FastAPI 版做对比？**
改一下端口即可，二者代码完全独立、互不依赖：

```bash
# Express 版仍跑 3000
cd server && npm run dev

# FastAPI 版换个端口
cd server-py && uvicorn app.main:app --port 3001
```
