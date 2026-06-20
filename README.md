# ApiPulse — API 全生命周期管理平台

[![Python](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com/)
[![Vue](https://img.shields.io/badge/Vue-3.4+-4FC08D.svg)](https://vuejs.org/)
[![MongoDB](https://img.shields.io/badge/MongoDB-7-47A248.svg)](https://www.mongodb.com/)
[![Redis](https://img.shields.io/badge/Redis-7-DC382D.svg)](https://redis.io/)
[![Docker](https://img.shields.io/badge/Docker%20Compose-ready-2496ED.svg)](https://docs.docker.com/compose/)
[![License](https://img.shields.io/badge/License-Apache%202.0-D22128.svg)](https://www.apache.org/licenses/LICENSE-2.0)

将 HTTP 流量「资产化」—— 从 HAR 导入或实时抓包出发，通过 AI 自动生成文档、断言与测试场景，经 DAG 引擎编排执行，最终落地为定时巡检与多渠道告警。

---

## 项目定位

以真实 HTTP 流量为唯一事实来源，构建「捕获 → 分析 → 测试 → 监控」全流程闭环：

- **零手工录入** — HAR 文件拖入即自动解析、去重、入库，AI 自动生成文档与断言
- **场景即代码** — DAG 可视化编排测试场景，支持条件分支、循环、数据注入、断言
- **持续巡检** — cron/interval 定时执行，DeepDiff 检测响应变更，多渠道告警路由
- **越用越准** — ReMe 记忆系统跨接口学习字段模式与断言规则

---

## 核心功能

| 模块 | 说明 |
|------|------|
| **HAR 导入** | ijson 流式解析，静态资源过滤、SHA256 去重、批量写入 MongoDB，自动推送 AI 分析队列 |
| **AI 分析引擎** | 4 Worker 并行消费队列，生成 API 文档、22 种断言规则、DAG 测试场景。支持 OpenAI / DeepSeek / Ollama |
| **AI Chat** | 对话式 API 分析，流式 WebSocket 推送，支持多轮交互 |
| **DAG 场景引擎** | Kahn 拓扑排序分波次并发执行，条件分支（10 种操作符）、循环执行、数据工厂注入 |
| **定时巡检** | APScheduler cron/interval 双模式，DeepDiff 哈希预判 + 全量 diff，告警去重与静默期 |
| **多渠道路由** | 钉钉 / 企业微信 / Slack / 自定义 Webhook，格式自适应 |
| **数据工厂** | 80+ Faker 生成器（中英文），支持 null/empty/invalid 异常值注入，递归嵌套生成 |
| **ReMe 记忆系统** | 多维检索评分，Prompt 预算控制，LLM 提取与去重合并，用户反馈置信度调整 |
| **差异比对** | 导入时自动检测 API 字段变化，AI 评估差异，支持确认/修复/忽略 |
| **Mock 服务** | 基于 API DSL 自动生成 Mock 端点，支持动态响应模板 |
| **SQL 数据源** | 支持 PostgreSQL / MySQL 作为数据源，SQL 运行时执行与断言 |
| **生成版本管理** | AI 生成结果的版本追踪、对比与回溯 |
| **多租户 RBAC** | admin / editor / viewer / monitor_admin 四角色，JWT + bcrypt + API Key 认证 |

---

## 架构概览

```
┌─────────────────────────────────────────────────────────┐
│                    Frontend (Vue 3)                      │
│   Element Plus · ECharts · VueFlow · Pinia · i18n       │
│                     Nginx :3000                          │
└──────────────────────┬──────────────────────────────────┘
                       │ /api  →  Backend :8000
                       │ /ws   →  WebSocket
┌──────────────────────┴──────────────────────────────────┐
│                 Backend (FastAPI)                        │
│                                                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐               │
│  │ai_analyzer│  │dag_engine│  │ monitor  │               │
│  │ 4 Workers │  │ 22 断言  │  │ cron/间隔│               │
│  │ DLQ 死信  │  │ 条件/循环 │  │ DeepDiff │               │
│  └─────┬─────┘  └────┬─────┘  └────┬─────┘               │
│        │             │             │                      │
│  ┌─────┴─────┐  ┌────┴─────┐  ┌────┴─────┐               │
│  │har_parser │  │  data    │  │knowledge  │              │
│  │ ijson流式 │  │ factory  │  │  ReMe    │               │
│  └───────────┘  └──────────┘  └──────────┘               │
│                                                          │
│  API Routes (27 modules)                                 │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────┴──────────────────────────────────┐
│          MongoDB 7  ·  Redis 7  ·  MinIO                │
└─────────────────────────────────────────────────────────┘
```

---

## 快速开始

### 环境要求

- **Docker** ≥ 24.0 + **Docker Compose** ≥ 2.20
- 本地运行：Python 3.12+、Node.js 20+、MongoDB 7、Redis 7
- LLM 服务：OpenAI / DeepSeek / Ollama

### Docker Compose 部署

```bash
git clone <repo-url> && cd ApiPulse

cp backend/.env.example backend/.env
# 编辑 backend/.env，填入 OPENAI_API_KEY 等必要配置

./start.sh --docker
# 或 docker compose up -d

# 前端: http://localhost:3000
# API 文档: http://localhost:8000/docs
# MinIO: http://localhost:9001 (minioadmin / minioadmin)
```

首次启动自动初始化数据库索引并创建默认管理员 `admin / admin123`。

### 本地开发

```bash
# 仅启动基础设施
docker compose up -d mongo redis minio

# 后端
pip install -r backend/requirements.txt
cp backend/.env.example backend/.env
python main.py

# 前端
cd frontend && npm install && npm run dev  # :5173 → :8000
```

---

## 使用指南

### 1. 创建项目 → 2. 导入 HAR → 3. AI 分析 → 4. 编排场景 → 5. 定时巡检

**导入 HAR**：浏览器 DevTools → Network → Export HAR，或抓包工具导出，上传后系统自动流式解析入库。

**AI 分析**：四个 Worker 并行处理，WebSocket 实时推送状态：

| 队列 | 产出 |
|------|------|
| `queue:ai_analyze` | 编排入口，分发到文档/断言/场景队列 |
| `queue:ai_doc` | API 文档（summary、参数表、响应字段、标签） |
| `queue:ai_asserts` | 断言规则（22 种操作符） |
| `queue:ai_scenario` | DAG 测试场景（single/multi/complex 三种策略） |

**场景编排**：VueFlow DAG 画布拖拽 API 节点，配置变量提取（JSONPath）、条件分支、循环、数据工厂注入。

**定时巡检**：创建巡检任务 → 选择目标 → 配置 cron/interval → 设置告警渠道 → Dashboard 查看趋势。

---

## 技术栈

### 后端

| 组件 | 技术 |
|------|------|
| Web 框架 | FastAPI 0.115+ |
| 异步驱动 | Motor (MongoDB) + redis.asyncio |
| 任务调度 | APScheduler 3.10+ |
| AI 集成 | OpenAI SDK（兼容 OpenAI / DeepSeek / Ollama） |
| 数据校验 | Pydantic v2 + pydantic-settings |
| 流式解析 | ijson 3.2+ |
| 差异检测 | DeepDiff 7.0+ |
| 数据生成 | Faker 25+ (zh_CN + en_US) |
| 认证 | bcrypt 4.1+ + PyJWT 2.9+ |
| SQL 数据源 | asyncpg + aiomysql |
| 日志 | Loguru |
| 对象存储 | MinIO |

### 前端

| 组件 | 技术 |
|------|------|
| 框架 | Vue 3.4+ (Composition API) |
| UI 库 | Element Plus |
| 可视化 | ECharts 5 + VueFlow |
| 状态管理 | Pinia |
| 国际化 | vue-i18n (zh-CN / en) |
| 构建 | Vite 5 |

### 基础设施

| 服务 | 版本 |
|------|------|
| MongoDB | 7 |
| Redis | 7-alpine |
| MinIO | RELEASE.2024-11-07 |
| Nginx | alpine |

---

## 项目结构

```
ApiPulse/
├── main.py                        # FastAPI 应用入口
├── docker-compose.yml             # Docker Compose 编排
├── start.sh                       # 一键启动脚本
├── api/
│   ├── routes.py                  # 主路由、中间件、WS、生命周期
│   ├── state.py                   # 共享服务实例注入
│   ├── ws_manager.py              # WebSocket 连接管理
│   ├── deps.py                    # 依赖注入（认证/鉴权）
│   └── routers/                   # 27 个路由模块
│       ├── auth.py                # 登录/注册/用户管理
│       ├── apis.py                # API 资产 CRUD + 运行
│       ├── ai_chat.py             # AI 对话分析
│       ├── har.py                 # HAR 上传/解析
│       ├── scenarios.py           # 场景 CRUD + 执行
│       ├── monitors.py            # 巡检任务 CRUD
│       ├── executions.py          # 执行记录查询
│       ├── data_factory.py        # 数据模板 CRUD + 造数
│       ├── generations.py         # 生成版本管理
│       ├── mock_services.py       # Mock 服务管理
│       ├── knowledge.py           # 知识条目管理
│       ├── environments.py        # 执行环境管理
│       ├── capture.py             # 实时抓包
│       ├── traffic.py             # 流量数据端点
│       ├── database_services.py   # SQL 数据源管理
│       ├── projects.py            # 项目管理
│       ├── prompts.py             # Prompt 模板管理
│       ├── alert_channels.py      # 告警渠道配置
│       ├── alerts.py              # 告警记录查询
│       ├── stats.py               # Dashboard 统计
│       ├── dlq.py                 # 死信队列管理
│       ├── diff_alerts.py         # 差异比对记录
│       ├── settings_llm.py        # LLM 配置
│       ├── settings_general.py    # 通用设置
│       ├── ai_operation_logs.py   # AI 操作日志
│       ├── system.py              # 系统健康检查
│       └── audit.py               # 审计日志
├── ai_analyzer/
│   ├── analyzer.py                # AI 分析服务（4 Worker + 状态机）
│   ├── alert_analyzer.py          # 告警 AI 分析
│   ├── diff_evaluator.py          # 差异比对 AI 评估
│   ├── failure_diagnoser.py       # 失败原因诊断
│   └── utils.py                   # 共享工具
├── dag_engine/
│   └── engine.py                  # DAG 执行引擎
├── monitor/
│   └── monitor.py                 # 巡检调度服务
├── har_parser/
│   ├── parser.py                  # HAR 流式解析与入库
│   └── quarantine.py              # 隔离文件处理
├── data_factory/
│   └── factory.py                 # 数据工厂（80+ Faker 生成器）
├── knowledge/
│   └── service.py                 # ReMe 记忆系统
├── mitmproxy_capture/
│   └── capture_addon.py           # mitmproxy 实时抓包插件
├── services/                      # 14 个业务服务
│   ├── api_service.py             # API 资产业务逻辑
│   ├── scenario_service.py        # 场景编排服务
│   ├── stats_service.py           # 统计聚合服务
│   ├── diff_service.py            # 差异比对检测
│   ├── auth_service.py            # JWT + 用户管理
│   ├── mock_service.py            # Mock 服务引擎
│   ├── sql_runtime_service.py     # SQL 运行时执行
│   ├── ai_job_service.py          # AI 任务调度
│   ├── llm_config_service.py      # LLM 配置管理
│   ├── capture_service.py         # 抓包数据服务
│   ├── curl_parser.py             # cURL 命令解析
│   ├── structured_output_service.py # 结构化输出
│   ├── ai_operation_log_service.py  # AI 操作日志
│   └── audit_service.py           # 审计服务
├── models/
│   ├── dsl.py                     # 核心 DSL 模型
│   ├── user.py                    # 用户与 RBAC 模型
│   ├── knowledge.py               # 知识条目模型
│   ├── project.py                 # 项目模型
│   ├── generation_version.py      # 生成版本模型
│   ├── mock_service.py            # Mock 服务模型
│   ├── prompt_template.py         # Prompt 模板模型
│   ├── database_service.py        # 数据库服务模型
│   ├── audit.py                   # 审计日志模型
│   └── ai_operation_log.py        # AI 操作日志模型
├── config/
│   ├── settings.py                # pydantic-settings 配置
│   ├── database.py                # MongoDB / Redis 连接
│   └── init_db.py                 # 索引初始化 + 数据迁移
├── backend/
│   ├── Dockerfile                 # 后端 Docker 镜像
│   ├── requirements.txt           # Python 依赖
│   └── .env.example               # 环境变量模板
├── frontend/
│   ├── Dockerfile                 # 前端 Docker 镜像
│   ├── nginx.conf                 # Nginx 配置
│   ├── package.json               # 前端依赖
│   ├── vite.config.js             # Vite 配置
│   └── src/
│       ├── views/                 # 14 个页面模块
│       ├── components/            # 通用组件
│       ├── stores/                # Pinia 状态管理
│       ├── composables/           # 组合式函数
│       ├── api/                   # API 请求封装
│       ├── locales/               # 国际化
│       ├── router/                # 路由配置
│       ├── types/                 # TypeScript 类型
│       └── utils/                 # 工具函数
├── scripts/                       # 辅助脚本
├── docs/                          # 设计文档
└── tests/                         # 测试（22 个测试文件）
```

---

## 配置说明

完整模板见 `backend/.env.example`。

### 必填

| 变量 | 说明 | 示例 |
|------|------|------|
| `OPENAI_API_KEY` | LLM API Key | `sk-xxx` |
| `OPENAI_BASE_URL` | LLM API 地址 | `https://api.openai.com/v1` |
| `OPENAI_MODEL` | 模型名称 | `gpt-4o-mini` |
| `MONGO_URI` | MongoDB 连接串 | `mongodb://localhost:27017` |
| `REDIS_URL` | Redis 连接串 | `redis://localhost:6379/0` |

### 可选

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `OPENAI_TEMPERATURE` | LLM 温度 | `0.1` |
| `OPENAI_MAX_TOKENS` | 默认 max_tokens | `4096` |
| `OPENAI_MAX_TOKENS_DOC` | 文档生成 | `3000` |
| `OPENAI_MAX_TOKENS_ASSERTS` | 断言生成 | `3000` |
| `OPENAI_MAX_TOKENS_SCENARIO` | 场景生成 | `4096` |
| `OPENAI_MAX_TOKENS_DIFF_EVAL` | 差异评估 | `2048` |
| `LLM_STREAM_ENABLED` | AI 流式输出 | `true` |
| `CORS_ORIGINS` | 跨域白名单 | `*` |
| `APP_ENV` | 运行环境 | `development` |
| `LOG_LEVEL` | 日志级别 | `INFO` |
| `JWT_SECRET` | JWT 签名密钥 | 默认值 |
| `SQL_SECRET_KEY` | SQL 密码加密密钥 | 空 |
| `API_KEY` | API Key 认证 | 空（不启用） |
| `RATE_LIMIT_ENABLED` | 速率限制 | `true` |
| `RATE_LIMIT_MAX_REQUESTS` | 窗口内最大请求数 | `120` |
| `RATE_LIMIT_WINDOW_S` | 窗口大小（秒） | `60` |

### Ollama 本地模型

```env
OPENAI_API_KEY=ollama
OPENAI_BASE_URL=http://localhost:11434/v1
OPENAI_MODEL=qwen2.5:7b
```

---

## 开发指南

### 运行测试

```bash
pip install pytest pytest-asyncio
pytest tests/ -v
```

### 添加新路由

在 `api/routers/` 下创建模块，遵循现有模式，然后在 `api/routes.py` 中 `include_router`。

```python
from fastapi import APIRouter, Depends
from api.deps import require_auth

router = APIRouter(prefix="/xxx", tags=["Xxx"])

@router.get("/")
async def list_items(user=Depends(require_auth("xxx:read"))):
    ...
```

### 中间件栈

1. `request_id_middleware` — 注入 X-Request-ID
2. `jwt_auth_middleware` — 提取 Bearer token → `request.state.user`
3. `rate_limit_middleware` — 固定窗口计数器，Redis 故障时放行

### 数据库索引

应用启动时由 `config/init_db.py` 自动创建。新增查询模式时在 `init_indexes()` 中添加对应索引。

---

## Roadmap

- [ ] OpenAPI 3.x 导入导出
- [ ] `stats_overview` `$facet` 聚合优化（单次查询替代多次独立查询）
- [ ] `httpx.AsyncClient` 连接池复用
- [ ] 场景执行结果 diff 可视化对比
- [ ] 批量操作（批量删除/批量 AI 分析）
