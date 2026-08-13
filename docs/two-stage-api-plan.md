# 两阶段图片搜索 API 改造计划

## 1. 目标

将现有单任务图片搜索流程拆分为两个独立异步阶段：

1. 上传任务：下载远程图片并上传到 1688，尽快返回 `image_id` 和官方图片搜索页 URL。
2. 商品任务：调用方取得上传结果后，按需显式创建商品查询任务，后台查询 Top 1～3 商品。

同时增加两类任务的协作式取消能力。

## 2. 已确认决策

- [x] 删除旧 `/api/v1/search-tasks` 创建与查询接口。
- [x] 新增上传任务与商品任务两套独立接口。
- [x] 商品任务由调用方显式创建，不自动创建。
- [x] 商品任务请求只提交 `upload_task_id`，不接受调用方直接提交 `image_id`。
- [x] 服务端从上传任务读取 `image_id`、搜索页 URL 和绑定的 Cookie 版本。
- [x] 上传任务并发数为 `2`。
- [x] 商品任务并发数为 `2`。
- [x] 同一上传任务重复提交商品查询时，每次创建新的商品任务。
- [x] 商品查询结束仍没有商品时，商品任务标记为 `failed`。
- [x] 排队任务取消后立即变为 `cancelled`。
- [x] 运行中任务采用协作式取消：当前阻塞 HTTP 请求结束后停止后续步骤并变为 `cancelled`。
- [x] 商品任务取消不影响上传任务、`image_id` 和搜索页 URL。
- [x] 上传任务取消后禁止创建商品任务。

## 3. 已确认故障诊断

服务器真实测试结果：

- IPv4 小型 MTOP POST：约 `1.23s`。
- IPv6 小型 MTOP POST：约 `1.50s`。
- IPv4 约 `200KB` 大型 MTOP POST：约 `25.01s`。
- 原默认 `SEARCH_HTTP_TIMEOUT=30`。
- 原 Worker 并发为 `4`。
- 失败任务执行约 `93s`，对应 `3 × 30s` 网络请求。
- 错误为 `curl (28)`，30 秒内收到 `0 bytes`。

结论：

- DNS、IPv4、IPv6、TLS、HTTP/2 和 MTOP 域名均可访问。
- 主要问题是大 POST 响应接近 30 秒，4 路并发后超过超时。
- 故障不是 `_m_h5_tk` 缺失，也不是商品 JSON 解析错误。
- 两阶段改造后上传并发固定为 2，并将 MTOP HTTP 超时提高至 90 秒。

## 4. 新接口

所有接口继续使用 `SEARCH_API_KEY`，请求头为：

```http
X-API-Key: SEARCH_API_KEY
```

### 4.1 创建上传任务

```http
POST /api/v1/upload-tasks
Content-Type: application/json
```

请求：

```json
{
  "image_url": "https://example.com/product.jpg"
}
```

响应：

```json
{
  "task_id": "uuid",
  "status": "queued"
}
```

### 4.2 查询上传任务

```http
GET /api/v1/upload-tasks/{task_id}
```

成功响应：

```json
{
  "task_id": "uuid",
  "status": "succeeded",
  "created_at": 0,
  "updated_at": 0,
  "started_at": 0,
  "finished_at": 0,
  "result": {
    "image_id": "1688-image-id",
    "search_page_url": "https://air.1688.com/kapp/1688-search/pc-image-search/?tab=imageSearch&imageId=...&imageIdList=...&spm=..."
  },
  "error": null
}
```

允许状态：

- `queued`
- `running`
- `succeeded`
- `failed`
- `cancelled`

### 4.3 取消上传任务

```http
DELETE /api/v1/upload-tasks/{task_id}
```

语义：

- `queued`：立即取消。
- `running`：设置 `cancel_requested=1`，当前 HTTP 请求结束后取消。
- 终态重复取消：幂等返回当前状态。
- `cancelled` 上传任务不能创建商品任务。

### 4.4 创建商品任务

```http
POST /api/v1/product-tasks
Content-Type: application/json
```

请求：

```json
{
  "upload_task_id": "uuid"
}
```

约束：

- 上传任务必须存在。
- 上传任务必须为 `succeeded`。
- 上传任务必须具有 `image_id`。
- 每次请求都创建新的商品任务，不复用旧任务。

响应：

```json
{
  "task_id": "uuid",
  "upload_task_id": "uuid",
  "status": "queued"
}
```

### 4.5 查询商品任务

```http
GET /api/v1/product-tasks/{task_id}
```

成功响应：

```json
{
  "task_id": "uuid",
  "upload_task_id": "uuid",
  "status": "succeeded",
  "created_at": 0,
  "updated_at": 0,
  "started_at": 0,
  "finished_at": 0,
  "result": {
    "image_id": "1688-image-id",
    "search_page_url": "https://air.1688.com/...",
    "found": 698,
    "products": [
      {
        "image": "https://...",
        "title": "商品标题",
        "price": "128.00",
        "sale_quantity": "9",
        "product_url": "https://detail.1688.com/offer/..."
      }
    ]
  },
  "error": null
}
```

无商品时：

```json
{
  "status": "failed",
  "result": null,
  "error": {
    "code": "PRODUCTS_NOT_FOUND",
    "message": "图片搜索未返回商品"
  }
}
```

### 4.6 取消商品任务

```http
DELETE /api/v1/product-tasks/{task_id}
```

语义与上传任务一致。取消商品任务不修改关联上传任务。

## 5. SQLite 设计

### 5.1 `upload_tasks`

建议字段：

```text
task_id TEXT PRIMARY KEY
status TEXT NOT NULL
image_url TEXT NOT NULL
cookie_version INTEGER
image_id TEXT
search_page_url TEXT
cancel_requested INTEGER NOT NULL DEFAULT 0
error_code TEXT
error_message TEXT
created_at REAL NOT NULL
updated_at REAL NOT NULL
started_at REAL
finished_at REAL
```

状态约束：

```text
queued, running, succeeded, failed, cancelled
```

### 5.2 `product_tasks`

建议字段：

```text
task_id TEXT PRIMARY KEY
upload_task_id TEXT NOT NULL
status TEXT NOT NULL
cookie_version INTEGER NOT NULL
result_json TEXT
cancel_requested INTEGER NOT NULL DEFAULT 0
error_code TEXT
error_message TEXT
created_at REAL NOT NULL
updated_at REAL NOT NULL
started_at REAL
finished_at REAL
FOREIGN KEY(upload_task_id) REFERENCES upload_tasks(task_id)
```

状态约束：

```text
queued, running, succeeded, failed, cancelled
```

### 5.3 重启恢复

服务初始化时：

- `running` 且 `cancel_requested=0`：恢复为 `queued`。
- `running` 且 `cancel_requested=1`：恢复为 `cancelled`。
- 两类任务分别恢复。

### 5.4 旧表迁移

当前旧表为 `search_tasks`。

已确定并实施：

- [x] 方案 B：升级时直接删除旧表和旧任务。
- [ ] 方案 A：重命名为 `legacy_search_tasks`，不采用。
- [ ] 方案 C：迁移旧任务，不采用。

## 6. Worker 设计

### 6.1 `UploadWorkerPool`

职责：

1. 原子领取 `upload_tasks.status='queued'` 的任务。
2. 绑定领取时的活动 Cookie 版本。
3. 检查取消标记。
4. 安全下载远程图片。
5. 再次检查取消标记。
6. 创建独立 `curl_cffi` Chrome 指纹 Session。
7. 只调用图片上传接口。
8. 取得 `image_id`。
9. 生成并保存 `search_page_url`。
10. 将任务更新为 `succeeded`。

并发：

```text
UPLOAD_WORKER_COUNT=2
```

### 6.2 `ProductWorkerPool`

职责：

1. 原子领取 `product_tasks.status='queued'` 的任务。
2. 从关联上传任务读取 `image_id` 和搜索页 URL。
3. 加载上传任务绑定的 Cookie 版本。
4. 检查取消标记。
5. 创建独立 `curl_cffi` Chrome 指纹 Session。
6. 轮询首屏商品结果。
7. 返回 Top 1～3 商品。
8. 无商品时以 `PRODUCTS_NOT_FOUND` 标记失败。

并发：

```text
PRODUCT_WORKER_COUNT=2
```

### 6.3 协作式取消

检查点：

- Worker 领取任务后。
- 图片下载完成后。
- 每次 MTOP HTTP 请求返回或失败后。
- 每次网络重试前。
- 每次商品就绪轮询前。
- 写入成功结果前。

限制：

已经在线程中执行的阻塞式 `curl_cffi` 请求不会被立即强制中止。取消最迟在当前单次 HTTP 请求结束或超时后生效。

## 7. ImageSearchClient 拆分

当前 `ImageSearchClient.search_bytes()` 同时执行上传和商品查询。

需要公开独立能力：

```python
upload_image_bytes(content: bytes) -> UploadContext
search_uploaded(upload: UploadContext) -> SearchResult
search_image_id(image_id: str) -> SearchResult
```

要求：

- 上传 Worker 只调用 `upload_image_bytes()`。
- 商品 Worker 按已保存的 `image_id` 查询，不重复上传图片。
- 商品查询结果继续按 `offer_id` 去重。
- Top 1～3 字段保持现有格式。

## 8. 配置调整

新增：

```env
UPLOAD_WORKER_COUNT=2
PRODUCT_WORKER_COUNT=2
UPLOAD_TASK_TIMEOUT_SECONDS=240
PRODUCT_TASK_TIMEOUT_SECONDS=180
```

调整：

```env
SEARCH_HTTP_TIMEOUT=90
SEARCH_NETWORK_RETRIES=1
```

删除或停止使用：

```env
WORKER_COUNT
TASK_TIMEOUT_SECONDS
```

其余队列、图片下载、任务保留配置继续使用。

需要同步更新：

- `.env.example`
- `src/init_env.py`
- `src/api/config.py`
- `README.md`
- `docs/search-api.md`

生产服务器 `.env` 需要手工迁移，不得覆盖已有 `COOKIE_ENCRYPTION_KEY`。

## 9. 错误码

新增或明确：

```text
MTOP_NETWORK_ERROR
UPLOAD_NO_IMAGE_ID
PRODUCTS_NOT_FOUND
TASK_CANCELLED
UPLOAD_TASK_NOT_FOUND
UPLOAD_TASK_NOT_READY
UPLOAD_TASK_CANCELLED
UPLOAD_TASK_FAILED
PRODUCT_TASK_NOT_FOUND
QUEUE_FULL
```

继续保留：

```text
COOKIE_UNAVAILABLE
COOKIE_INVALID
MTOP_TOKEN_INVALID
RISK_CONTROL
RATE_LIMITED
IMAGE_DOWNLOAD_FAILED
SSRF_REJECTED
TASK_TIMEOUT
INTERNAL_ERROR
```

网络异常不得再统一误报为 `PROTOCOL_ERROR`。

## 10. 日志与诊断

当前工作区已修改但尚未提交：

- `src/image_search/mtop.py`
- `src/api/worker.py`

现有未提交修改增加了：

- MTOP API 名称。
- HTTP 方法。
- 当前尝试次数和总尝试次数。
- 请求 JSON 字节数。
- 单次请求耗时。
- HTTP 状态或网络错误类型。
- Worker 失败的错误码和消息。

这些修改必须保留并适配新 Worker。

日志禁止记录：

- Cookie。
- `_m_h5_tk`。
- `_m_h5_tk_enc`。
- MTOP 签名。
- API Key。
- 图片 Base64。
- 完整请求体。

## 11. 健康检查

详细健康接口应返回：

```json
{
  "status": "ok",
  "cookie": {},
  "upload_workers": 2,
  "product_workers": 2,
  "upload_tasks": {
    "queued": 0,
    "running": 0,
    "succeeded": 0,
    "failed": 0,
    "cancelled": 0
  },
  "product_tasks": {
    "queued": 0,
    "running": 0,
    "succeeded": 0,
    "failed": 0,
    "cancelled": 0
  }
}
```

## 12. 自动化测试

需要增加：

- [x] SQLite 新表初始化和旧表迁移。
- [x] 上传任务状态机。
- [x] 商品任务状态机。
- [x] 上传任务成功后立即返回 URL。
- [x] 商品任务只能引用成功上传任务。
- [x] 重复商品任务每次生成新任务 ID。
- [x] 商品结果 Top 1～3 标准化。
- [x] 无商品返回 `PRODUCTS_NOT_FOUND`。
- [x] queued 上传任务立即取消。
- [x] running 上传任务协作式取消。
- [x] queued 商品任务立即取消。
- [x] running 商品任务协作式取消。
- [x] 取消上传任务后禁止创建商品任务。
- [x] 取消商品任务不影响上传任务。
- [x] 服务重启恢复 queued/running/cancel_requested。
- [x] 上传 Worker 并发上限为 2。
- [x] 商品 Worker 并发上限为 2。
- [x] 双 API Key 隔离继续有效。
- [x] 旧 `/api/v1/search-tasks` 返回 404。
- [x] 日志不泄露敏感信息。

## 13. 文档与部署

需要更新：

- `README.md`
- `docs/search-api.md`
- `.env.example`
- 宝塔 Python 项目部署说明
- curl 两阶段调用示例
- Python 两阶段调用示例
- 取消任务示例
- 状态轮询示例

宝塔启动命令保持：

```bash
python /www/wwwroot/1688imagesearch/src/run_api.py
```

宝塔依赖文件保持：

```text
/www/wwwroot/1688imagesearch/requirements-panel.txt
```

## 14. 生产验证

验证顺序：

1. 备份 SQLite 数据库。
2. 拉取代码。
3. 安装依赖。
4. 手工更新 `.env`，不得覆盖 `COOKIE_ENCRYPTION_KEY`。
5. 启动服务并检查数据库迁移日志。
6. 上传最新 Cookie。
7. 创建上传任务。
8. 轮询直至取得 `image_id` 和搜索页 URL。
9. 确认此时尚未自动创建商品任务。
10. 显式创建商品任务。
11. 轮询并取得 Top 1～3 商品。
12. 验证无商品失败。
13. 验证 queued 与 running 取消。
14. 验证上传并发 2 和商品并发 2。
15. 验证服务重启恢复。

## 15. 安全事项

聊天记录中已经暴露旧 `SEARCH_API_KEY`。

上线前必须：

1. 在服务器 `.env` 中生成新的 `SEARCH_API_KEY`。
2. 保持 `COOKIE_ENCRYPTION_KEY` 不变。
3. 重启 API。
4. 更新所有调用方。
5. 验证旧密钥返回 401。
6. 验证新密钥正常使用。

## 16. 实施顺序

- [x] 确定旧 `search_tasks` 表迁移策略。
- [x] 修改 SQLite 表与数据库访问层。
- [x] 拆分图片上传与商品查询客户端能力。
- [x] 实现上传 Worker。
- [x] 实现商品 Worker。
- [x] 实现协作式取消。
- [x] 实现上传任务 API。
- [x] 实现商品任务 API。
- [x] 删除旧搜索任务 API。
- [x] 更新配置和环境变量模板。
- [x] 完善错误分类与诊断日志。
- [x] 更新健康检查。
- [x] 增加自动化测试。
- [x] 更新说明文档。
- [ ] 执行真实端到端验证。
- [ ] 轮换 `SEARCH_API_KEY`。
- [ ] 提交并推送 GitHub。
- [ ] 服务器拉取、迁移和重启。
