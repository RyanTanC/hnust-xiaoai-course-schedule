# 小爱课程表 · HNUST 教务助手

> 把教务系统的课表同步到「小爱课程表 / 小米 AI 课表」的自托管 Web 工具。
> 后端 Flask + 前端 React（Appica UI），本地或 Docker 一键运行，凭证全部走环境变量，绝不入库。

## ✨ 功能

- **多种登录方式**：小米账号密码登录、扫码登录、抓包凭证（`authorization`）导入
- **课表同步**：从教务系统抓取课表，写入小爱课程表，支持多张课表管理
- **逐周浏览与校对**：周视图 / 详情视图切换，按周次查看课程安排
- **作息规则自定义**：单节时长、小/大课间、季节晚修起点、节次时间编辑器
- **智能推荐节次**：按课表实际收敛节次数，一键载入季节预设
- **操作日志**：全流程可视化日志，便于排查同步问题

## 🧱 技术栈

| 层 | 技术 |
| --- | --- |
| 后端 | Flask + waitress（默认端口 `8080`） |
| 前端 | React 19 + TypeScript + Vite + Tailwind CSS 4 + [`@appica/ui-react`](https://www.npmjs.com/package/@appica/ui-react) |
| 认证 | 小米账号 OAuth2（5 步流程）+ Flask session |
| 部署 | 本地脚本 / Docker + docker-compose |

## 🚀 快速开始

### 1. 配置凭证

```bash
cp .env.example .env
# 编辑 .env 填入 XIAOMI_CLIENT_ID / XIAOMI_CLIENT_SECRET
```

> 小米开放平台凭证用于登录流程签发 token。`data/` 目录存放运行时抓到的凭证与课表，已被 `.gitignore` 屏蔽。

### 2. 后端

```bash
python -m venv .venv && .venv\Scripts\activate   # Windows
pip install -r requirements.txt
python app.py            # 访问 http://127.0.0.1:8080
```

### 3. 前端（开发 / 构建）

```bash
cd frontend
npm install
npm run dev              # 开发
npm run build            # 构建产物回填到 Flask 的 static/
```

### 4. Docker

```bash
docker compose up -d
```

## 📁 目录结构

```
.
├── app.py                # Flask 入口与路由
├── core.py               # 小米认证 / 教务抓取 / 课表同步核心
├── requirements.txt
├── static/               # 前端构建产物（由 frontend 打包回填）
├── templates/            # Flask 模板
├── frontend/             # React + Vite 前端源码
│   └── src/{pages,components,lib}
├── data/                 # 运行时凭证与课表（不入库）
├── data.example/         # 凭证模板
├── Dockerfile / docker-compose.yml
└── DEPLOY.md             # 部署说明
```

## 🔒 安全说明

- 所有真实凭证（`XIAOMI_CLIENT_SECRET`、`data/userinfo.txt`、`.secret_key` 等）均通过环境变量或 `data/` 注入，`.gitignore` 已屏蔽，绝不提交。
- 示例文件（`.env.example`、`data.example/userinfo.txt.example`）仅含占位符。
- 请妥善保管 `data/` 目录，它等同于你的账号密码。

## ⚠️ 免责声明

本项目仅供**个人学习与教学课表管理**使用。请遵守小米及所在学校教务系统的相关服务条款，合理、合规使用，勿用于任何滥用或自动化骚扰行为。因使用本项目造成的后果由使用者自行承担。
