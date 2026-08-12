# 1688 图片搜索纯协议客户端

## 1. 功能

本项目在 Linux 上使用 HTTP 协议完成 1688 图片搜索，不启动浏览器实例。传输层使用 `curl_cffi` 模拟 Chrome 的 TLS、JA3 和 HTTP/2 指纹，不使用普通 Python TLS 裸请求。

执行链路：

1. 从 Cookie 文件建立登录会话。
2. 读取本地图片并编码为 Base64。
3. 调用图片上传接口取得 `imageId`。
4. 请求首屏图搜结果。
5. 轮询首屏结果直至图片处理完成。
6. 按 `offerId` 去重并输出排序最前的 3 个商品。

## 2. 环境

- Python 3.10 或更高版本
- Linux 或其他可运行 Python 的系统
- 有效的 1688 登录 Cookie

安装依赖：

```bash
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -r requirements.txt
```

## 3. Cookie 导出

### 3.1 推荐格式

推荐使用浏览器 Cookie 管理器导出完整 Cookie 数组，结构参考 `config/cookies.example.json`。

每条记录支持以下字段：

| 字段       | 必需 | 说明                      |
| ---------- | ---: | ------------------------- |
| `name`     |   是 | Cookie 名称               |
| `value`    |   是 | Cookie 值                 |
| `domain`   |   否 | 默认 `.1688.com`          |
| `path`     |   否 | 默认 `/`                  |
| `expires`  |   否 | 秒或毫秒时间戳            |
| `secure`   |   否 | 默认 `true`               |
| `httpOnly` |   否 | 作为 Cookie 附加属性保留  |
| `sameSite` |   否 | `None`、`Lax` 或 `Strict` |

文件根节点可为：

- Cookie 数组。
- `{"cookies": [...]}` 包装对象。
- Cookie 名称到值的对象。
- 原始 `name=value; name2=value2` 字符串。

客户端至少需要：

- `_m_h5_tk`
- `_m_h5_tk_enc`
- `cookie1` 或 `cookie2`

还应保留全部登录态和风控相关字段，例如 `cna`、`cookie17`、`sgcookie`、`t`、`_tb_token_`、`isg`、`tfstk`、`x5sec`。上传接口可能在缺少账号 Cookie 时仍然成功，但商品搜索会返回空结果，因此客户端会在启动时检查 `cookie1/cookie2`。

### 3.2 油猴脚本

安装 `userscripts/export-1688-cookies.user.js` 后：

1. 使用支持 `GM_cookie` 的最新版 Tampermonkey。
2. 登录 1688 并刷新图搜页面。
3. 执行“配置远程 Cookie API”。
4. 输入 HTTPS API 服务地址和 `COOKIE_UPLOAD_API_KEY`。
5. 执行“上传完整 Cookie 到 API”。

脚本通过 `GM_cookie.list()` 读取包含 `HttpOnly` 的完整 Cookie，并使用 `X-API-Key` 请求头上传到 `/api/v1/cookies`。服务地址和上传密钥只保存在 Tampermonkey 存储中。菜单中的“下载完整 Cookie JSON 备份”可用于人工备份。

## 4. Linux 安全设置

Cookie 等同于账号登录凭据。上传后设置文件权限：

```bash
mkdir -p secrets outputs
chmod 700 secrets outputs
chmod 600 secrets/1688-cookies.json
```

不要将 Cookie 文件、原始响应或结果文件提交到版本库。

## 5. 运行

基本命令：

```bash
PYTHONPATH=src python3 src/search_1688.py \
  --cookie-file secrets/1688-cookies.json \
  --image input/product.jpg \
  --output outputs/result.json
```

仅输出头部 3 个商品并排除广告：

```bash
PYTHONPATH=src python3 src/search_1688.py \
  --cookie-file secrets/1688-cookies.json \
  --image input/product.jpg \
  --limit 3 \
  --interval 1.0 \
  --exclude-ads \
  --output outputs/result.json
```

保存原始 MTOP 响应：

```bash
PYTHONPATH=src python3 src/search_1688.py \
  --cookie-file secrets/1688-cookies.json \
  --image input/product.jpg \
  --raw-response-dir outputs/raw \
  --output outputs/result.json
```

覆盖或补充图搜业务参数：

```bash
PYTHONPATH=src python3 src/search_1688.py \
  --cookie-file secrets/1688-cookies.json \
  --image input/product.jpg \
  --extra-params '{"sortType":"default"}'
```

## 6. 输出

结果 JSON 包含：

- `image_id`
- `session_id`
- `request_id`
- `pvid`
- `trace_id`
- `pages_requested`
- `found`
- `stop_reason`
- `product_count`
- `products`

商品字段：

- `offer_id`
- `link_url`
- `image_url`
- `title`
- `price`
- `sale_quantity`
- `seller_login_id`
- `seller_name`
- `is_ad`

## 7. 首屏等待与输出限制

客户端只请求首屏，不请求第 2 页及后续分页：

- 上传成功后最多重试 `--ready-retries` 次。
- 每次间隔由 `--interval` 控制。
- 首屏就绪后按原始排序取前 `--limit` 条，默认 3 条。
- 使用 `--exclude-ads` 时先排除广告，再取前 3 条非广告商品。

## 8. 错误与退出码

| 退出码 | 含义                           |
| -----: | ------------------------------ |
|    `0` | 成功                           |
|    `2` | 协议、响应结构、图片或参数错误 |
|    `3` | 登录 Cookie 无效               |
|    `4` | MTOP Token 无效且无法自动刷新  |
|    `5` | 验证码或风控响应               |
|    `6` | 服务端限流                     |
|  `130` | 用户中止                       |

遇到退出码 `3`、`4` 或 `5` 时：

1. 在本机浏览器打开 1688。
2. 完成登录或页面要求的验证。
3. 刷新图搜页面，使 `_m_h5_tk` 更新。
4. 重新导出完整 Cookie。
5. 覆盖 Linux 上的 Cookie 文件后重试。

## 9. 测试

运行离线测试：

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_*.py' -v
```

离线测试不调用 1688。端到端验证必须提供有效 Cookie 和测试图片。

## 10. 协议参数

当前实现使用：

- API：`mtop.relationrecommend.wirelessrecommend.recommend`
- 版本：`2.0`
- `appKey=12574478`
- `appId=32517`
- 上传方法：`uploadBase64WithRequest`
- 首屏方法：`getImageSearchPreResult`
- 分页方法：`imageOfferSearchService`
- 搜索场景：`pcImageSearch`
- 应用名：`pctusou`

MTOP H5 签名规则：

```text
md5(token + "&" + timestamp_ms + "&" + appKey + "&" + data_json)
```

其中 `token` 为 `_m_h5_tk` 下划线前的部分，`data_json` 必须与最终提交内容完全一致。

## 11. API 服务

查询接口的完整调用文档见 [`docs/search-api.md`](docs/search-api.md)。

### 11.1 架构

API 使用 FastAPI、SQLite 和 4 个后台 Worker：

- Cookie 加密保存在 SQLite。
- 搜索任务持久化为 `queued`、`running`、`succeeded` 或 `failed`。
- 同时最多执行 4 个任务，超出的请求排队。
- 每个任务创建独立的 Chrome 指纹会话。
- 服务重启后，未完成的 `running` 任务恢复为 `queued`。

必须使用单个 Uvicorn 进程。不要配置多个 Uvicorn 或 Gunicorn Worker。

### 11.2 初始化配置

安装依赖后执行：

```bash
python3 src/init_env.py
```

该命令在项目根目录生成 `.env`，并自动创建三个安全随机值：

| 变量                    | 用途                   | 使用位置                  |
| ----------------------- | ---------------------- | ------------------------- |
| `COOKIE_UPLOAD_API_KEY` | Cookie 上传接口密钥    | 配置到油猴脚本            |
| `SEARCH_API_KEY`        | 搜索查询接口密钥       | 配置到调用搜索 API 的程序 |
| `COOKIE_ENCRYPTION_KEY` | SQLite Cookie 加密密钥 | 仅由 API 服务使用         |

`.env` 已加入 `.gitignore`，不会提交到 Git。配置模板见 `.env.example`。

查看生成的密钥：

```bash
cat .env
```

注意：

- `COOKIE_UPLOAD_API_KEY` 和 `SEARCH_API_KEY` 必须不同。
- `COOKIE_ENCRYPTION_KEY` 不得配置到油猴或查询调用方。
- 不要随意修改 `COOKIE_ENCRYPTION_KEY`，否则已有数据库中的 Cookie 无法解密。
- 初始化工具默认不覆盖已有 `.env`。确认要重新生成全部密钥时使用 `python3 src/init_env.py --force`。

API 启动时会自动加载项目根目录 `.env`。系统环境变量优先级高于 `.env`，生产环境也可以通过 systemd、Docker 或 Shell 注入配置。

可选参数：

| 变量                       |    默认值 | 说明                        |
| -------------------------- | --------: | --------------------------- |
| `WORKER_COUNT`             |       `4` | 后台 Worker 数，范围 1 到 4 |
| `MAX_QUEUED_TASKS`         |     `100` | 排队和执行中的任务总上限    |
| `TASK_TIMEOUT_SECONDS`     |     `120` | 单任务总超时                |
| `TASK_RETENTION_SECONDS`   |   `86400` | 已完成任务保留秒数          |
| `DOWNLOAD_CONNECT_TIMEOUT` |      `10` | 图片连接超时                |
| `DOWNLOAD_TIMEOUT`         |      `30` | 图片下载总超时              |
| `MAX_IMAGE_BYTES`          | `8388608` | 远程图片最大字节数          |
| `SEARCH_READY_RETRIES`     |       `8` | 首屏就绪重试次数            |
| `SEARCH_READY_INTERVAL`    |     `1.5` | 首屏重试间隔秒数            |

### 11.3 启动

```bash
python3 src/init_env.py
PYTHONPATH=src python3 src/run_api.py
```

基础存活检查：

```bash
curl http://127.0.0.1:8000/health
```

敏感健康状态：

```bash
curl -H "X-API-Key: $SEARCH_API_KEY" \
  http://127.0.0.1:8000/api/v1/health
```

### 11.4 HTTPS 反向代理

油猴远程上传应使用 HTTPS。Nginx 示例：

```nginx
server {
    listen 443 ssl http2;
    server_name search.example.com;

    ssl_certificate /etc/letsencrypt/live/search.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/search.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
    }
}
```

不要直接向公网暴露 Uvicorn 端口。

### 11.5 Cookie 上传 API

```bash
curl -X POST https://search.example.com/api/v1/cookies \
  -H "X-API-Key: $COOKIE_UPLOAD_API_KEY" \
  -H 'Content-Type: application/json' \
  --data-binary @secrets/1688-cookies.json
```

成功响应：

```json
{
  "status": "updated",
  "version": 1,
  "cookie_count": 44,
  "exported_at": "2026-08-12T01:42:22Z",
  "has_cookie1": true,
  "has_cookie2": true,
  "has_m_h5_tk": true,
  "has_m_h5_tk_enc": true
}
```

### 11.6 创建搜索任务

```bash
curl -X POST https://search.example.com/api/v1/search-tasks \
  -H "X-API-Key: $SEARCH_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"image_url":"https://example.com/product.jpg"}'
```

响应：

```json
{
  "task_id": "1d6f595d-34cb-4c49-a022-dfb97e1c7924",
  "status": "queued"
}
```

### 11.7 查询任务

```bash
curl -H "X-API-Key: $SEARCH_API_KEY" \
  https://search.example.com/api/v1/search-tasks/1d6f595d-34cb-4c49-a022-dfb97e1c7924
```

成功响应中的 `products` 最多 3 条：

```json
{
  "task_id": "1d6f595d-34cb-4c49-a022-dfb97e1c7924",
  "status": "succeeded",
  "created_at": 1786490000.0,
  "updated_at": 1786490004.0,
  "started_at": 1786490000.2,
  "finished_at": 1786490004.0,
  "result": {
    "search_page_url": "https://air.1688.com/kapp/1688-search/pc-image-search/?tab=imageSearch&imageId=123&imageIdList=123&spm=...",
    "image_id": "123",
    "found": 697,
    "products": [
      {
        "image": "https://cbu01.alicdn.com/image.jpg",
        "title": "商品标题",
        "price": "128.00",
        "sale_quantity": "311",
        "product_url": "https://detail.1688.com/offer/123.html"
      }
    ]
  },
  "error": null
}
```

### 11.8 安全限制

- Cookie 上传与搜索使用不同 API Key，不能互相替代。
- Cookie 使用 Fernet 加密后写入 SQLite。
- 图片 URL 仅允许 HTTP 和 HTTPS。
- 拒绝环回、私网、链路本地、保留和多播地址。
- 每次重定向都会重新执行地址检查。
- 图片必须具有图片 Content-Type 和有效 JPEG、PNG、GIF 或 WebP 文件签名。
- API Key、Cookie 和 MTOP Token 不写入日志。
