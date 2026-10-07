# 一次查询商家 ID、规格图片 URL 与 SKU ID API

## 1. 用途与标识说明

**新接入推荐调用一次 [POST /api/v2/product-skus](../src/api/sku_v2.py:13)，提交一个1688商品详情链接，即在同一个响应中返回商家ID、规格图片URL和SKU ID。无需分别调用商家、图片或规格接口，也不需要额外开启参数。** [第一版接口](../src/api/sku.py:18)继续保留，不自动改变已有客户端的响应结构。

### 1.1 版本选择与阅读范围

第1～7节的字段表、完整响应和顶层读取说明以第一版为准；请求体、鉴权、安全规则、数据限制与HTTP策略两版共用。第8节说明第二版结构、快速调用及迁移；新客户端请读取第二版数据对象，不照搬第一版顶层路径。

| 版本 | POST路径 | 响应读取入口 | 使用建议 |
|---|---|---|---|
| 第一版 | [/api/v1/product-skus](../src/api/sku.py:19) | [顶层商品、商家、SKU和汇总字段](../src/api/sku_schemas.py:15) | 兼容已有接入 |
| 第二版 | [/api/v2/product-skus](../src/api/sku_v2.py:14) | [`data`](../src/api/sku_v2_schemas.py:89) 内的商品、商家、规格、尺码和SKU；诊断读取 [`meta`](../src/api/sku_v2_schemas.py:90) | 推荐新接入，见第8节 |

两版均为一次同步查询，不是任务创建接口；更换路径时必须同步更换响应读取方式。

同时返回规格名称、规格值和位置，以及带维度标识的去重尺码、按颜色关联的尺码和逐SKU可空价格字段。无需先发起图搜任务，无需轮询。服务器从本次商品详情查询中提取，不为价格另查接口。**[生产价格优先级](../src/product_sku/prices.py:82)：原始SKU报价优先，其次同SKU折扣报价；均不可用才为空，不提供阶梯价或最终结算价。**

| 需要的数据 | 在同一响应中的读取位置 |
|---|---|
| 商家数字用户ID | 顶层 [`seller_user_id`](../src/api/sku_schemas.py:29) |
| 商家会员ID | 顶层 [`seller_member_id`](../src/api/sku_schemas.py:30) |
| SKU ID | [`skus`](../src/api/sku_schemas.py:25) 数组内每条记录的 [`sku_id`](../src/product_sku/models.py:16) |
| 规格图片URL | 顶层 [`specification_images`](../src/api/sku_schemas.py:31) 数组内的 [`image_url`](../src/product_sku/models.py:50) |
| 去重尺码 | 顶层 [`sizes`](../src/api/sku_schemas.py:32)，按维度分组 |
| 每种颜色对应尺码 | 顶层 [`color_sizes`](../src/api/sku_schemas.py:33)，只关联真实返回组合 |
| 逐SKU价格 | [`price`](../src/product_sku/models.py:21) 与 [`currency`](../src/product_sku/models.py:22)，有可信报价时非空，缺失时为空 |

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
| 路径 | 新接入：[/api/v2/product-skus](../src/api/sku_v2.py:14)；兼容接入：[/api/v1/product-skus](../src/api/sku.py:19) |
| 已配置站点示例 | https://1688imagesearch.xiaoc-ai.com + 所选版本路径；不代表已验证该站点部署了第二版 |
| 本机地址 | http://127.0.0.1:实际API端口 + 所选版本路径 |
| 请求类型 | application/json |
| 鉴权请求头 | X-API-Key：服务器配置的搜索/查询密钥 |
| Cookie | 由服务器读取当前激活的1688会话，调用方不传Cookie |

鉴权使用[服务器搜索密钥配置](../src/api/config.py:106)，不能使用Cookie上传密钥。密钥仅放入请求头，不放入URL，不写入前端公开代码或日志。

API Cookie 加密存入 SQLite 的 [cookie_versions 表](../src/api/database.py)，默认数据库为 [data/image-search.db](../data/image-search.db)，可由 [DATABASE_PATH 配置](../src/api/config.py:108)覆写；默认路径不是部署实际路径的确认。解密需要原 [COOKIE_ENCRYPTION_KEY 配置](../src/api/config.py)，备份或迁移时保留数据库和对应密钥，不将密钥写入日志。独立命令行的 Cookie 文件参数与浏览器下载备份不属于 API 活动 Cookie 存储。

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

### 2.2 响应编码与客户端读取

- JSON 响应正文使用 UTF-8 编码。[编码兼容中间件](../src/api/response_encoding.py:1)为未声明字符集的 JSON 响应补充UTF-8字符集，不改写正文、标识或图片地址。
- 调用方应按 UTF-8 解码响应字节，再解析 JSON。不得按系统默认 ANSI、GBK 或 Latin-1 解码，也不要对已经正确解码的中文重复转码。
- Windows PowerShell 5.1 读取旧服务响应时，可能因响应头未声明字符集而出现乱码。旧服务尚未更新时，应显式按 UTF-8 解码原始响应流；仅设置终端输出编码不能修复 HTTP 解码错误。
- 2026-09-17 对部署服务的商品1046759477024进行验证：响应头为 application/json，原始响应字节按 UTF-8 解码后颜色、尺码中文正确；确认本次乱码来自客户端解码，而不是服务器商品数据损坏。
- 历史编码兼容修改记录：5项离线测试通过，覆盖中文与标识保留、错误响应、已有字符集、不改动非JSON响应和JSON扩展媒体类型。**需要部署更新并重启API后，新的响应头才会在服务器生效；尚未进行部署后的在线验证。本轮文档补充未重新测试。**

实现见[响应编码中间件](../src/api/response_encoding.py:1)、[应用接入](../src/api/app.py:1)和[编码测试](../tests/test_response_encoding.py:1)。

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
  "sizes": [
    {"position": 2, "field": "sku2", "name": "尺码", "values": ["M", "L"]}
  ],
  "color_sizes": [
    {
      "color": {"position": 1, "field": "sku1", "name": "颜色", "value": "蓝色"},
      "size_position": 2,
      "size_field": "sku2",
      "size_name": "尺码",
      "values": ["M", "L"]
    }
  ],
  "skus": [
    {
      "sku_id": "900000000000000001",
      "spec_id": "0123456789abcdef0123456789ABCDEF",
      "price": "39.80",
      "currency": "CNY",
      "price_source": "result.data.mainPrice.fields.finalPriceModel.tradeWithoutPromotion.skuMapOriginal[].price",
      "price_basis": "detail_html_sku_original_quote_without_promotion",
      "specifications": [
        {"position": 1, "field": "sku1", "value": "蓝色", "name": "颜色"},
        {"position": 2, "field": "sku2", "value": "M", "name": "尺码"}
      ]
    },
    {
      "sku_id": "900000000000000002",
      "spec_id": "abcdef0123456789abcdef0123456789",
      "price": null,
      "currency": null,
      "price_source": null,
      "price_basis": null,
      "specifications": [
        {"position": 1, "field": "sku1", "value": "蓝色", "name": "颜色"},
        {"position": 2, "field": "sku2", "value": "L", "name": "尺码"}
      ]
    }
  ],
  "sku_count": 2,
  "warnings": ["sku_price_missing", "sku_completeness_unknown"],
  "completeness": "unknown"
}
```

示例第一条SKU具有合成可信报价，第二条缺少报价但保留SKU；[`price`](../src/product_sku/models.py:21) 及对应币种、来源、口径均为空，不回填第一条价格。

### 3.1 顶层字段

| 字段 | 类型 | 说明 |
|---|---|---|
| [`product_id`](../src/api/sku_schemas.py:16) | 字符串 | 当前商品ID |
| [`canonical_url`](../src/api/sku_schemas.py:17) | 字符串 | 规范化HTTPS商品地址 |
| [`seller_user_id`](../src/api/sku_schemas.py:29) | 字符串或空值 | 卖家数字用户ID，不转换为浮点或普通前端数值 |
| [`seller_member_id`](../src/api/sku_schemas.py:30) | 字符串或空值 | 卖家会员ID，保留大小写 |
| [`skus`](../src/api/sku_schemas.py:25) | 数组 | 当前解析得到的有效SKU |
| [`specification_images`](../src/api/sku_schemas.py:31) | 数组 | 同一次查询返回的去重规格选项及可空图片URL，见第3.3节 |
| [`sizes`](../src/api/sku_schemas.py:32) | 数组 | 按明确尺码维度去重的原始值，见第3.4节；无法识别或没有有效SKU时为空数组 |
| [`color_sizes`](../src/api/sku_schemas.py:33) | 数组 | 颜色选项与尺码维度的真实组合关联，见第3.4节；歧义时为空数组 |
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
| [`price`](../src/product_sku/models.py:21) | 十进制字符串或空值 | 已验证来源的未促销原始SKU报价，非商品最低价、非结算价；缺失或不可信时为空，见第3.5节 |
| [`currency`](../src/product_sku/models.py:22) | 字符串或空值 | 当前可信报价契约为CNY；价格为空时币种为空，不为无报价SKU默认补币种 |
| [`price_source`](../src/product_sku/models.py:23) | 字符串或空值 | 非空价格的已验证来源路径；价格为空时也为空 |
| [`price_basis`](../src/product_sku/models.py:24) | 字符串或空值 | 非空价格的已验证报价口径；价格为空时也为空，不将促销价和其他口径混用 |
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
- 当时规格标识实现阶段记录：96项离线回归通过，改动尚未提交推送，未停止运行中的服务。新增字段需部署并重启API才会生效；这是历史记录，不是本轮测试或仓库状态检查。

### 3.3 规格图片URL（按选项去重）

顶层新增 [`specification_images`](../src/api/sku_schemas.py:31) 数组。只返回URL，不下载图片；不在每个尺码的SKU上重复附加颜色图片。

每条记录结构见 [`SpecificationImage`](../src/product_sku/models.py:45)：规格位置、位置字段、规格名称、规格值、可空图片URL。通过这些规格信息与SKU的规格项关联，不能将图片列表下标与SKU数组下标对应。

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

### 3.4 去重尺码与颜色尺码关联

字段模型见 [`SizeDimension`](../src/product_sku/models.py:28) 和 [`ColorSizes`](../src/product_sku/models.py:36)，规则见[独立汇总模块](../src/product_sku/size_summary.py:1)。

| 列表 | 字段 | 类型与说明 |
|---|---|---|
| 尺码维度 | [`position`](../src/product_sku/models.py:29) | 整数，原规格位置 |
| 尺码维度 | [`field`](../src/product_sku/models.py:30) | 字符串，原位置字段 |
| 尺码维度 | [`name`](../src/product_sku/models.py:31) | 字符串，原始维度名称 |
| 尺码维度 | [`values`](../src/product_sku/models.py:32) | 原始值字符串数组，按最终SKU出现顺序去重 |
| 颜色关联 | [`color`](../src/product_sku/models.py:37) | 颜色规格对象，含原位置、字段、名称和值；结构同SKU规格项 |
| 颜色关联 | [`size_position`](../src/product_sku/models.py:38) | 整数，对应尺码维度位置 |
| 颜色关联 | [`size_field`](../src/product_sku/models.py:39) | 字符串，对应尺码维度字段 |
| 颜色关联 | [`size_name`](../src/product_sku/models.py:40) | 字符串，对应原始尺码维度名称 |
| 颜色关联 | [`values`](../src/product_sku/models.py:41) | 该颜色实际返回组合中的尺码原始值，按出现顺序去重 |

- 名称仅为匹配而去掉首尾空白、忽略英文大小写；返回名称和值保持原样。
- 尺码名称有限规则为：尺码、尺寸、大小、size；颜色名称为：颜色、色彩、color、colour。只做完整名称匹配，不按位置、规格值或部分关键词猜测。
- “规格”“型号”“尺码/型号”等模糊名称不当作尺码。尺寸可能是长度、器物大小等，**不保证是衣服尺码**。
- 同一位置字段的名称缺失或不一致时不识别该维度。旧重量表来源没有可信名称，两个汇总列表均为空数组。
- 多个明确尺码维度分别汇总，不混在一个列表里；存在多个颜色或多个尺码维度时不推测配对，颜色关联为空数组。
- 仅一个明确颜色维度和一个明确尺码维度时，按最终SKU中真实组合关联；不生成笛卡尔积、不补齐未返回组合。
- 缺失或空白值不参加汇总；同一维度内按原始值去重，带空格的不同原始值不合并。
- 汇总仅覆盖返回SKU，不保证平台完整尺码范围，也不代表库存、可购买数量或当前可售状态。没有有效SKU时两个列表为空数组。

### 3.5 逐SKU价格口径

实现见[独立价格模块](../src/product_sku/prices.py:9)。每个SKU只返回一个价格，按以下顺序选择：原始报价优先；原始报价缺失、无效或冲突时，回退到同SKU的唯一有效折扣报价。两类报价均不可用才为空。响应不新增字段或回退标记；已有来源、口径字段记录实际选中的来源。

折扣来源为 [`result.global.globalData.model.tradeModel.skuMap[].discountPrice`](../src/product_sku/prices.py:11)，沿用商品身份、SKU标识和规格校验。原始来源为：

[`result.data.mainPrice.fields.finalPriceModel.tradeWithoutPromotion.skuMapOriginal[].price`](../src/product_sku/prices.py:9)

[有界递归遍历](../src/product_sku/parser.py:101)在普通包装对象、数组和嵌套 JSON 字符串中收集通过[商品上下文验证](../src/product_sku/parser.py:36)的完整业务对象；验证仍要求参数、商品详情及交易模型的商品标识与请求一致，并跳过推荐分支。[共享报价收集](../src/product_sku/quote_sources.py:32)只从这些相同可信对象的固定路径读取，不将相邻对象的身份与报价拼接。报价容器支持数组及有界解码后的 JSON 数组，不猜测映射容器语义、不执行脚本。报价行按校验后的SKU标识关联有效交易行，不按数组下标、规格文本或最低价关联；总报价行数也受结构限额约束，越界按原规则清空解析结果。

历史在线验证商品898728774563时，详情价格组件标记逐SKU原价模式，单位字段为“件”，224/224条SKU原始报价存在；示例SKU 5752895764090的原始报价为37.00。该字段是国内1688详情页显示的CNY十进制报价，口径为**未促销原始逐SKU报价**；本轮未重复验证。

- [`tradeModel.skuMap[].priceAmount`](../src/product_sku/prices.py:62) 不作为生产金额，禁止除以100；商品展示最低价、阶梯价及最终结算价不回填。折扣价仅从已验证的同SKU行读取，不按商品最低价推断。
- 返回的 [`price`](../src/product_sku/models.py:21) 为精确十进制字符串，[`currency`](../src/product_sku/models.py:22) 为CNY，[`price_source`](../src/product_sku/models.py:23) 为固定已验证路径，[`price_basis`](../src/product_sku/models.py:24) 为 [detail_html_sku_original_quote_without_promotion](../src/product_sku/prices.py:10)。不再次缩放或补齐小数位；这不是最终结算价，不含数量阶梯、运费、税费、优惠或支付服务费。
- [输入校验](../src/product_sku/prices.py:28)接受正整数或无符号十进制字符串：整数部分1～40位，小数部分如有则1～12位。零（包括字符串0.00）、负数、布尔值、二进制浮点数、科学计数法、空白、正号或其他非法格式均返回空值。没有对应报价时也保留SKU并返回空价格及空币种、来源、口径，不把空值改成0或字符串“null”。
- [候选合并](../src/product_sku/prices.py:82)按来源去重；同一来源多个不同报价或有效与无效/空候选并存时，该来源不可用，按优先级尝试下一来源。均不可用才置空并告警。不同小数表示不归一；重复相同报价不冲突，不删除规格有效的SKU。
- 仅有无效非空候选时可附 [invalid_sku_price](../src/product_sku/prices.py:82)；单纯缺失或空候选不附该警告，但会附下面的缺价提示。缺少已验证报价不会产生旧的sku_price_unverified；旧来源页面也可返回空价格。
- 第一版保留 [SKU价格四字段](../src/product_sku/models.py:21)；第二版转换为 [固定价格对象](../src/api/sku_v2_schemas.py:52)。每次所选版本请求只查询一次，再按该版结构输出，不需要为了读取价格再次请求另一版。

### 3.6 缺价识别与安全诊断

[价源警告](../src/product_sku/quote_sources.py:20)为结果级去重提示，第一版读取顶层警告，第二版读取 [meta.warnings](../src/api/sku_v2_schemas.py:81)。不得将这些提示作为某一SKU的精确失败原因。

| 警告 | 说明 |
|---|---|
| [sku_price_source_missing](../src/product_sku/quote_sources.py:24) | 没有可读取的可信报价数组，包括路径缺失或空值 |
| [invalid_sku_price_schema](../src/product_sku/quote_sources.py:46) | 固定路径结构异常、归属冲突或报价容器不是数组；不猜测映射格式 |
| [invalid_sku_price_rows](../src/product_sku/quote_sources.py:64) | 报价行不是对象、SKU标识非法或显式商品归属不一致 |
| [sku_price_unmatched](../src/product_sku/quote_sources.py:26) | 存在合法报价行标识，但与最终有效SKU没有交集 |
| [sku_price_missing](../src/product_sku/quote_sources.py:28) | 至少一个有效SKU最终没有可信金额；包含全部缺价、部分缺价、无效及冲突情况 |

没有报价时仍保留有效SKU。普通商品与旧来源也会有缺价提示，不再仅返回完整性未知。第二版价格状态继续只有 [available / unavailable](../src/api/sku_v2_converter.py:33)，外部结构不变。

[诊断入口](../src/product_sku/price_diagnostics.py:26)复用可信对象与价源收集，固定白名单输出路径、模式、容器行数、价格存在/缺失/空值、类型和SKU交集。默认不输出金额；[显式诊断脚本](../src/diagnose_quote_evidence.py:25)允许最多三种合法金额样例，禁止输出完整页面、账号、凭据及动态键。

2026-10-07 本轮本地离线修复：系统 Python 完整回归162项通过，新增[价源回归](../tests/test_sku_quote_sources.py:1)覆盖递归、隔离、格式、缺价、冲突、限额、两版响应和诊断安全。历史149项记录保留，不代表本轮数量。未部署、重启、提交或在线查询；商品1031837296171的54个SKU（9色6码）部署价格是否恢复尚未验证，没有本次上游原数据，不能认定本缺陷就是该商品实际根因。

### 3.7 商品1031837296171诊断与修复（2026-10-07）

- [x] 一次真实详情GET，活动Cookie，禁重试；上游200，一个完整可信业务根，54个有效SKU。响应只在内存，日志仅固定白名单统计与少量金额。
- [x] 根因确认：原始报价数组54行、有效ID54、交集54，但原始价格字段54行全部缺失，不是显式空值，也不是关联失败。原价模式为区间报价，单位为件；未发现可据以填充全部SKU的统一原价契约。
- [x] 同SKU折扣报价54个字符串，唯一金额28.00；商品最小/最大价也是28.00，但不用于回填。用户批准原始价优先、折扣价兜底，已实现。
- [x] [结构回归](../tests/test_sku_discount_prices.py:14)重建9色6码54行，金额沿用真实诊断，ID、规格及非金额整数为合成数据。修复后54个价格均为28.00，第二版均为可用；169项系统Python离线回归通过。
- [ ] 运行中API加载及实际接口验证：本轮未重启服务、未部署、未提交或推送，也未再联网。原诊断进程已结束，修复后未重放完整真实HTML；结构回归不能冒充实际API验证。

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

HTTP状态映射以[共享HTTP策略](../src/api/sku_http.py:9)为准。

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

此类响应通常使用详情错误对象，而不是完整SKU结果；定义见[共享请求和异常处理](../src/api/sku_http.py:16)及[查询服务](../src/api/sku_service.py:15)。

| HTTP | 错误码或情形 | 处理 |
|---|---|---|
| 401 | INVALID_API_KEY | 查询密钥缺失或错误，检查X-API-Key |
| 422 | INVALID_PRODUCT_URL或框架参数校验错误 | 检查链接、字段类型、缺少字段或额外字段 |
| 429 | SKU_BUSY | 并发已满，延迟后有限重试 |
| 502 | SKU_QUERY_FAILED | 内部查询异常；服务不回显原始异常或页面 |
| 503 | COOKIE_UNAVAILABLE | 服务没有可用活动Cookie或无法解密；由管理员处理 |
| 503 | SKU_SERVICE_STOPPING | 服务停机中，稍后重试 |
| 504 | SKU_QUERY_TIMEOUT | 等待超时；后台请求结束前仍占并发槽，避免立即密集重试 |

错误示例，结构由[服务异常转换](../src/api/sku_http.py:25)产生：

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

### 6.1 查询启动间隔

- 同一API进程内，所有商品详情查询共用[启动限速器](../src/api/sku_rate_limit.py:7)：相邻查询按随机2～4秒的最小间隔启动，不按商品、调用方或密钥分别计时。
- 首个查询立即启动；空闲时间已超过上一轮间隔时，新查询无需额外等待。网络处理与系统调度可能使实际启动间隔超过4秒。
- 保留现有并发上限，前一个查询尚未完成时，后一个查询可在间隔满足后启动，不改为串行查询。
- 等待启动的查询也占并发槽位，并计入接口等待超时。满额仍立即返回429，不额外建立准入队列；客户端断开或等待超时不会提前释放槽位。
- 此间隔仅控制商品查询的启动，不改变查询内部重试和重定向的原有规则；不影响图搜、独立命令行和诊断客户端。
- 限制按进程计算，现有启动入口为单进程；多个进程或实例不共享计时。更新后需重启现有API服务才能生效。

## 7. 部署与验证记录

- **更新代码后必须重启现有API进程**，才能加载商家字段与新解析逻辑；不要重复启动占用同一端口的服务。
- 复用原有数据库、加密密钥和活动Cookie，不需要新建表或重新上传仍有效的会话。
- 重启后可在服务 /docs 查看接口说明，或在 /openapi.json 核对响应字段。
- 2026-09-17独立新代码客户端实测商品844515661443：上游HTTP 200、24个SKU、48个带名称规格项，两类卖家ID均成功返回。
- 此前商品898728774563已验证224个SKU；新增商家字段本轮在线验证对象为844515661443。
- 历史商家及规格扩展阶段：六个测试文件共96项离线回归通过，包含规格标识原值保留/冲突/空值、商家字段、规格图片去重、图片冲突置空及API契约测试；不是当前最新测试计数，本轮未重新运行。
- 上述在线记录不等于已验证部署网站、反向代理或仍运行的旧API进程。页面结构和会话状态变化可能影响后续结果。

### 7.1 尺码与价格扩展历史记录（2026-10-07）

以下保留此前实现阶段的验证记录，不是本轮文档编辑测得的结果。

- 已完成模型、接口契约、独立尺码汇总、逐SKU价格源接入和异常处理；原请求、状态映射和第一版字段保留，不增加阶梯价。[生产价格契约](../src/product_sku/prices.py:25)已启用，不再处于“单位、币种、口径未验证”的待办状态。
- 商品844515661443在线HTTP200验证：24个交易行的旧 [priceAmount](../src/product_sku/prices.py:58) 均为1，商品展示价为39.80，证明该字段不是金额，未启用除100。
- 商品898728774563在线HTTP200验证：224个有效SKU、224条[已验证来源报价](../src/product_sku/prices.py:9)，价格样例37.00；同页逐SKU模式和“件”单位证据确认价格为CNY十进制显示报价。
- 历史149项离线Python回归通过，覆盖逐SKU关联、空值、零/负值、非法输入、冲突保留SKU、第一版兼容和第二版价格对象转换。
- 历史在线输出仅记录脱敏路径、SKU样例和计数，未保存完整页面、Cookie、密钥、签名或账号信息。
- **部署及部署后验证仍未完成。** 本轮仅补文档，未运行测试或在线查询，未部署、重启、提交或推送；运行中的旧API进程不会自动加载新增代码。

实现参考：[查询路由](../src/api/sku.py:1)、[响应契约](../src/api/sku_schemas.py:15)、[SKU解析器](../src/product_sku/parser.py:1)、[结果模型](../src/product_sku/models.py:1)、[尺码价格测试](../tests/test_sku_sizes_prices.py:1)、[HTTP契约测试](../tests/test_sku_api.py:173)、[有界价格诊断](../src/diagnose_sku_prices.py:1)。

## 8. 第二版结构化接口

### 8.1 请求与兼容

新增 **[POST /api/v2/product-skus](../src/api/sku_v2.py:14)**。请求体仍使用[商品链接请求模型](../src/api/sku_schemas.py:9)，仅提交商品链接；鉴权、活动Cookie、并发上限、启动间隔、等待超时与第一版一致。

[第一版接口](../src/api/sku.py:18)保留原字段和原HTTP契约，不切换为第二版结构。两版共享[同一个查询服务调用](../src/api/sku_http.py:24)，每次请求只查询一次，再执行[无网络、无副作用的结果转换](../src/api/sku_v2_converter.py:39)。不会为了第二版重复查询、补查价格或下载图片；上游原有有限重试与重定向规则不变。

#### 第二版快速调用

最简请求体，两版完全相同；只需选择第二版路径：

```json
{"product_url":"https://detail.1688.com/offer/844515661443.html"}
```

以下示例只负责调用已运行的服务，不包含部署步骤，不使用虚拟环境。通过操作系统或受信任的密钥管理方式，预先为当前客户端进程安全配置SKU_API_URL（完整第二版接口地址）和SEARCH_API_KEY（查询密钥）两个环境变量。不要把密钥写在命令、脚本、URL或日志中；服务端配置文件不会自动导出为客户端Shell环境变量。

Linux调用（响应正文按UTF-8读取，另输出HTTP状态；不要只凭状态200判断成功）：

```bash
curl --silent --show-error --max-time 110 \
  --request POST "$SKU_API_URL" \
  --header "Content-Type: application/json; charset=utf-8" \
  --header "X-API-Key: $SEARCH_API_KEY" \
  --data-raw '{"product_url":"https://detail.1688.com/offer/844515661443.html"}' \
  --write-out '\nHTTP %{http_code}\n'
```

Windows PowerShell调用系统Python：显式使用UTF-8处理输入、终端输出和响应原始字节；HTTP错误正文也保留，便于区分完整结果与详情错误。当前系统Python需可直接调用，无需启用虚拟环境。

```powershell
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
$env:PYTHONIOENCODING = 'utf-8'
@'
import json, os, urllib.error, urllib.request
body = json.dumps({"product_url": "https://detail.1688.com/offer/844515661443.html"}).encode("utf-8")
request = urllib.request.Request(
    os.environ["SKU_API_URL"], data=body, method="POST",
    headers={"Content-Type": "application/json; charset=utf-8", "X-API-Key": os.environ["SEARCH_API_KEY"]},
)
try:
    response = urllib.request.urlopen(request, timeout=110)
except urllib.error.HTTPError as error:
    response = error
with response:
    print("HTTP", response.status)
    payload = json.loads(response.read().decode("utf-8"))
print(json.dumps(payload, ensure_ascii=False, indent=2))
'@ | python -
```

第二版返回后按第8.3节读取；输出包含业务数据，应按调用方的数据权限保管，不能将响应或密钥公开。网络连接等客户端异常不属于接口JSON结果。

### 8.2 完整合成响应

以下是**合成成功获取SKU的结构示例**，不是在线查询结果；业务状态仍为部分成功，因为完整性未知。复用第3节相同商品、规格与报价，第一条价格可用，第二条缺失。真实页面只有命中可信报价时，[金额、币种、来源和口径](../src/api/sku_v2_schemas.py:52)才非空；模型见[第二版响应契约](../src/api/sku_v2_schemas.py:84)。

```json
{
  "status": "partial_success",
  "data": {
    "product": {
      "id": "123456789012",
      "url": "https://detail.1688.com/offer/123456789012.html",
      "main_image": null
    },
    "seller": {
      "user_id": "987654321012345678",
      "member_id": "synthetic_member_01"
    },
    "specifications": [
      {
        "dimension_id": "d1",
        "position": 1,
        "name": "颜色",
        "role": "color",
        "options": [
          {"option_id": "d1_o1", "value": "蓝色", "image_url": "https://img.example/blue.jpg"}
        ]
      },
      {
        "dimension_id": "d2",
        "position": 2,
        "name": "尺码",
        "role": "size",
        "options": [
          {"option_id": "d2_o1", "value": "M", "image_url": null},
          {"option_id": "d2_o2", "value": "L", "image_url": null}
        ]
      }
    ],
    "size_summary": {
      "dimensions": [
        {"dimension_id": "d2", "name": "尺码", "values": ["M", "L"], "option_ids": ["d2_o1", "d2_o2"]}
      ],
      "by_color": [
        {"color_option_id": "d1_o1", "color": "蓝色", "size_dimension_id": "d2", "sizes": ["M", "L"], "size_option_ids": ["d2_o1", "d2_o2"]}
      ]
    },
    "skus": [
      {
        "sku_id": "900000000000000001",
        "spec_id": "0123456789abcdef0123456789ABCDEF",
        "option_ids": ["d1_o1", "d2_o1"],
        "price": {"amount": "39.80", "currency": "CNY", "status": "available", "source": "result.data.mainPrice.fields.finalPriceModel.tradeWithoutPromotion.skuMapOriginal[].price", "basis": "detail_html_sku_original_quote_without_promotion"}
      },
      {
        "sku_id": "900000000000000002",
        "spec_id": "abcdef0123456789abcdef0123456789",
        "option_ids": ["d1_o1", "d2_o2"],
        "price": {"amount": null, "currency": null, "status": "unavailable", "source": null, "basis": null}
      }
    ]
  },
  "meta": {
    "schema_version": "2",
    "source": "detail_html",
    "sku_count": 2,
    "completeness": "unknown",
    "reason": "sku_data_found",
    "warnings": ["sku_completeness_unknown"]
  }
}
```

### 8.3 字段与关联规则

| 字段 | 类型及含义 |
|---|---|
| [`status`](../src/api/sku_v2_schemas.py:85) | 字符串，业务状态；与第一版相同，不等于HTTP状态或逐SKU价格状态 |
| [`data`](../src/api/sku_v2_schemas.py:67) | 固定对象，含商品、商家、规格、尺码汇总和SKU，不省略 |
| [`data.product`](../src/api/sku_v2_schemas.py:7) | 对象；[`id`](../src/api/sku_v2_schemas.py:8)、[`url`](../src/api/sku_v2_schemas.py:9) 为字符串，[`main_image`](../src/api/sku_v2_schemas.py:10) 为字符串或空值 |
| [`data.seller`](../src/api/sku_v2_schemas.py:13) | 对象；[`user_id`](../src/api/sku_v2_schemas.py:14)、[`member_id`](../src/api/sku_v2_schemas.py:15) 均为字符串或空值，彼此独立 |
| [`data.specifications`](../src/api/sku_v2_schemas.py:70) | 维度对象数组；[`dimension_id`](../src/api/sku_v2_schemas.py:25) 字符串，[`position`](../src/api/sku_v2_schemas.py:26) 整数，[`name`](../src/api/sku_v2_schemas.py:27) 字符串或空值，[`role`](../src/api/sku_v2_schemas.py:28) 为color、size或unknown |
| [`options`](../src/api/sku_v2_schemas.py:29) | 每个维度的选项对象数组；[`option_id`](../src/api/sku_v2_schemas.py:19) 字符串，[`value`](../src/api/sku_v2_schemas.py:20)、[`image_url`](../src/api/sku_v2_schemas.py:21) 为字符串或空值 |
| [`data.size_summary.dimensions`](../src/api/sku_v2_schemas.py:48) | 尺码维度对象数组；[`dimension_id`](../src/api/sku_v2_schemas.py:33)、[`name`](../src/api/sku_v2_schemas.py:34) 字符串，[`values`](../src/api/sku_v2_schemas.py:35)、[`option_ids`](../src/api/sku_v2_schemas.py:36) 为一一对应的字符串数组 |
| [`data.size_summary.by_color`](../src/api/sku_v2_schemas.py:49) | 颜色尺码关联对象数组；[`color_option_id`](../src/api/sku_v2_schemas.py:40)、[`color`](../src/api/sku_v2_schemas.py:41)、[`size_dimension_id`](../src/api/sku_v2_schemas.py:42) 为字符串，[`sizes`](../src/api/sku_v2_schemas.py:43)、[`size_option_ids`](../src/api/sku_v2_schemas.py:44) 为一一对应的字符串数组 |
| [`data.skus`](../src/api/sku_v2_schemas.py:72) | SKU对象数组；[`sku_id`](../src/api/sku_v2_schemas.py:61) 字符串，[`spec_id`](../src/api/sku_v2_schemas.py:62) 字符串或空值，[`option_ids`](../src/api/sku_v2_schemas.py:63) 为全部规格选项引用的字符串数组，[`price`](../src/api/sku_v2_schemas.py:64) 为固定对象 |
| [`data.skus[].price`](../src/api/sku_v2_schemas.py:52) | 对象；[`amount`](../src/api/sku_v2_schemas.py:53)、[`currency`](../src/api/sku_v2_schemas.py:54)、[`source`](../src/api/sku_v2_schemas.py:56)、[`basis`](../src/api/sku_v2_schemas.py:57) 为字符串或空值，[`status`](../src/api/sku_v2_schemas.py:55) 为available或unavailable |
| [`meta`](../src/api/sku_v2_schemas.py:75) | 固定诊断对象；[`schema_version`](../src/api/sku_v2_schemas.py:76) 固定字符串“2”，[`source`](../src/api/sku_v2_schemas.py:77) 固定detail_html，[`sku_count`](../src/api/sku_v2_schemas.py:78) 为整数，[`completeness`](../src/api/sku_v2_schemas.py:79) 为unknown、complete或partial，[`reason`](../src/api/sku_v2_schemas.py:80) 字符串，[`warnings`](../src/api/sku_v2_schemas.py:81) 字符串数组；当前解析器完整性为unknown |

- [维度和选项标识](../src/api/sku_v2_schemas.py:24)仅用于**本响应内部关联**，不是上游ID，不保证跨响应稳定；不得替代[SKU ID或上游规格标识](../src/api/sku_v2_schemas.py:61)。选项按维度和原始值去重，只包含最终SKU用到的选项；不同维度的同文本不会合并。
- [角色](../src/api/sku_v2_schemas.py:28)只允许颜色、尺码、未知三类，分别使用 color、size、unknown。按[现有完整名称规则](../src/product_sku/size_summary.py:4)识别，不按固定位置、规格值或部分关键词猜测。名称缺失、不一致或模糊时为未知。
- 同一原位置字段出现缺失名称和唯一明确名称时，[转换器](../src/api/sku_v2_converter.py:45)保留一个名称为空的未知维度，不拆成不合理的重复维度。多个明确名称冲突时保守分开，不强行合并；相关角色保持未知，不生成尺码或颜色配对。
- 旧来源缺少名称和值仍保留[可空选项结构](../src/api/sku_v2_schemas.py:18)和SKU。三维或更多维时，每条SKU保留全部选项引用。多颜色或多尺码维度只保留独立尺码汇总，不猜测配对，不生成额外组合。
- [汇总](../src/api/sku_v2_converter.py:99)直接复用最终SKU的名称识别规则，按准确维度、完整原始值映射引用。原始值不去空格、不改大小写；空白或缺失值不参与汇总。
- [图片](../src/api/sku_v2_converter.py:91)按原位置、字段、名称和值完整关联，不按下标。候选冲突、缺失或无法验证时为空；名称合并后也不能借用另一身份的图片。不以商品主图回填。
- 第二版维度[不输出原位置字段](../src/api/sku_v2_converter.py:75)，SKU也不再输出逐条规格对象或第一版独立图片列表。转换内部仍使用原字段建立身份；客户端不能把局部维度标识当成原字段，也不能仅凭位置重建冲突名称、合并身份或保证第一版往返还原。

#### 按SKU查规格值和图片

1. 从 [`data.skus`](../src/api/sku_v2_schemas.py:72) 选择所需SKU，读取其 [`option_ids`](../src/api/sku_v2_schemas.py:63)。
2. 遍历 [`data.specifications`](../src/api/sku_v2_schemas.py:70) 及每个维度的 [`options`](../src/api/sku_v2_schemas.py:29)，建立选项标识到“所属维度＋选项对象”的查找表。
3. 将SKU的每个选项引用匹配到 [`option_id`](../src/api/sku_v2_schemas.py:19)，读取选项的 [`value`](../src/api/sku_v2_schemas.py:20)、[`image_url`](../src/api/sku_v2_schemas.py:21) 和所属维度的 [`name`](../src/api/sku_v2_schemas.py:27)、[`role`](../src/api/sku_v2_schemas.py:28)。空图片表示没有可信URL，不借用其他选项图片。
4. 第8.2节第一条SKU引用d1_o1和d2_o1，因此对应蓝色＋M；颜色图片来自d1_o1。第二条引用d2_o2，对应L，不依赖SKU或图片数组下标。
5. 三维及更多维必须处理全部引用。第三维可能是材质、型号等；角色未知时保留原名称和值，不强行当作颜色或尺码，也不按第一、第二、第三位置赋予业务角色。

#### 读取去重尺码和颜色尺码

- 全部明确尺码按 [`data.size_summary.dimensions`](../src/api/sku_v2_schemas.py:48) 分维度读取原始值；如需选项或图片，使用对应 [`option_ids`](../src/api/sku_v2_schemas.py:36) 查找上述表，必要时由 [`dimension_id`](../src/api/sku_v2_schemas.py:33) 确认维度，不把不同尺码维度混成一个列表。
- 每种颜色对应尺码读取 [`data.size_summary.by_color`](../src/api/sku_v2_schemas.py:49)：用 [`color_option_id`](../src/api/sku_v2_schemas.py:40) 查颜色选项，用 [`size_dimension_id`](../src/api/sku_v2_schemas.py:42) 确认尺码维度，读取 [`sizes`](../src/api/sku_v2_schemas.py:43) 或对应 [`size_option_ids`](../src/api/sku_v2_schemas.py:44)。只覆盖真实返回组合，不补笛卡尔积。
- 汇总为空不等于没有SKU或没有规格值；名称不明确、旧来源或多颜色/多尺码配对歧义时，应继续显示原规格，不自行猜测汇总。

### 8.4 价格状态及生产限制

每条SKU固定包含[价格对象](../src/api/sku_v2_schemas.py:52)。当前只定义以下两态，不声称已区分缺失、非法、冲突和未验证的逐条原因：

| 状态 | 含义 |
|---|---|
| [available](../src/api/sku_v2_converter.py:19) | 金额是合法正十进制字符串即为可用；不要求币种、来源或口径齐全，不要求必须为原价 |
| [unavailable](../src/api/sku_v2_converter.py:19) | 无法提供可信完整价格；金额、币种、来源和口径全部为空 |

**[生产价格优先级](../src/product_sku/prices.py:82)已启用。** 原始SKU报价优先，其次同SKU折扣报价；不除以100、不使用商品展示价回填、不提供阶梯价或最终结算价。在线证据和限制见第3.5、3.7节。

[结果级价格警告](../src/api/sku_v2_schemas.py:81)可保留非法或冲突提示，但不包含逐SKU原因映射；缺少已验证报价的SKU保持 [unavailable](../src/api/sku_v2_converter.py:33)。第二版不会把结果级警告盲目传播为逐条状态，也不新增逐条错误码。

**顶层 [业务状态](../src/api/sku_v2_schemas.py:85) 不等于 [价格状态](../src/api/sku_v2_schemas.py:55)。** 部分成功仍可含可用价格。调用方只需读取逐SKU的有效金额，不应以币种、来源、口径缺失或不是原价拒绝金额。国内1688本接口按人民币使用，原价和活动价均可用；金额按十进制字符串处理，不可用不是零价。

可复制的合成价格对象片段，与第8.2节两条SKU一致：

```json
{"amount":"39.80","currency":"CNY","status":"available","source":"result.data.mainPrice.fields.finalPriceModel.tradeWithoutPromotion.skuMapOriginal[].price","basis":"detail_html_sku_original_quote_without_promotion"}
```

```json
{"amount":null,"currency":null,"status":"unavailable","source":null,"basis":null}
```

第二版[转换校验](../src/api/sku_v2_converter.py:19)只校验金额为正十进制字符串，币种、来源、口径仅保留响应兼容，不影响金额可用性，也不要求必须是原价。不重新查询商品，源输入零值与格式限制以第3.5节为准。第一版直接返回内部金额，同样不依赖这些附加字段。

2026-10-07 用户确认：保持两版现有响应形状，取消附加价格字段作为可用条件。[回归](../tests/test_sku_v2_converter.py:141)覆盖无元数据、空元数据、原价及活动价金额均可用；非法及非正金额仍不可用。

### 8.5 HTTP与错误契约

两版复用[HTTP映射和错误处理](../src/api/sku_http.py:10)：

| HTTP | 响应结构及情形 |
|---|---|
| 200 | 第二版完整结构：成功、部分成功、数据源不适用；最后一种可没有SKU |
| 502 | 上游受限、解析失败、网络失败返回第二版完整结构；服务或转换内部异常返回原详情错误结构 |
| 503 | 上游要求登录返回第二版完整结构；Cookie不可用、服务停止返回原详情错误结构 |
| 401 / 422 / 429 / 504 | 继续使用原详情错误契约，分别为鉴权、输入、共享并发或等待超时错误 |

请求或服务错误不包装成HTTP 200。框架输入校验仍可能返回详情错误列表。没有SKU时仍返回[空规格、空汇总和空SKU数组](../src/api/sku_v2_converter.py:116)，不省略第二版数据和元信息。完整业务结果的HTTP 200、502和503均在[OpenAPI响应模型](../src/api/sku_v2.py:13)中声明。

以下为HTTP 200数据源不适用的完整合成空结果，不是详情错误，也不是成功获取SKU：

```json
{
  "status": "source_not_applicable",
  "data": {
    "product": {"id": "123456789012", "url": "https://detail.1688.com/offer/123456789012.html", "main_image": null},
    "seller": {"user_id": null, "member_id": null},
    "specifications": [],
    "size_summary": {"dimensions": [], "by_color": []},
    "skus": []
  },
  "meta": {"schema_version": "2", "source": "detail_html", "sku_count": 0, "completeness": "unknown", "reason": "field_missing", "warnings": []}
}
```

服务错误仍采用第5.2节的[详情错误结构](../src/api/sku_http.py:25)，例如Cookie不可用的HTTP 503响应只含详情错误对象，不带第二版数据对象。先判断是否为完整结果，再分别处理业务状态或详情错误；HTTP 502/503本身不足以判断正文结构。

### 8.6 完成记录与待验证事项（2026-10-07）

- 已完成[独立模型](../src/api/sku_v2_schemas.py:1)、[纯转换](../src/api/sku_v2_converter.py:1)、[第二版路由](../src/api/sku_v2.py:1)、[共享HTTP策略](../src/api/sku_http.py:1)与应用接入。第一版字段和请求兼容保留；[生产价格契约](../src/product_sku/prices.py:25)已启用。
- 历史记录：149项离线Python测试通过；覆盖价格源关联、精确十进制、异常/冲突保留SKU、第一版字段和第二版价格对象。本轮未重新运行。
- 历史在线记录：真实详情解析商品898728774563返回HTTP 200、224条SKU；第一版转换224/224条有非空[价格及币种、口径](../src/product_sku/models.py:21)，第二版转换224/224条为 [available](../src/api/sku_v2_converter.py:33)。脱敏样例金额37.00、币种CNY、口径 [detail_html_sku_original_quote_without_promotion](../src/product_sku/prices.py:10)。两版使用同一内存结果，未为第二版重复查询上游；本轮未重新在线检查。
- [x] 本轮文档补充：修正价格状态矛盾及合成示例，补版本选择、UTF-8调用、字段读取、价格检查、迁移和空结果说明。
- [ ] 部署更新、重启既有API并验证部署站点第二版路径、响应编码与报价字段；历史离线及独立客户端记录不能代替部署验证。
- 本轮仅文档编辑，没有测试、网络查询、部署、重启、提交或推送；既有运行进程不会自动加载新增代码。

### 8.7 第一版到第二版迁移映射

请求体及请求头不变，切换接口路径并按下表调整读取。标识仍按字符串保存，空值仍保留；第二版局部选项标识不能替代上游SKU或规格标识。

| 第一版读取路径 | 第二版读取路径 | 迁移处理 |
|---|---|---|
| [`product_id / canonical_url / main_image`](../src/api/sku_schemas.py:16) | [`data.product.id / url / main_image`](../src/api/sku_v2_schemas.py:7) | 商品字段移入商品对象 |
| 顶层 [`seller_user_id / seller_member_id`](../src/api/sku_schemas.py:29) | [`data.seller.user_id / member_id`](../src/api/sku_v2_schemas.py:13) | 两种标识分别读取，不互相替代 |
| [`skus[].sku_id / spec_id`](../src/product_sku/models.py:16) | [`data.skus[].sku_id / spec_id`](../src/api/sku_v2_schemas.py:60) | 保留真实上游标识 |
| [`skus[].specifications`](../src/product_sku/models.py:17) | [`data.skus[].option_ids`](../src/api/sku_v2_schemas.py:63) → [`data.specifications[].options`](../src/api/sku_v2_schemas.py:29) | 由引用查维度、原值与图片，不按数组下标 |
| 顶层 [`specification_images`](../src/api/sku_schemas.py:31) | [`data.specifications[].options[].image_url`](../src/api/sku_v2_schemas.py:21) | 不再有独立图片列表；同一选项共享URL |
| 顶层 [`sizes`](../src/api/sku_schemas.py:32) | [`data.size_summary.dimensions`](../src/api/sku_v2_schemas.py:48) | 按维度读取原值及选项引用 |
| 顶层 [`color_sizes`](../src/api/sku_schemas.py:33) | [`data.size_summary.by_color`](../src/api/sku_v2_schemas.py:49) | 颜色、尺码维度与选项均改为局部引用，真实组合规则不变 |
| [`skus[].price`](../src/product_sku/models.py:21) | [`data.skus[].price.amount`](../src/api/sku_v2_schemas.py:53) | 标量变为对象中的金额，增加逐条价格状态 |
| [`currency / price_source / price_basis`](../src/product_sku/models.py:22) | [`price.currency / source / basis`](../src/api/sku_v2_schemas.py:54) | 移入每条SKU价格对象；不可用时全部为空 |
| 顶层 [`sku_count / completeness / reason / warnings / source`](../src/api/sku_schemas.py:22) | [`meta.sku_count / completeness / reason / warnings / source`](../src/api/sku_v2_schemas.py:75) | 数量及诊断元信息移入元对象；新增固定版本字符串 |
| 顶层 [`status`](../src/api/sku_schemas.py:18) | 顶层 [`status`](../src/api/sku_v2_schemas.py:85) | 业务语义及HTTP映射不变，不与价格状态混用 |
| 规格项原 [`field`](../src/product_sku/models.py:9) | [不输出该字段](../src/api/sku_v2_converter.py:75) | 第二版保留位置、可空名称、角色与局部维度标识；不声称完整往返还原第一版身份 |

迁移后优先核对“同一SKU的全部选项引用能查到原值和图片”“每条价格状态单独检查”“结果级警告只作为结果诊断”。无需同时请求两版来拼接一次结果，也不要因选项数量变化而补造SKU。
