# 1688 两阶段图片查询 API 使用说明

## 1. 文档范围

本文档供图片查询 API 调用方使用，说明以下接口：

- 创建、查询和取消上传任务。
- 创建、查询和取消商品任务。
- 查询服务健康状态。

Cookie 上传接口由服务管理员使用，不属于普通查询流程。

## 2. 基础信息

### 2.1 服务地址

示例地址：

```text
https://search.example.com
```

接口前缀：

```text
/api/v1
```

请将示例地址替换为实际部署地址。

### 2.2 数据格式

请求和响应使用 JSON：

```http
Content-Type: application/json; charset=utf-8
```

时间字段为 Unix 秒时间戳，可能包含小数。

### 2.3 鉴权

所有查询接口必须携带搜索密钥：

```http
X-API-Key: your-search-api-key
```

搜索密钥对应服务端环境变量：

```text
SEARCH_API_KEY
```

不要使用 `COOKIE_UPLOAD_API_KEY` 调用查询接口。

鉴权失败响应：

```http
HTTP/1.1 401 Unauthorized
Content-Type: application/json
```

```json
{
  "detail": {
    "code": "INVALID_API_KEY",
    "message": "搜索 API Key 无效"
  }
}
```

## 3. 查询流程

完整查询分为两个阶段：

1. 调用上传任务接口并提交远程图片 URL。
2. 轮询上传任务，直到状态为 `succeeded`。
3. 从上传结果读取 `image_id` 和 `search_page_url`。
4. 使用上传任务 ID 创建商品任务。
5. 轮询商品任务，直到进入终态。
6. 商品任务成功时读取 Top 1～3 商品。

商品任务不会由上传任务自动创建。调用方可以只取得图片上传结果，也可以稍后按需创建一个或多个商品任务。

## 4. 任务状态

上传任务和商品任务使用相同状态集合：

| 状态        | 说明                            | 是否继续轮询 |
| ----------- | ------------------------------- | ------------ |
| `queued`    | 已进入队列，等待 Worker 领取    | 是           |
| `running`   | Worker 正在执行任务             | 是           |
| `succeeded` | 任务执行成功，`result` 包含结果 | 否           |
| `failed`    | 任务执行失败，`error` 包含错误  | 否           |
| `cancelled` | 任务已取消                      | 否           |

建议轮询间隔为 1～2 秒。

## 5. 创建上传任务

### 5.1 请求

```http
POST /api/v1/upload-tasks
X-API-Key: your-search-api-key
Content-Type: application/json
```

请求体：

```json
{
  "image_url": "https://example.com/product.jpg"
}
```

字段说明：

| 字段        | 类型   | 必需 | 说明                             |
| ----------- | ------ | ---: | -------------------------------- |
| `image_url` | string |   是 | 可公开访问的 HTTP/HTTPS 图片 URL |

图片限制：

- 只允许 HTTP 或 HTTPS URL。
- 只允许公网地址。
- 拒绝本机、私网、链路本地、保留地址和多播地址。
- 不允许 URL 包含用户名或密码。
- 重定向后的地址也会重新执行安全检查。
- 远程响应必须返回图片 Content-Type。
- 支持 JPEG、PNG、GIF 和 WebP。
- 默认最大图片体积为 8 MiB。

### 5.2 成功响应

```http
HTTP/1.1 202 Accepted
Content-Type: application/json
```

```json
{
  "task_id": "47704941-1c6a-45f8-885e-c59e15b64883",
  "status": "queued"
}
```

字段说明：

| 字段      | 类型   | 说明                  |
| --------- | ------ | --------------------- |
| `task_id` | string | 上传任务唯一标识      |
| `status`  | string | 新任务固定为 `queued` |

### 5.3 cURL 示例

```bash
curl -X POST 'https://search.example.com/api/v1/upload-tasks' \
  -H 'X-API-Key: your-search-api-key' \
  -H 'Content-Type: application/json' \
  -d '{
    "image_url": "https://example.com/product.jpg"
  }'
```

## 6. 查询上传任务

### 6.1 请求

```http
GET /api/v1/upload-tasks/{task_id}
X-API-Key: your-search-api-key
```

路径参数：

| 参数      | 类型   | 说明                        |
| --------- | ------ | --------------------------- |
| `task_id` | string | 创建上传任务时返回的任务 ID |

### 6.2 排队响应

```json
{
  "task_id": "47704941-1c6a-45f8-885e-c59e15b64883",
  "status": "queued",
  "created_at": 1786504832.7233613,
  "updated_at": 1786504832.7233613,
  "started_at": null,
  "finished_at": null,
  "result": null,
  "error": null
}
```

### 6.3 执行中响应

```json
{
  "task_id": "47704941-1c6a-45f8-885e-c59e15b64883",
  "status": "running",
  "created_at": 1786504832.7233613,
  "updated_at": 1786504832.804938,
  "started_at": 1786504832.804938,
  "finished_at": null,
  "result": null,
  "error": null
}
```

### 6.4 成功响应

```json
{
  "task_id": "47704941-1c6a-45f8-885e-c59e15b64883",
  "status": "succeeded",
  "created_at": 1786504832.7233613,
  "updated_at": 1786504850.9230394,
  "started_at": 1786504832.804938,
  "finished_at": 1786504850.9230394,
  "result": {
    "image_id": "1054908812775127604",
    "search_page_url": "https://air.1688.com/kapp/1688-search/pc-image-search/?tab=imageSearch&imageId=1054908812775127604&imageIdList=1054908812775127604&spm=..."
  },
  "error": null
}
```

上传结果字段：

| 字段              | 类型   | 说明                      |
| ----------------- | ------ | ------------------------- |
| `image_id`        | string | 1688 图片标识             |
| `search_page_url` | string | 1688 官方图片搜索页面 URL |

上传任务成功后不会自动创建商品任务。

### 6.5 失败响应

```json
{
  "task_id": "47704941-1c6a-45f8-885e-c59e15b64883",
  "status": "failed",
  "created_at": 1786504832.7233613,
  "updated_at": 1786504850.9230394,
  "started_at": 1786504832.804938,
  "finished_at": 1786504850.9230394,
  "result": null,
  "error": {
    "code": "IMAGE_DOWNLOAD_FAILED",
    "message": "图片下载超时"
  }
}
```

### 6.6 cURL 示例

```bash
curl -H 'X-API-Key: your-search-api-key' \
  'https://search.example.com/api/v1/upload-tasks/47704941-1c6a-45f8-885e-c59e15b64883'
```

## 7. 创建商品任务

### 7.1 前置条件

关联上传任务必须满足：

- 上传任务存在。
- 上传任务状态为 `succeeded`。
- 上传任务结果包含 `image_id`。
- 上传任务未被取消。

调用方只提交 `upload_task_id`，不能直接提交 `image_id`。

### 7.2 请求

```http
POST /api/v1/product-tasks
X-API-Key: your-search-api-key
Content-Type: application/json
```

请求体：

```json
{
  "upload_task_id": "47704941-1c6a-45f8-885e-c59e15b64883"
}
```

字段说明：

| 字段             | 类型   | 必需 | 说明                |
| ---------------- | ------ | ---: | ------------------- |
| `upload_task_id` | string |   是 | 已成功的上传任务 ID |

### 7.3 成功响应

```http
HTTP/1.1 202 Accepted
Content-Type: application/json
```

```json
{
  "task_id": "0125de9c-fd8a-4fa0-b59e-b480541628f3",
  "upload_task_id": "47704941-1c6a-45f8-885e-c59e15b64883",
  "status": "queued"
}
```

每次请求都会创建新的商品任务。重复提交同一个 `upload_task_id` 不会复用旧商品任务。

### 7.4 cURL 示例

```bash
curl -X POST 'https://search.example.com/api/v1/product-tasks' \
  -H 'X-API-Key: your-search-api-key' \
  -H 'Content-Type: application/json' \
  -d '{
    "upload_task_id": "47704941-1c6a-45f8-885e-c59e15b64883"
  }'
```

## 8. 查询商品任务

### 8.1 请求

```http
GET /api/v1/product-tasks/{task_id}
X-API-Key: your-search-api-key
```

路径参数：

| 参数      | 类型   | 说明                            |
| --------- | ------ | ------------------------------- |
| `task_id` | string | 创建商品任务时返回的商品任务 ID |

不要使用上传任务 ID 查询商品任务。

### 8.2 成功响应

```json
{
  "task_id": "0125de9c-fd8a-4fa0-b59e-b480541628f3",
  "upload_task_id": "47704941-1c6a-45f8-885e-c59e15b64883",
  "status": "succeeded",
  "created_at": 1786504851.104,
  "updated_at": 1786504855.901,
  "started_at": 1786504851.203,
  "finished_at": 1786504855.901,
  "result": {
    "image_id": "1054908812775127604",
    "search_page_url": "https://air.1688.com/kapp/1688-search/pc-image-search/?tab=imageSearch&imageId=1054908812775127604&imageIdList=1054908812775127604&spm=...",
    "found": 698,
    "products": [
      {
        "image": "https://cbu01.alicdn.com/img/ibank/example-1.jpg",
        "title": "商品标题一",
        "price": "128.00",
        "sale_quantity": "9",
        "product_url": "https://detail.1688.com/offer/100000000001.html"
      },
      {
        "image": "https://cbu01.alicdn.com/img/ibank/example-2.jpg",
        "title": "商品标题二",
        "price": "119.00",
        "sale_quantity": 36,
        "product_url": "https://detail.1688.com/offer/100000000002.html"
      }
    ]
  },
  "error": null
}
```

任务字段：

| 字段             | 类型        | 说明                |
| ---------------- | ----------- | ------------------- |
| `task_id`        | string      | 商品任务 ID         |
| `upload_task_id` | string      | 关联上传任务 ID     |
| `status`         | string      | 当前任务状态        |
| `created_at`     | number      | 创建时间            |
| `updated_at`     | number      | 最后更新时间        |
| `started_at`     | number/null | Worker 开始执行时间 |
| `finished_at`    | number/null | 进入终态时间        |
| `result`         | object/null | 成功结果            |
| `error`          | object/null | 失败或取消信息      |

商品查询结果字段：

| 字段              | 类型         | 说明                       |
| ----------------- | ------------ | -------------------------- |
| `image_id`        | string       | 关联上传任务取得的图片标识 |
| `search_page_url` | string       | 1688 官方图片搜索页面 URL  |
| `found`           | integer/null | 1688 返回的搜索结果总数    |
| `products`        | array        | 排序最前的商品，最多 3 条  |

商品字段：

| 字段            | 类型               | 说明                             |
| --------------- | ------------------ | -------------------------------- |
| `image`         | string/null        | 商品主图 URL                     |
| `title`         | string/null        | 商品标题                         |
| `price`         | string/null        | 搜索结果展示价格，不包含货币符号 |
| `sale_quantity` | string/number/null | 1688 返回的销量字段              |
| `product_url`   | string/null        | 1688 商品详情 URL                |

说明：

- 商品按 `offer_id` 去重。
- 最多返回 3 个商品。
- 有效商品不足 3 个时按实际数量返回。
- 商品字段可能为 `null`。
- `price` 不是最终采购结算价。

### 8.3 无商品响应

商品轮询结束仍未返回商品时，任务状态为 `failed`：

```json
{
  "task_id": "0125de9c-fd8a-4fa0-b59e-b480541628f3",
  "upload_task_id": "47704941-1c6a-45f8-885e-c59e15b64883",
  "status": "failed",
  "created_at": 1786504851.104,
  "updated_at": 1786504865.901,
  "started_at": 1786504851.203,
  "finished_at": 1786504865.901,
  "result": null,
  "error": {
    "code": "PRODUCTS_NOT_FOUND",
    "message": "图片搜索未返回商品"
  }
}
```

### 8.4 cURL 示例

```bash
curl -H 'X-API-Key: your-search-api-key' \
  'https://search.example.com/api/v1/product-tasks/0125de9c-fd8a-4fa0-b59e-b480541628f3'
```

## 9. 取消上传任务

### 9.1 请求

```http
DELETE /api/v1/upload-tasks/{task_id}
X-API-Key: your-search-api-key
```

### 9.2 取消语义

- `queued`：立即变为 `cancelled`。
- `running`：记录取消请求，当前阻塞 HTTP 请求结束后停止后续步骤。
- `succeeded`、`failed`、`cancelled`：幂等返回当前状态。
- 上传任务取消后不能创建商品任务。

取消响应使用上传任务完整结构：

```json
{
  "task_id": "47704941-1c6a-45f8-885e-c59e15b64883",
  "status": "cancelled",
  "created_at": 1786504832.7233613,
  "updated_at": 1786504833.001,
  "started_at": null,
  "finished_at": 1786504833.001,
  "result": null,
  "error": {
    "code": "TASK_CANCELLED",
    "message": "任务已取消"
  }
}
```

### 9.3 cURL 示例

```bash
curl -X DELETE \
  -H 'X-API-Key: your-search-api-key' \
  'https://search.example.com/api/v1/upload-tasks/47704941-1c6a-45f8-885e-c59e15b64883'
```

## 10. 取消商品任务

### 10.1 请求

```http
DELETE /api/v1/product-tasks/{task_id}
X-API-Key: your-search-api-key
```

商品任务取消语义与上传任务一致。取消商品任务不会修改关联上传任务、`image_id` 或 `search_page_url`。

### 10.2 cURL 示例

```bash
curl -X DELETE \
  -H 'X-API-Key: your-search-api-key' \
  'https://search.example.com/api/v1/product-tasks/0125de9c-fd8a-4fa0-b59e-b480541628f3'
```

## 11. Python 完整调用示例

```python
import time

import httpx

API_BASE = "https://search.example.com"
SEARCH_API_KEY = "your-search-api-key"
HEADERS = {"X-API-Key": SEARCH_API_KEY}
TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}


def wait_task(client, path, timeout_seconds):
    deadline = time.monotonic() + timeout_seconds

    while time.monotonic() < deadline:
        response = client.get(path, headers=HEADERS)
        response.raise_for_status()
        task = response.json()

        if task["status"] in TERMINAL_STATUSES:
            return task

        time.sleep(2)

    raise TimeoutError(f"等待任务超时: {path}")


with httpx.Client(base_url=API_BASE, timeout=30) as client:
    upload_response = client.post(
        "/api/v1/upload-tasks",
        headers=HEADERS,
        json={"image_url": "https://example.com/product.jpg"},
    )
    upload_response.raise_for_status()
    upload_id = upload_response.json()["task_id"]

    upload_task = wait_task(
        client,
        f"/api/v1/upload-tasks/{upload_id}",
        timeout_seconds=300,
    )

    if upload_task["status"] != "succeeded":
        raise RuntimeError(upload_task["error"])

    print("image_id:", upload_task["result"]["image_id"])
    print("搜索页:", upload_task["result"]["search_page_url"])

    product_response = client.post(
        "/api/v1/product-tasks",
        headers=HEADERS,
        json={"upload_task_id": upload_id},
    )
    product_response.raise_for_status()
    product_id = product_response.json()["task_id"]

    product_task = wait_task(
        client,
        f"/api/v1/product-tasks/{product_id}",
        timeout_seconds=240,
    )

    if product_task["status"] != "succeeded":
        raise RuntimeError(product_task["error"])

    for product in product_task["result"]["products"]:
        print(product["title"])
        print(product["price"])
        print(product["sale_quantity"])
        print(product["image"])
        print(product["product_url"])
```

## 12. JavaScript 完整调用示例

```javascript
const apiBase = "https://search.example.com";
const searchApiKey = "your-search-api-key";
const terminalStatuses = new Set(["succeeded", "failed", "cancelled"]);

const headers = {
  "Content-Type": "application/json",
  "X-API-Key": searchApiKey,
};

const sleep = (milliseconds) =>
  new Promise((resolve) => setTimeout(resolve, milliseconds));

async function requestJson(path, options = {}) {
  const response = await fetch(`${apiBase}${path}`, options);
  const payload = await response.json();

  if (!response.ok) {
    throw new Error(
      `${payload.detail?.code ?? response.status}: ` +
      `${payload.detail?.message ?? "请求失败"}`,
    );
  }

  return payload;
}

async function waitTask(path, maxAttempts) {
  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    const task = await requestJson(path, {
      headers: { "X-API-Key": searchApiKey },
    });

    if (terminalStatuses.has(task.status)) {
      return task;
    }

    await sleep(2000);
  }

  throw new Error(`等待任务超时: ${path}`);
}

const upload = await requestJson("/api/v1/upload-tasks", {
  method: "POST",
  headers,
  body: JSON.stringify({
    image_url: "https://example.com/product.jpg",
  }),
});

const uploadTask = await waitTask(
  `/api/v1/upload-tasks/${upload.task_id}`,
  150,
);

if (uploadTask.status !== "succeeded") {
  throw new Error(`${uploadTask.error.code}: ${uploadTask.error.message}`);
}

console.log(uploadTask.result.image_id);
console.log(uploadTask.result.search_page_url);

const product = await requestJson("/api/v1/product-tasks", {
  method: "POST",
  headers,
  body: JSON.stringify({
    upload_task_id: upload.task_id,
  }),
});

const productTask = await waitTask(
  `/api/v1/product-tasks/${product.task_id}`,
  120,
);

if (productTask.status !== "succeeded") {
  throw new Error(`${productTask.error.code}: ${productTask.error.message}`);
}

console.table(productTask.result.products);
```

## 13. HTTP 错误

### 13.1 错误响应结构

接口级错误使用以下结构：

```json
{
  "detail": {
    "code": "ERROR_CODE",
    "message": "错误说明"
  }
}
```

参数格式错误可能由 FastAPI 返回标准 `422` 校验错误结构。

### 13.2 常见 HTTP 状态码

| HTTP 状态码 | 错误码                   | 说明                              |
| ----------: | ------------------------ | --------------------------------- |
|       `400` | `INVALID_JSON`           | 请求体不是有效 JSON               |
|       `401` | `INVALID_API_KEY`        | API Key 缺失或错误                |
|       `404` | `UPLOAD_TASK_NOT_FOUND`  | 上传任务不存在或已清理            |
|       `404` | `PRODUCT_TASK_NOT_FOUND` | 商品任务不存在或已清理            |
|       `409` | `UPLOAD_TASK_NOT_READY`  | 上传任务尚未成功                  |
|       `409` | `UPLOAD_TASK_CANCELLED`  | 上传任务已取消                    |
|       `409` | `UPLOAD_TASK_FAILED`     | 上传任务已失败                    |
|       `422` | 参数校验错误             | 请求字段缺失、类型错误或 URL 无效 |
|       `429` | `QUEUE_FULL`             | 对应任务队列已满                  |
|       `503` | `COOKIE_UNAVAILABLE`     | 尚未上传可用的 1688 Cookie        |

### 13.3 商品任务创建失败示例

```http
HTTP/1.1 409 Conflict
Content-Type: application/json
```

```json
{
  "detail": {
    "code": "UPLOAD_TASK_NOT_READY",
    "message": "上传任务尚未成功"
  }
}
```

## 14. 任务失败错误码

任务执行失败时，HTTP 查询本身仍返回 `200`，任务对象中的 `status` 为 `failed`，错误信息位于 `error`。

| 错误码                  | 说明                          | 建议处理                        |
| ----------------------- | ----------------------------- | ------------------------------- |
| `IMAGE_URL_REJECTED`    | 图片 URL 被安全策略拒绝       | 更换为可公开访问的公网图片 URL  |
| `IMAGE_DOWNLOAD_FAILED` | 图片下载、格式或体积检查失败  | 检查 URL、图片格式和文件大小    |
| `COOKIE_UNAVAILABLE`    | Worker 未取得可用 Cookie      | 联系服务管理员上传 Cookie       |
| `COOKIE_INVALID`        | 1688 登录态无效               | 重新登录并上传完整 Cookie       |
| `MTOP_TOKEN_INVALID`    | MTOP Token 无效或过期         | 刷新 1688 页面并重新上传 Cookie |
| `MTOP_NETWORK_ERROR`    | MTOP 网络请求或 HTTP 请求失败 | 稍后重试并检查服务日志          |
| `UPLOAD_NO_IMAGE_ID`    | 上传响应缺少图片标识          | 稍后重试并检查服务日志          |
| `PRODUCTS_NOT_FOUND`    | 轮询结束仍没有商品            | 可重新创建商品任务或更换图片    |
| `RISK_CONTROL`          | 1688 返回验证码或风控         | 在浏览器完成验证并更新 Cookie   |
| `RATE_LIMITED`          | 1688 服务端限流               | 降低请求频率后重试              |
| `TASK_TIMEOUT`          | 任务总执行时间超过限制        | 稍后创建新任务                  |
| `PROTOCOL_ERROR`        | 1688 响应结构不符合预期       | 检查服务日志和协议变化          |
| `INTERNAL_ERROR`        | 未分类内部错误                | 联系服务管理员检查日志          |
| `TASK_CANCELLED`        | 任务已取消                    | 按需创建新任务                  |

## 15. 健康检查

### 15.1 基础存活检查

无需鉴权：

```http
GET /health
```

响应：

```json
{
  "status": "ok"
}
```

### 15.2 详细健康状态

```http
GET /api/v1/health
X-API-Key: your-search-api-key
```

响应示例：

```json
{
  "status": "ok",
  "cookie": {
    "available": true,
    "version": 3,
    "cookie_count": 44,
    "uploaded_at": 1786503915.2712467
  },
  "upload_workers": 2,
  "product_workers": 2,
  "upload_tasks": {
    "queued": 0,
    "running": 1,
    "succeeded": 25,
    "failed": 2,
    "cancelled": 1
  },
  "product_tasks": {
    "queued": 1,
    "running": 0,
    "succeeded": 20,
    "failed": 3,
    "cancelled": 0
  }
}
```

## 16. 并发、队列和超时

默认配置：

- 上传 Worker：2。
- 商品 Worker：2。
- 上传任务总超时：240 秒。
- 商品任务总超时：180 秒。
- 单次 MTOP HTTP 请求超时：90 秒。

任务超过 Worker 并发数时进入 `queued`。队列达到上限后，创建接口返回 HTTP `429` 和 `QUEUE_FULL`。

调用方处理建议：

- 正常处理 `queued`，不要将排队视为失败。
- 对 HTTP `429` 使用指数退避。
- 不要高频轮询同一任务。
- 客户端等待时间应高于服务端任务总超时。

## 17. 任务保留

终态任务默认保留 24 小时。任务被清理后，查询接口返回 HTTP `404`。

调用方应在任务成功后及时保存：

- `image_id`。
- `search_page_url`。
- 需要持久保存的商品结果。

## 18. 兼容性说明

旧接口已删除：

```http
POST /api/v1/search-tasks
GET /api/v1/search-tasks/{task_id}
```

调用旧接口返回 HTTP `404`。所有调用方必须迁移到上传任务和商品任务两阶段流程。

## 19. 安全建议

- 只通过 HTTPS 调用生产 API。
- `SEARCH_API_KEY` 只存放在可信服务端，不要暴露在公开网页代码中。
- 不要在日志中记录 API Key。
- 不要向查询调用方提供 `COOKIE_UPLOAD_API_KEY` 或 `COOKIE_ENCRYPTION_KEY`。
- 服务端轮换 `SEARCH_API_KEY` 后，调用方应及时更新配置。
