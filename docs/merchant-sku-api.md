# 一次查询商家 ID、规格图片 URL 与 SKU ID API

## 1. 用途与标识说明

**只调用一次 POST /api/v1/product-skus，提交一个1688商品详情链接，即在同一个响应中返回商家ID、规格图片URL和SKU ID。无需分别调用商家、图片或规格接口，也不需要额外开启参数。**

同时返回规格名称、规格值和位置，以及带维度标识的去重尺码、按颜色关联的尺码和逐SKU可空价格字段。无需先发起图搜任务，无需轮询。服务器从本次详情查询的数据中提取这些信息；请求可能按既定规则发生重定向或有限网络重试，但不会为了商家、规格图片、尺码或价格另查接口，也不会下载图片。**当前价格单位、币种和口径未验证，价格返回空值；不提供阶梯价。**

| 需要的数据 | 在同一响应中的读取位置 |
|---|---|
| 商家数字用户ID | 顶层 [`seller_user_id`](../src/api/sku_schemas.py:29) |
| 商家会员ID | 顶层 [`seller_member_id`](../src/api/sku_schemas.py:30) |
| SKU ID | [`skus`](../src/api/sku_schemas.py:25) 数组内每条记录的 [`sku_id`](../src/product_sku/models.py:16) |
| 规格图片URL | 顶层 [`specification_images`](../src/api/sku_schemas.py:31) 数组内的 [`image_url`](../src/product_sku/models.py:50) |
| 去重尺码 | 顶层 [`sizes`](../src/api/sku_schemas.py:32)，按维度分组 |
| 每种颜色对应尺码 | 顶层 [`color_sizes`](../src/api/sku_schemas.py:33)，只关联真实返回组合 |
| 逐SKU价格 | [`price`](../src/product_sku/models.py:21) 与 [`currency`](../src/product_sku/models.py:22)，当前为空值 |

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

### 2.2 响应编码与客户端读取

- JSON 响应正文使用 UTF-8 编码。编码兼容中间件为未声明字符集的 JSON 响应补充 `Content-Type: application/json; charset=utf-8`，不改写正文、标识或图片地址。
- 调用方应按 UTF-8 解码响应字节，再解析 JSON。不得按系统默认 ANSI、GBK 或 Latin-1 解码，也不要对已经正确解码的中文重复转码。
- Windows PowerShell 5.1 读取旧服务响应时，可能因响应头未声明字符集而出现乱码。旧服务尚未更新时，应显式按 UTF-8 解码原始响应流；仅设置终端输出编码不能修复 HTTP 解码错误。
- 2026-09-17 对部署服务的商品1046759477024进行验证：响应头为 application/json，原始响应字节按 UTF-8 解码后颜色、尺码中文正确；确认本次乱码来自客户端解码，而不是服务器商品数据损坏。
- 本次兼容修改已通过5项离线测试，覆盖中文与标识保留、错误响应、已有字符集、不改动非JSON响应和JSON扩展媒体类型。**需要部署更新并重启API后，新的响应头才会在服务器生效；尚未进行部署后的在线验证。**

实现见[响应编码中间件](../src/api/response_encoding.py)、[应用接入](../src/api/app.py)和[编码测试](../tests/test_response_encoding.py)。

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
      "price": null,
      "currency": null,
      "price_source": null,
      "price_basis": null,
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
| [`price`](../src/product_sku/models.py:21) | 十进制字符串或空值 | 单条SKU交易报价，非商品最低价、非结算价；当前单位和语义未验证，返回空值 |
| [`currency`](../src/product_sku/models.py:22) | 字符串或空值 | 已验证报价币种；不默认人民币，当前为空值 |
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
- 最新96项离线回归通过。新增字段需部署并重启API才会生效；本轮规格标识改动尚未提交推送，未停止运行中的服务。

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

### 3.5 逐SKU价格及未验证限制

实现见[独立价格模块](../src/product_sku/prices.py:1)。**当前生产价格契约未启用，所有SKU的价格、币种、来源和口径均为空值。** 金额字段为正整数或与商品价数值相符，不构成单位、币种或口径证据。

- 只从通过当前商品归属和SKU规格组合验证的交易行收集候选，不读取推荐商品价格、不回填商品最低价或区间价。
- 后续仅在可靠证据确认单位、币种和报价语义并经代码审查后，才可启用内部价格契约；页面自行出现单位字段不会自动启用。
- 有已验证契约时使用十进制精确运算，返回十进制字符串，不使用二进制浮点金额；缺失、非法、单位未知或候选冲突均为空值。
- 当前精确金额输入仅支持正整数和普通正十进制字符串；不接受布尔值、浮点数、科学计数法、非有限值或带空白金额。
- 同一SKU存在不同价格候选，或有效候选与缺失/非法候选并存时，不选择第一个。价格失败不删除有效SKU，不改变规格标识、规格值冲突或图片规则。
- 当前存在未验证金额时附带 [`sku_price_unverified`](../src/product_sku/prices.py:64)；已验证契约下非法候选附带 [`invalid_sku_price`](../src/product_sku/prices.py:68)，冲突附带 [`conflicting_sku_price`](../src/product_sku/prices.py:66)。
- 即使未来返回非空报价，也不保证是结算价；不含数量阶梯、运费、税费、优惠或最终支付金额。本接口不增加阶梯价字段。

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
- 最新六个测试文件共96项离线回归通过，包含规格标识原值保留/冲突/空值、商家字段、规格图片去重、图片冲突置空及API契约测试。
- 上述在线记录不等于已验证部署网站、反向代理或仍运行的旧API进程。页面结构和会话状态变化可能影响后续结果。

### 7.1 本轮尺码与价格扩展（2026-10-07）

- 已完成模型、接口契约、独立尺码汇总、独立保守价格模块和接口文档；原请求、状态映射和已有字段保留，无阶梯价。
- 全部123项离线Python回归通过，耗时1.736秒。新增14项尺码/价格用例与1项HTTP契约用例，覆盖顺序去重、真实颜色组合、缺名、多维歧义、旧来源、空结果、金额精度/单位、非法金额、候选缺失/冲突、推荐隔离、规格标识与图片兼容；已有请求、状态、限速和线程生命周期用例也通过。
- 测试使用合成价格契约验证十进制与冲突算法，**不表示实际页面单位或报价口径已获验证**。测试仅有第三方客户端弃用提示，不影响通过结果。
- 本轮仅一次上游详情请求，商品844515661443返回HTTP 200、1个已验证模型、24条有效SKU；24条交易金额均为正整数。检查的固定字段中，交易模型、商品详情与交易行均未发现币种、金额单位、价格缩放、价格类型或价格描述声明，项目现有代码也未提供可靠定义。因此不推断除100、不默认人民币，生产价格保持空值。
- 此前观察到商品报价39.80与单件价格候选38.80/36.80并存，不能用数值接近或匹配证明金额口径。本轮未输出或保存完整页面、Cookie、密钥、签名或账号信息。
- 在线诊断在本轮功能接入前执行，只验证上游结构与价格限制；本轮新尺码字段和API响应通过离线回归验证，未再发起网络请求。
- 未运行前端构建，未部署、未重启、未提交；线上旧进程不会自动加载本轮字段。

实现参考：[查询路由](../src/api/sku.py:1)、[响应契约](../src/api/sku_schemas.py:15)、[SKU解析器](../src/product_sku/parser.py:1)、[结果模型](../src/product_sku/models.py:1)、[尺码价格测试](../tests/test_sku_sizes_prices.py:1)、[HTTP契约测试](../tests/test_sku_api.py:173)、[有界价格诊断](../src/diagnose_sku_prices.py:1)。

## 8. 第二版结构化接口

### 8.1 请求与兼容

新增 **[POST /api/v2/product-skus](../src/api/sku_v2.py:14)**。请求体仍使用[商品链接请求模型](../src/api/sku_schemas.py:9)，仅提交商品链接；鉴权、活动Cookie、并发上限、启动间隔、等待超时与第一版一致。

[第一版接口](../src/api/sku.py:18)保留原字段和原HTTP契约，不切换为第二版结构。两版共享[同一个查询服务](../src/api/app.py:118)，每次请求只查询一次，再执行[无网络、无副作用的结果转换](../src/api/sku_v2_converter.py:42)。不会为了第二版重复查询、补查价格或下载图片；上游原有有限重试与重定向规则不变。

### 8.2 完整合成响应

以下是**合成结构示例**，不是在线查询结果。金额保持空值，符合当前生产限制；模型见[第二版响应契约](../src/api/sku_v2_schemas.py:89)。

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
        "price": {"amount": null, "currency": null, "status": "unavailable", "source": null, "basis": null}
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
    "warnings": ["sku_completeness_unknown", "sku_price_unverified"]
  }
}
```

### 8.3 字段与关联规则

| 字段 | 类型及含义 |
|---|---|
| [`status`](../src/api/sku_v2_schemas.py:90) | 与第一版相同的业务状态，不等同于HTTP状态 |
| [`data.product`](../src/api/sku_v2_schemas.py:7) | 商品ID、规范链接、可空主图；ID为字符串 |
| [`data.seller`](../src/api/sku_v2_schemas.py:13) | 两种可空卖家ID，彼此独立，不改变原验证规则 |
| [`data.specifications`](../src/api/sku_v2_schemas.py:24) | 维度列表：局部维度标识、原位置、可空名称、角色、去重选项 |
| [`options`](../src/api/sku_v2_schemas.py:18) | 每项包含局部选项标识、可空原始值、可空图片URL |
| [`data.size_summary.dimensions`](../src/api/sku_v2_schemas.py:32) | 明确尺码维度；原始值与选项引用一一对应 |
| [`data.size_summary.by_color`](../src/api/sku_v2_schemas.py:39) | 明确颜色选项对应的真实尺码；尺码值与选项引用一一对应 |
| [`data.skus`](../src/api/sku_v2_schemas.py:61) | 原SKU ID、可空上游规格标识、全部规格选项引用、固定价格对象 |
| [`meta`](../src/api/sku_v2_schemas.py:77) | 版本字符串、来源、返回SKU数、完整性、原因及结果级警告 |

- [维度和选项标识](../src/api/sku_v2_schemas.py:24)仅用于**本响应内部关联**，不是上游ID，不保证跨响应稳定；不得替代[SKU ID或上游规格标识](../src/api/sku_v2_schemas.py:61)。选项按维度和原始值去重，只包含最终SKU用到的选项；不同维度的同文本不会合并。
- [角色](../src/api/sku_v2_schemas.py:28)只允许颜色、尺码、未知三类，分别使用 color、size、unknown。按[现有完整名称规则](../src/product_sku/size_summary.py:4)识别，不按固定位置、规格值或部分关键词猜测。名称缺失、不一致或模糊时为未知。
- 同一原位置字段出现缺失名称和唯一明确名称时，[转换器](../src/api/sku_v2_converter.py:48)保留一个名称为空的未知维度，不拆成不合理的重复维度。多个明确名称冲突时保守分开，不强行合并；相关角色保持未知，不生成尺码或颜色配对。
- 旧来源缺少名称和值仍保留[可空选项结构](../src/api/sku_v2_schemas.py:18)和SKU。三维或更多维时，每条SKU保留全部选项引用。多颜色或多尺码维度只保留独立尺码汇总，不猜测配对，不生成额外组合。
- [汇总](../src/api/sku_v2_converter.py:108)直接复用最终SKU的名称识别规则，按准确维度、完整原始值映射引用。原始值不去空格、不改大小写；空白或缺失值不参与汇总。
- [图片](../src/api/sku_v2_converter.py:101)按原位置、字段、名称和值完整关联，不按下标。候选冲突、缺失或无法验证时为空；名称合并后也不能借用另一身份的图片。不以商品主图回填。

### 8.4 价格状态及生产限制

每条SKU固定包含[价格对象](../src/api/sku_v2_schemas.py:53)。当前只定义以下两态，不声称已区分缺失、非法、冲突和未验证的逐条原因：

| 状态 | 含义 |
|---|---|
| [available](../src/api/sku_v2_converter.py:19) | 内部结果已有合法正十进制字符串、币种、来源和口径；原样返回，不再次换算 |
| [unavailable](../src/api/sku_v2_converter.py:19) | 无法提供可信完整价格；金额、币种、来源和口径全部为空 |

**[生产价格契约](../src/product_sku/prices.py:21)仍为未启用状态。尚未真实输出SKU价格，不除以100、不默认人民币、不用商品展示价回填。** 合法合成价格的可用状态仅由离线测试覆盖，不构成真实币种、单位或报价口径的证据。

[结果级价格警告](../src/api/sku_v2_schemas.py:84)保留原有未验证、非法或冲突详情。其中[未验证警告](../src/product_sku/prices.py:63)表示返回SKU中至少一条存在未验证金额候选，**不表示每条SKU都有候选或都已确诊未验证**；缺失候选的SKU不能据此标为未验证。现有内部结果没有逐SKU诊断信息，第二版不会把全局警告盲目传播为逐条状态。

### 8.5 HTTP与错误契约

两版复用[HTTP映射和错误处理](../src/api/sku_http.py:10)：

| HTTP | 响应结构及情形 |
|---|---|
| 200 | 第二版完整结构：成功、部分成功、数据源不适用；最后一种可没有SKU |
| 502 | 上游受限、解析失败、网络失败返回第二版完整结构；服务或转换内部异常返回原详情错误结构 |
| 503 | 上游要求登录返回第二版完整结构；Cookie不可用、服务停止返回原详情错误结构 |
| 401 / 422 / 429 / 504 | 继续使用原详情错误契约，分别为鉴权、输入、共享并发或等待超时错误 |

错误不包装成HTTP 200。框架输入校验仍可能返回详情错误列表。没有SKU时仍返回[空规格、空汇总和空SKU数组](../src/api/sku_v2_converter.py:127)，不省略第二版数据和元信息。完整业务结果的HTTP 200、502和503均在[OpenAPI响应模型](../src/api/sku_v2.py:12)中声明。

### 8.6 本轮完成记录（2026-10-07）

- 已完成[独立模型](../src/api/sku_v2_schemas.py:1)、[纯转换](../src/api/sku_v2_converter.py:1)、[第二版路由](../src/api/sku_v2.py:1)、[共享HTTP策略](../src/api/sku_http.py:1)与[应用接入](../src/api/app.py:119)。未修改第一版模型、解析器或价格生产契约。
- 全部147项离线Python测试通过，耗时2.622秒。新增24项：独立[接口测试9项](../tests/test_sku_api_v2.py:16)、[转换测试15项](../tests/test_sku_v2_converter.py:23)。覆盖第一版响应不变、鉴权/输入/业务状态/异常、一次查询、共享服务/并发/启动间隔/等待超时、去重引用、准确汇总、三维歧义、旧来源空值、图片身份和价格限制。
- 本轮没有重新抓取上游，没有部署、重启、提交或推送，没有在线验证第二版路径。既有运行进程不会自动加载新增路由；历史在线记录不代表第二版已在线可用。
