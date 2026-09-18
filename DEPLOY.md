# 小爱课程表 · 教务助手 — 部署与内网穿透指南

## 零、凭证配置（所有部署方式都必须先做）

```bash
cp .env.example .env
# 编辑 .env，填入 XIAOMI_CLIENT_ID 与 XIAOMI_CLIENT_SECRET
```

`docker-compose.yml` 会从 `.env` 自动读取这两个变量并注入容器。
若未配置，`docker compose up` 会**直接报错中止**并提示变量名，避免起了一个登不上的服务。

校验配置是否生效：访问 `/api/config`，确认 `has_app_credentials` 为 `true`。

详见 `环境变量与凭证配置.md`。

## Docker 部署

```bash
# 构建镜像
docker-compose build

# 启动服务
docker-compose up -d

# 查看日志
docker-compose logs -f

# 停止服务
docker-compose down
```

访问地址：`http://localhost:8080`

## 本地启动

```bash
# 使用启动脚本（自动检查依赖）
run.bat

# 或手动启动
pip install -r requirements.txt
python app.py
```

## 内网穿透

如果需要从外网访问（如手机上使用），可以通过以下方式：

### 方案一：ngrok（推荐，最简单）

1. 注册 ngrok 账号：https://ngrok.com/signup
2. 下载并安装 ngrok
3. 运行：

```bash
ngrok http 8080
```

4. ngrok 会生成一个公网地址，如 `https://xxxx.ngrok.io`

### 方案二：frp（自建服务器）

1. 准备一台有公网 IP 的服务器
2. 服务端配置 `frps.toml`：

```toml
bindPort = 7000
auth.token = "your-secret-token"
```

3. 客户端配置 `frpc.toml`：

```toml
serverAddr = "your-server-ip"
serverPort = 7000
auth.token = "your-secret-token"

[[proxies]]
name = "aischedule"
type = "http"
localIP = "127.0.0.1"
localPort = 8080
customDomains = ["your-domain.com"]
```

4. 启动：`./frpc -c frpc.toml`

### 方案三：Cloudflare Tunnel

```bash
cloudflared tunnel --url http://localhost:8080
```

> ⚠️ 安全提示：通过公网访问时，建议添加 HTTPS 和访问认证。
