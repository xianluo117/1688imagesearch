# 1688 图片搜索查询 API

## 1. 接口说明

查询 API 根据远程图片 URL 创建 1688 图片搜索任务，并异步返回：

- 1688 官方图搜页面 URL。
- 搜索结果总数。
- 排序最前的 3 个商品。
- 商品图片、标题、售价、销量和详情链接。

API 使用异步任务模式：

1. 调用创建任务接口，获得 `task_id`。
2. 使用 `task_id` 轮询任务状态。
3. 当状态变为 `succeeded` 时读取搜索结果。

服务同时最多执行 4 个搜索任务。超出的任务进入队列等待。

## 2. 基础信息

假设服务地址为：

```text
https://search.example.com
```

接口统一前缀：

```text
/api/v1
```

请求和响应编码：

```text
application/json; charset=utf-8
```

## 3. 鉴权

查询接口使用独立的 `SEARCH_API_KEY`。

每次请求必须携带：

```http
X-API-Key: your-search-api-key
```

查询密钥只允许：

- 创建图片搜索任务。
- 查询任务状态和结果。
- 查询服务详细健康状态。

查询密钥不能上传或替换 1688 Cookie。

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

## 4. 创建搜索任务

### 4.1 请求

```http
POST /api/v1/search-tasks
```

请求头：

```http
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

| 字段        | 类型   | 必需 | 说明                                       |
| ----------- | ------ | ---: | ------------------------------------------ |
| `image_url` | string |   是 | 可公开访问的图片 URL，只支持 HTTP 或 HTTPS |

图片 URL 限制：

- 仅允许 `http://` 或 `https://`。
- 仅允许 80 或 443 端口。
- 不允许 URL 中包含用户名或密码。
- 不允许访问本机、局域网、私网、链路本地或保留地址。
- 远程资源必须返回图片 Content-Type。
- 图片格式只支持 JPEG、PNG、GIF、WebP。
- 图片默认最大 8 MiB。
- 重定向目标也会重新进行安全检查。

### 4.2 成功响应

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

| 字段      | 类型   | 说明                           |
| --------- | ------ | ------------------------------ |
| `task_id` | string | 任务唯一标识，后续用于查询状态 |
| `status`  | string | 新任务固定为 `queued`          |

### 4.3 cURL 示例

```bash
curl -X POST 'https://search.example.com/api/v1/search-tasks' \
  -H 'X-API-Key: your-search-api-key' \
  -H 'Content-Type: application/json' \
  -d '{
    "image_url": "https://example.com/product.jpg"
  }'
```

### 4.4 Python 示例

```python
import httpx

API_BASE = "https://search.example.com"
SEARCH_API_KEY = "your-search-api-key"

response = httpx.post(
    f"{API_BASE}/api/v1/search-tasks",
    headers={"X-API-Key": SEARCH_API_KEY},
    json={"image_url": "https://example.com/product.jpg"},
    timeout=30,
)
response.raise_for_status()

task_id = response.json()["task_id"]
print(task_id)
```

### 4.5 JavaScript 示例

```javascript
const apiBase = "https://search.example.com";
const searchApiKey = "your-search-api-key";

const response = await fetch(`${apiBase}/api/v1/search-tasks`, {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "X-API-Key": searchApiKey,
  },
  body: JSON.stringify({
    image_url: "https://example.com/product.jpg",
  }),
});

if (!response.ok) {
  throw new Error(`创建任务失败：HTTP ${response.status}`);
}

const task = await response.json();
console.log(task.task_id);
```

## 5. 查询任务状态

### 5.1 请求

```http
GET /api/v1/search-tasks/{task_id}
```

路径参数：

| 参数      | 类型   | 说明                       |
| --------- | ------ | -------------------------- |
| `task_id` | string | 创建任务接口返回的任务标识 |

请求头：

```http
X-API-Key: your-search-api-key
```

### 5.2 任务状态

| 状态        | 说明                         | 是否继续轮询 |
| ----------- | ---------------------------- | ------------ |
| `queued`    | 任务正在等待 Worker          | 是           |
| `running`   | 正在下载图片并执行 1688 图搜 | 是           |
| `succeeded` | 搜索成功，`result` 包含结果  | 否           |
| `failed`    | 搜索失败，`error` 包含错误   | 否           |

建议每 1 至 2 秒轮询一次，不要高频请求。

### 5.3 排队状态响应

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

### 5.4 执行状态响应

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

### 5.5 成功响应

```json
{
  "task_id": "47704941-1c6a-45f8-885e-c59e15b64883",
  "status": "succeeded",
  "created_at": 1786504832.7233613,
  "updated_at": 1786504838.9230394,
  "started_at": 1786504832.804938,
  "finished_at": 1786504838.9230394,
  "result": {
    "search_page_url": "https://air.1688.com/kapp/1688-search/pc-image-search/?tab=imageSearch&imageId=1054908812775127604&imageIdList=1054908812775127604&spm=a260k.home2025%2F2025.imagesearch.upload",
    "image_id": "1054908812775127604",
    "found": 698,
    "products": [
      {
        "image": "https://cbu01.alicdn.com/img/ibank/example-1.jpg",
        "title": "亚麻直筒阔腿裤子女夏薄款",
        "price": "128.00",
        "sale_quantity": "9",
        "product_url": "https://detail.1688.com/offer/1046898628766.html"
      },
      {
        "image": "https://cbu01.alicdn.com/img/ibank/example-2.jpg",
        "title": "夏季松紧纯色亚麻直筒裤女",
        "price": "128.00",
        "sale_quantity": "12",
        "product_url": "https://detail.1688.com/offer/1039159288332.html"
      },
      {
        "image": "https://cbu01.alicdn.com/img/ibank/example-3.jpg",
        "title": "纯亚麻阔腿裤女夏薄款",
        "price": "119.00",
        "sale_quantity": "73",
        "product_url": "https://detail.1688.com/offer/1045602619863.html"
      }
    ]
  },
  "error": null
}
```

### 5.6 响应字段说明

任务字段：

| 字段          | 类型        | 说明                              |
| ------------- | ----------- | --------------------------------- |
| `task_id`     | string      | 任务唯一标识                      |
| `status`      | string      | 当前任务状态                      |
| `created_at`  | number      | 创建时间，Unix 秒时间戳           |
| `updated_at`  | number      | 最后更新时间，Unix 秒时间戳       |
| `started_at`  | number/null | Worker 开始处理时间               |
| `finished_at` | number/null | 任务完成或失败时间                |
| `result`      | object/null | 成功结果，仅 `succeeded` 状态存在 |
| `error`       | object/null | 失败信息，仅 `failed` 状态存在    |

结果字段：

| 字段              | 类型         | 说明                      |
| ----------------- | ------------ | ------------------------- |
| `search_page_url` | string       | 1688 官方图搜页面 URL     |
| `image_id`        | string       | 1688 图片搜索标识         |
| `found`           | integer/null | 1688 返回的搜索结果总数   |
| `products`        | array        | 排序最前的商品，最多 3 条 |

商品字段：

| 字段            | 类型               | 说明                                |
| --------------- | ------------------ | ----------------------------------- |
| `image`         | string/null        | 商品主图 URL                        |
| `title`         | string/null        | 商品标题                            |
| `price`         | string/null        | 1688 返回的商品售价，不包含货币符号 |
| `sale_quantity` | string/number/null | 1688 返回的销量                     |
| `product_url`   | string/null        | 1688 商品详情 URL                   |

注意：

- `price` 是搜索结果展示价格，实际采购价格可能因规格、数量、活动和登录状态变化。
- `sale_quantity` 保持 1688 原始响应类型和含义。
- 个别商品字段可能为空，调用方应允许 `null`。
- `products` 最多返回 3 条；实际有效结果不足时可能少于 3 条。

### 5.7 失败响应

```json
{
  "task_id": "47704941-1c6a-45f8-885e-c59e15b64883",
  "status": "failed",
  "created_at": 1786504832.7233613,
  "updated_at": 1786504838.9230394,
  "started_at": 1786504832.804938,
  "finished_at": 1786504838.9230394,
  "result": null,
  "error": {
    "code": "IMAGE_DOWNLOAD_FAILED",
    "message": "图片下载超时"
  }
}
```

## 6. 完整轮询示例

### 6.1 Python

```python
import time
import httpx

API_BASE = "https://search.example.com"
SEARCH_API_KEY = "your-search-api-key"
HEADERS = {"X-API-Key": SEARCH_API_KEY}

create_response = httpx.post(
    f"{API_BASE}/api/v1/search-tasks",
    headers=HEADERS,
    json={"image_url": "https://example.com/product.jpg"},
    timeout=30,
)
create_response.raise_for_status()
task_id = create_response.json()["task_id"]

for _ in range(90):
    response = httpx.get(
        f"{API_BASE}/api/v1/search-tasks/{task_id}",
        headers=HEADERS,
        timeout=30,
    )
    response.raise_for_status()
    task = response.json()

    if task["status"] == "succeeded":
        result = task["result"]
        print("搜索页：", result["search_page_url"])
        for product in result["products"]:
            print(product["title"])
            print(product["price"])
            print(product["sale_quantity"])
            print(product["image"])
            print(product["product_url"])
        break

    if task["status"] == "failed":
        raise RuntimeError(
            f'{task["error"]["code"]}: {task["error"]["message"]}'
        )

    time.sleep(2)
else:
    raise TimeoutError("等待搜索任务完成超时")
```

### 6.2 JavaScript

```javascript
const apiBase = "https://search.example.com";
const searchApiKey = "your-search-api-key";
const headers = {
  "Content-Type": "application/json",
  "X-API-Key": searchApiKey,
};

const createResponse = await fetch(`${apiBase}/api/v1/search-tasks`, {
  method: "POST",
  headers,
  body: JSON.stringify({
    image_url: "https://example.com/product.jpg",
  }),
});

if (!createResponse.ok) {
  throw new Error(`创建任务失败：HTTP ${createResponse.status}`);
}

const { task_id: taskId } = await createResponse.json();

for (let index = 0; index < 90; index += 1) {
  const response = await fetch(
    `${apiBase}/api/v1/search-tasks/${taskId}`,
    {
      headers: { "X-API-Key": searchApiKey },
    },
  );

  if (!response.ok) {
    throw new Error(`查询任务失败：HTTP ${response.status}`);
  }

  const task = await response.json();

  if (task.status === "succeeded") {
    console.log(task.result.search_page_url);
    console.table(task.result.products);
    break;
  }

  if (task.status === "failed") {
    throw new Error(`${task.error.code}: ${task.error.message}`);
  }

  await new Promise((resolve) => setTimeout(resolve, 2000));
}
```

## 7. HTTP 错误

### 7.1 常见 HTTP 状态码

| HTTP 状态码 | 错误码               | 说明                              |
| ----------: | -------------------- | --------------------------------- |
|       `401` | `INVALID_API_KEY`    | 搜索 API Key 缺失或错误           |
|       `404` | `TASK_NOT_FOUND`     | `task_id` 不存在或已清理          |
|       `422` | FastAPI 参数校验错误 | 请求字段缺失、类型错误或 URL 无效 |
|       `429` | `QUEUE_FULL`         | 排队和执行中的任务达到上限        |
|       `503` | `COOKIE_UNAVAILABLE` | 尚未上传有效的 1688 Cookie        |

### 7.2 任务失败错误码

| 错误码                  | 说明                         | 建议处理                               |
| ----------------------- | ---------------------------- | -------------------------------------- |
| `IMAGE_URL_REJECTED`    | 图片 URL 被安全策略拒绝      | 使用可公开访问的公网图片 URL           |
| `IMAGE_DOWNLOAD_FAILED` | 图片下载、格式或体积检查失败 | 检查 URL、Content-Type、格式和文件大小 |
| `COOKIE_UNAVAILABLE`    | Worker 未取得可用 Cookie     | 联系服务管理员重新上传 Cookie          |
| `COOKIE_INVALID`        | 1688 登录态无效              | 重新登录并上传完整 Cookie              |
| `MTOP_TOKEN_INVALID`    | MTOP Token 无效或过期        | 刷新 1688 页面并重新上传 Cookie        |
| `RISK_CONTROL`          | 1688 返回验证码或风控        | 在浏览器完成验证后重新上传 Cookie      |
| `RATE_LIMITED`          | 1688 服务端限流              | 降低请求频率并稍后重试                 |
| `PROTOCOL_ERROR`        | 1688 响应结构不符合预期      | 记录任务信息并检查服务日志             |
| `TASK_TIMEOUT`          | 任务总执行时间超过限制       | 稍后重试或调整服务超时参数             |
| `INTERNAL_ERROR`        | 未分类内部错误               | 联系服务管理员检查日志                 |

## 8. 并发与队列

- 服务内部最多同时执行 4 个搜索任务。
- 第 5 个及后续任务进入 `queued` 状态。
- 队列和正在执行的任务总数达到服务上限时，创建接口返回 HTTP `429`。
- 调用方无需自行控制到 4 个并发，但应处理 `queued` 和 `429`。
- 建议调用方对 `429` 使用指数退避，初始等待 2 秒。
- 不要对同一 `task_id` 每秒请求多次。

## 9. 任务保留

已成功或失败的任务会在服务端保留一段时间，默认 24 小时。过期任务被清理后，查询接口返回：

```http
HTTP/1.1 404 Not Found
```

```json
{
  "detail": {
    "code": "TASK_NOT_FOUND",
    "message": "任务不存在"
  }
}
```

调用方应在任务完成后及时保存需要的结果。

## 10. 健康检查

### 10.1 基础存活检查

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

### 10.2 详细健康状态

需要 `SEARCH_API_KEY`：

```http
GET /api/v1/health
X-API-Key: your-search-api-key
```

响应示例：

```json
{
  "status": "ok",
  "worker_limit": 4,
  "cookie": {
    "available": true,
    "version": 1,
    "cookie_count": 44,
    "uploaded_at": 1786503915.2712467
  },
  "tasks": {
    "queued": 0,
    "running": 1,
    "succeeded": 25,
    "failed": 2
  }
}
```

## 11. 调用建议

- API Key 仅存放在服务端或可信后端，不要暴露在公开网页前端。
- 使用 HTTPS 调用正式服务。
- 为创建任务和查询任务分别设置 30 秒 HTTP 超时。
- 轮询间隔建议为 2 秒。
- 单个任务建议最多等待 180 秒。
- 对 `429`、网络错误和 HTTP `5xx` 执行有限次数重试。
- 不要对已经 `failed` 的同一任务继续轮询，应根据错误码决定是否创建新任务。
