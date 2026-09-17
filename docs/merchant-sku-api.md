# 一次查询商家 ID、规格图片 URL 与 SKU ID API

## 1. 用途与标识说明

**只调用一次 POST /api/v1/product-skus，提交一个1688商品详情链接，即在同一个响应中返回商家ID、规格图片URL和SKU ID。无需分别调用商家、图片或规格接口，也不需要额外开启参数。**

同时返回规格名称、规格值和位置。无需先发起图搜任务，无需轮询。服务器从本次详情查询的数据中提取这些信息；请求可能按既定规则发生重定向或有限网络重试，但不会为了商家或规格图片另查接口，也不会下载图片。

| 需要的数据 | 在同一响应中的读取位置 |
|---|---|
| 商家数字用户ID | 顶层 [`seller_user_id`](../src/api/sku_schemas.py:29) |
| 商家会员ID | 顶层 [`seller_member_id`](../src/api/sku_schemas.py:30) |
| SKU ID | [`skus`](../src/api/sku_schemas.py:25) 数组内每条记录的 [`sku_id`](../src/product_sku/models.py:16) |
| 规格图片URL | 顶层 [`specification_images`](../src/api/sku_schemas.py:31) 数组内的 [`image_url`](../src/product_sku/models.py:26) |

同色不同尺码共用颜色选项的图片记录，不把URL重复放到每条SKU中。图片记录与SKU通过规格位置、字段、名称和值对应，不按数组下标对应。无法可靠获取的商家ID或图片URL返回空值，并不保证每次查询都能得到非空值。

| 标识 | 响应字段 | 含义 |
|---|---|---|
| 商家数字用户ID | [`seller_user_id`](../src/api/sku_schemas.py:29) | 当前商品卖家的数字用户ID，以字符串返回 |
| 商家会员ID | [`seller_member_id`](../src/api/sku_schemas.py:30) | 当前商品卖家的会员标识，与数字用户ID分开使用 |
| 规格组合ID（SKU ID） | [`sku_id`](../src/product_sku/models.py:16) | 一个可售规格组合的标识，例如“蓝色 + M” |
| 商品ID | [`product_id`](../src/api/sku_schemas.py:16) | 详情链接中的商品编号，不是SKU ID |

**SKU ID与上游规格标识是不同字段，不能互相替代。** 每条SKU同时保留 [`sku_id`](../src/product_sku/models.py:16) 和新增的 [`spec_id`](../src/product_sku/models.py:18)。后者取自同一已验证交易行的上游规格标识，不是单个颜色或尺码选项ID。其他程序要求哪种标识，以该程序的接口契约为准；本项目未验证其他程序的兼容性。商家数字用户ID和会员ID也不等同于店铺ID或登录名。

## 2. 请求

| 项目 | 内容 |
|---|---|
| 方法 | POST |
| 路径 | /api/v1/product-skus |
| 已配置站点示例 | https://1688imagesearch.xiaoc-ai.com/api/v1/product-skus |
| 本机地址 | http://127.0.0.1:实际API端口/api/v1/product-skus |
| 请求类型 | application/json |
| 鉴权请求头 | X-API-Key：服务器配置的搜索/查询密钥 |
| Cookie | 由服务器读取当前激活的1688会话，调用方不传Cookie |

鉴权使用[服务器搜索密钥配置](../src/api/config.py:106)，不能使用Cookie上传密钥。密钥仅放入请求头，不放入URL，不写入前端公开代码或日志。

### 2.1 请求体

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| [`product_url`](../src/api/sku_schemas.py:12) | 字符串 | 是 | 标准1688商品详情链接，长度1至4096 |

仅接受上述字段，额外字段会被拒绝。链接必须属于标准1688详情域名及数字商品路径；拒绝用户信息、显式端口、外域和非详情路径。HTTP链接会规范为HTTPS，查询参数与片段被移除。

请求示例，字段定义见[请求模型](../src/api/sku_schemas.py:9)：

```json
{
  "product_url": "https://detail.1688.com/offer/844515661443.html"
}
```

客户端需向实际部署地址发送上述请求体，并设置两个请求头：Content-Type 为 application/json；X-API-Key 为本地安全配置的查询密钥。

## 3. 成功响应

HTTP 200直接返回结果对象，不额外包装任务对象。以下全部标识为**合成示例**，不是实际商家或SKU数据。完整字段定义见[API响应模型](../src/api/sku_schemas.py:15)和[SKU结果模型](../src/product_sku/models.py:6)。

```json
{
  "product_id": "123456789012",
  "canonical_url": "https://detail.1688.com/offer/123456789012.html",
  "status": "partial_success",
  "reason": "sku_data_found",
  "source": "detail_html",
  "main_image": null,
  "seller_user_id": "987654321012345678",
  "seller_member_id": "synthetic_member_01",
  "specification_images": [
    {"position": 1, "field": "sku1", "name": "颜色", "value": "蓝色", "image_url": "https://img.example/blue.jpg"},
    {"position": 2, "field": "sku2", "name": "尺码", "value": "M", "image_url": null},
    {"position": 2, "field": "sku2", "name": "尺码", "value": "L", "image_url": null}
  ],
  "skus": [
    {
      "sku_id": "900000000000000001",
      "spec_id": "0123456789abcdef0123456789ABCDEF",
      "specifications": [
        {"position": 1, "field": "sku1", "value": "蓝色", "name": "颜色"},
        {"position": 2, "field": "sku2", "value": "M", "name": "尺码"}
      ]
    },
    {
      "sku_id": "900000000000000002",
      "spec_id": "abcdef0123456789abcdef0123456789",
      "specifications": [
        {"position": 1, "field": "sku1", "value": "蓝色", "name": "颜色"},
        {"position": 2, "field": "sku2", "value": "L", "name": "尺码"}
      ]
    }
  ],
  "sku_count": 2,
  "warnings": ["sku_completeness_unknown"],
  "completeness": "unknown"
}
```

### 3.1 顶层字段

| 字段 | 类型 | 说明 |
|---|---|---|
| [`product_id`](../src/api/sku_schemas.py:16) | 字符串 | 当前商品ID |
| [`canonical_url`](../src/api/sku_schemas.py:17) | 字符串 | 规范化HTTPS商品地址 |
| [`seller_user_id`](../src/api/sku_schemas.py:29) | 字符串或空值 | 卖家数字用户ID，不转换为浮点或普通前端数值 |
| [`seller_member_id`](../src/api/sku_schemas.py:30) | 字符串或空值 | 卖家会员ID，保留大小写 |
| [`skus`](../src/api/sku_schemas.py:25) | 数组 | 当前解析得到的有效SKU |
| [`specification_images`](../src/api/sku_schemas.py:31) | 数组 | 同一次查询返回的去重规格选项及可空图片URL，见第3.3节 |
| [`sku_count`](../src/api/sku_schemas.py:28) | 整数 | 返回SKU数组长度，不代表平台全部SKU总数 |
| [`status`](../src/api/sku_schemas.py:18) | 字符串 | 业务状态，见第5节 |
| [`reason`](../src/api/sku_schemas.py:22) | 字符串 | 分类原因，例如找到SKU数据或字段缺失 |
| [`warnings`](../src/api/sku_schemas.py:26) | 字符串数组 | 数据限制、冲突或字段无法验证的提示 |
| [`completeness`](../src/api/sku_schemas.py:27) | 字符串 | 当前为 unknown，不承诺SKU完整覆盖 |
| [`source`](../src/api/sku_schemas.py:23) | 字符串 | 当前为 detail_html |
| [`main_image`](../src/api/sku_schemas.py:24) | 字符串或空值 | 商品主图兼容字段；新交易模型路径返回空值。规格图片请读取独立规格图片列表，不读取此字段 |

### 3.2 SKU与规格字段

| 字段 | 类型 | 说明 |
|---|---|---|
| [`sku_id`](../src/product_sku/models.py:16) | 字符串 | 实际SKU ID，不根据选项组合生成，继续保留 |
| [`spec_id`](../src/product_sku/models.py:18) | 字符串或空值 | 同一交易行的上游规格标识，原样返回并保留大小写；不是SKU ID |
| [`specifications`](../src/product_sku/models.py:17) | 数组 | 该SKU的规格项 |
| [`position`](../src/product_sku/models.py:8) | 整数 | 从1开始的规格位置，不能当作规格ID |
| [`field`](../src/product_sku/models.py:9) | 字符串 | 位置字段，例如 sku1、sku2 |
| [`name`](../src/product_sku/models.py:11) | 字符串或空值 | 已验证的规格名称；旧数据来源可能无法提供 |
| [`value`](../src/product_sku/models.py:10) | 字符串或空值 | 规格值，保留原始内容；旧来源缺失位置可能为空 |

规格按位置读取，不应假定第1维总是颜色、第2维总是尺码。数据来源不同，规格维度数也可能不同。

### 规格标识规则

- 新增字段随SKU、商家ID及规格图片列表在同一次查询中返回，不需要额外参数或额外接口。
- 仅使用通过商品归属和规格组合验证的交易行。当前支持实际页面已验证的32位十六进制字符串，按不透明标识处理，不计算哈希、不转换大小写、不从SKU ID推导。
- 缺失、旧重量表来源或不支持的格式返回空值，不删除原本有效的SKU。
- 同一SKU对应多个不同标识、有效标识与缺失候选不一致，或同一标识被不同SKU共用时，受影响的规格标识置空并附带冲突警告。SKU本身的规格值冲突仍按原规则移除SKU。
- 格式非法时附带 invalid_spec_id 警告；关联冲突时附带 conflicting_spec_id 警告。
- 2026-09-17在线验证商品844515661443：24个SKU均返回非空规格标识，24个标识互不重复、均与SKU ID不同；同时返回两类商家ID和3条非空颜色图片URL。结果仅有完整性未知警告。
- 最新96项离线回归通过。新增字段需部署并重启API才会生效；本轮规格标识改动尚未提交推送，未停止运行中的服务。

### 3.3 规格图片URL（按选项去重）

顶层新增 [`specification_images`](../src/api/sku_schemas.py:31) 数组。只返回URL，不下载图片；不在每个尺码的SKU上重复附加颜色图片。

每条记录结构见 [`SpecificationImage`](../src/product_sku/models.py:21)：规格位置、位置字段、规格名称、规格值、可空图片URL。通过这些规格信息与SKU的规格项关联，不能将图片列表下标与SKU数组下标对应。

合成示例（响应片段）：

```json
{
  "specification_images": [
    {"position": 1, "field": "sku1", "name": "颜色", "value": "蓝色", "image_url": "https://img.example/blue.jpg"},
    {"position": 2, "field": "sku2", "name": "尺码", "value": "M", "image_url": null},
    {"position": 2, "field": "sku2", "name": "尺码", "value": "L", "image_url": null}
  ]
}
```

- 同一规格位置、名称和值只出现一次。同一颜色有多个尺码时，该颜色仅返回一个URL。
- 仅包含最终返回SKU使用的选项，不包含未返回SKU的选项；不同选项即使共用URL仍分别保留其关联信息。
- 没有图片、URL不符合校验规则、候选图片冲突或无法可靠关联时，图片URL为空。不会使用商品主图或其他颜色图片填补。
- 尺码等无图选项仍保留记录，图片URL为空。没有有效SKU时列表为空；旧重量表来源无法验证图片，选项URL为空。
- URL校验只接受明确HTTP/HTTPS地址，拒绝用户信息、控制字符、反斜杠、片段、异常端口及明显本地地址。不会解析DNS或下载图片；有效URL不保证远端当前可访问，调用方下载时仍需自己的网络安全校验。
- 返回地址保留原查询参数，不对整页或图片值做猜测性反转义。

2026-09-17独立客户端在线验证商品844515661443：同一次查询返回两类卖家ID、24个SKU，以及去重后的11个规格选项，其中3条颜色图片URL、8条尺码空值。未下载图片，日志未输出图片地址。完整离线回归87项通过。图片功能已随提交183877d推送至origin/main。**部署端必须拉取更新并重启现有API才能加载新字段，不能将代码已推送视为服务已更新。**

## 4. 商家字段的空值与安全规则

- 商家信息来自本次商品详情响应，不增加额外上游接口请求。
- 必须通过当前商品归属验证；推荐商品、其他商品或无法确认归属的卖家信息不会使用。
- 两种商家ID分别验证，不互相替代，也不使用当前会话买家ID或卖家登录名回填。
- 字段缺失或归属无法确认时返回空值。格式非法或多个可信候选冲突时，对应字段为空并可能附带未验证警告。
- 两个字段彼此独立：一个为空不必然导致另一个为空，不影响有效SKU返回。
- 仅在取得有效SKU后附带商家ID；解析失败或数据源不适用时，不保证提供商家ID。这不是独立的商家查询接口。
- 会员ID当前接受1至128位ASCII字母、数字、下划线或连字符；不支持的格式不猜测转换。

调用方需要商家ID时，应在读取SKU后分别检查两个字段是否非空。不得把空值写成字符串“null”或数字0。

## 5. 状态与错误处理

### 5.1 带完整结果结构的响应

HTTP状态映射以[路由实现](../src/api/sku.py:53)为准。

| HTTP | 业务状态 | 含义与处理 |
|---|---|---|
| 200 | partial_success | 已取得有效SKU，但存在完整性等限制；检查警告及商家字段 |
| 200 | success | 完整成功预留状态；当前解析器不承诺返回 |
| 200 | source_not_applicable | 当前数据源缺字段或为空；不能当作成功获取SKU |
| 502 | parse_failed | 格式、归属或规格映射不能可靠解析 |
| 502 | access_restricted | 上游访问受限或出现挑战页面，不应密集重试 |
| 502 | network_failed | 有限网络重试耗尽或上游HTTP异常 |
| 503 | login_required | 上游要求登录，需检查服务器活动Cookie |

**HTTP 200不等于一定有SKU或商家ID。** 必须同时检查业务状态、SKU数量、警告和所需字段。

### 5.2 请求或服务错误

此类响应通常使用详情错误对象，而不是完整SKU结果；定义见[路由](../src/api/sku.py:33)及[查询服务](../src/api/sku_service.py:15)。

| HTTP | 错误码或情形 | 处理 |
|---|---|---|
| 401 | INVALID_API_KEY | 查询密钥缺失或错误，检查X-API-Key |
| 422 | INVALID_PRODUCT_URL或框架参数校验错误 | 检查链接、字段类型、缺少字段或额外字段 |
| 429 | SKU_BUSY | 并发已满，延迟后有限重试 |
| 502 | SKU_QUERY_FAILED | 内部查询异常；服务不回显原始异常或页面 |
| 503 | COOKIE_UNAVAILABLE | 服务没有可用活动Cookie或无法解密；由管理员处理 |
| 503 | SKU_SERVICE_STOPPING | 服务停机中，稍后重试 |
| 504 | SKU_QUERY_TIMEOUT | 等待超时；后台请求结束前仍占并发槽，避免立即密集重试 |

错误示例，结构由[查询路由](../src/api/sku.py:45)产生：

```json
{
  "detail": {
    "code": "COOKIE_UNAVAILABLE",
    "message": "服务器没有可用的 1688 Cookie"
  }
}
```

请求体的框架校验失败可能返回错误列表，不保证详情始终是包含错误码的对象。调用方应先检查响应结构，再读取对应字段。

## 6. 调用建议

1. 从后端或受信任客户端调用，不向公开浏览器代码暴露查询密钥。
2. 只提交商品详情链接，不传Cookie、图片、图搜任务ID或额外参数。
3. 接收响应后先检查HTTP状态，再检查业务状态与SKU数量。
4. 从同一响应读取顶层两种商家ID、SKU数组和规格图片列表；逐条读取SKU ID，再根据规格位置、字段、名称和值关联图片，无需再次查询。
5. 所有ID按字符串保存；会员ID保留大小写。建议同时保存商品ID与SKU ID。
6. 当前数据完整性未知，不能仅根据规格选项数推断或补造SKU。
7. 上游受限、登录失效及字段无法解析时不要无限重试。

默认SKU并发为2，单次上游超时20秒，接口等待超时90秒；实际值可由管理员调整。默认最多一次网络重试，最多两次受限重定向。默认配置下客户端与反向代理超时建议至少110秒。配置与线程占用规则见[模块说明](product-sku.md)。

## 7. 部署与验证记录

- **更新代码后必须重启现有API进程**，才能加载商家字段与新解析逻辑；不要重复启动占用同一端口的服务。
- 复用原有数据库、加密密钥和活动Cookie，不需要新建表或重新上传仍有效的会话。
- 重启后可在服务 /docs 查看接口说明，或在 /openapi.json 核对响应字段。
- 2026-09-17独立新代码客户端实测商品844515661443：上游HTTP 200、24个SKU、48个带名称规格项，两类卖家ID均成功返回。
- 此前商品898728774563已验证224个SKU；新增商家字段本轮在线验证对象为844515661443。
- 最新六个测试文件共96项离线回归通过，包含规格标识原值保留/冲突/空值、商家字段、规格图片去重、图片冲突置空及API契约测试。
- 上述在线记录不等于已验证部署网站、反向代理或仍运行的旧API进程。页面结构和会话状态变化可能影响后续结果。

实现参考：[查询路由](../src/api/sku.py)、[响应契约](../src/api/sku_schemas.py)、[SKU解析器](../src/product_sku/parser.py)、[结果模型](../src/product_sku/models.py)。
