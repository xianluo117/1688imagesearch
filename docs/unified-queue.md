# 统一 1688 队列使用说明

## 1. 范围与部署边界

API 的图片上传、图搜商品查询、SKU 查询共用一个 SQLite 持久化队列、一个调度器和请求控制器。两版同步 SKU 入口也先入队，再等待结果。独立命令行及诊断进程不自动加入 API 队列，不受它的全局速率协调。

- 默认总任务并发 1，三类排队中加执行中总容量 100。并发占满时排队，容量满才拒绝新任务。
- 按持久化序号领取依赖已满足的任务；先领取不代表提高并发后仍先完成。图搜商品任务仍须显式引用成功的上传任务。
- 所有实际 1688 请求共享随机 2～4 秒启动间隔，包括首次请求、轮询、网络重试、令牌刷新重试和重定向。等待许可后才生成时间戳及签名。首个许可可立即取得，实际间隔可能更长。
- 既有退避和共享间隔都须满足，不叠加旧 SKU 启动限速。外部图片下载不消耗 1688 请求许可，但占上传任务执行名额和预算。
- **只支持单进程、单调度器。禁止多 Uvicorn/Gunicorn 工作进程、多实例或旧分类型消费者共用数据库并行消费。** SQLite 持久化不等于跨进程请求限速协调。
- 降低并发、增大间隔不能保证避免验证码、登录失效或上游限流。

实现入口：[`UnifiedQueueScheduler`](../src/api/queue_scheduler.py:18)、[`RequestGuard`](../src/api/request_guard.py:57)、[`QueueStoreMixin`](../src/api/queue_store.py:61)。

## 2. 配置

以 [`Settings`](../src/api/config.py:56) 和 [`.env.example`](../.env.example) 为准。系统环境同名变量优先于配置文件；配置修改由运维停旧进程后重新单进程启动，不支持在线热调参。

| 配置 | 默认值 | 含义与范围 |
|---|---:|---|
| [`GLOBAL_WORKER_COUNT`](../src/api/config.py:145) | 1 | 全局任务并发，整数 1～32 |
| [`REQUEST_INTERVAL_MIN_SECONDS`](../src/api/config.py:146) | 2 | 全局请求最小启动间隔秒数 |
| [`REQUEST_INTERVAL_MAX_SECONDS`](../src/api/config.py:147) | 4 | 全局请求最大随机间隔秒数；两项均大于 0、不超过 3600，最小值不得大于最大值 |
| [`MAX_QUEUED_TASKS`](../src/api/config.py:131) | 100 | 三类未结束任务合计容量，整数 1～100000 |
| [`RATE_LIMIT_COOLDOWN_SECONDS`](../src/api/config.py:148) | 60 | 默认限流冷却秒数，大于 0、不超过 86400 |
| [`UPLOAD_TASK_TIMEOUT_SECONDS`](../src/api/config.py:132) | 240 | 上传有效执行预算秒数 |
| [`PRODUCT_TASK_TIMEOUT_SECONDS`](../src/api/config.py:133) | 180 | 图搜有效执行预算秒数 |
| [`SKU_TASK_TIMEOUT_SECONDS`](../src/api/config.py:149) | 180 | SKU 有效执行预算秒数；三类预算均大于 0、不超过 86400 |
| [`SKU_QUERY_TIMEOUT_SECONDS`](../src/api/config.py:144) | 90 | 同步 SKU 入队后 HTTP 等待秒数，有限正数 |
| [`SKU_HTTP_TIMEOUT`](../src/api/config.py:143) | 20 | 单次 SKU 网络请求超时秒数，大于 0、不超过 120 |
| [`SEARCH_HTTP_TIMEOUT`](../src/api/config.py:138) | 90 | 单次 MTOP 网络请求超时秒数，有限正数 |
| [`TASK_RETENTION_SECONDS`](../src/api/config.py:134) | 86400 | 终态后保留秒数，正整数 |

**全局配置是唯一调度权威。** 旧 [`UPLOAD_WORKER_COUNT`](../src/api/config.py:129)、[`PRODUCT_WORKER_COUNT`](../src/api/config.py:130)、[`SKU_MAX_CONCURRENCY`](../src/api/config.py:142) 已弃用，不叠加额度、不改变全局默认并发 1。建议移除；仍设置时会执行旧范围校验，分别为 1～2、1～2、1～8，非法旧值仍会导致配置失败。

## 3. SKU 同步与异步接口

所有下列接口使用 [`X-API-Key`](../src/api/sku.py:13) 请求头，值为 [`SEARCH_API_KEY`](../src/api/config.py:126)，不能用 Cookie 上传密钥。

### 3.1 同步兼容入口

[`POST /api/v1/product-skus`](../src/api/sku.py:18) 和 [`POST /api/v2/product-skus`](../src/api/sku_v2.py:13) 均先入队。等待期内完成时仍返回对应版本的业务结果与原 HTTP 映射，不包任务对象。

入队后默认等待 90 秒，包含排队、执行和保护等待。到期返回 HTTP 504，错误码 [`SKU_QUERY_TIMEOUT`](../src/api/sku_service.py:65)；响应详情同时包含 [`task_id`](../src/api/sku_service.py:32) 和 [`query_url`](../src/api/sku_service.py:32)，响应头 [`Location`](../src/api/sku_http.py:42) 指向同一查询位置。使用该地址查询原任务，**后台继续执行，不要因为 504 立即重建任务**。HTTP 等待者断开也不取消任务。

客户端或反向代理先超时，可能收不到上述任务编号；需要可靠重试时从第一次提交就带幂等键，或直接使用异步接口。默认同步客户端及代理读取超时可设为 110 秒，目的是收到服务端 90 秒等待结果，不保证任务在 110 秒内完成。

### 3.2 异步三个路由

| 操作 | 路由 | 返回 |
|---|---|---|
| 提交 | [`POST /api/v2/sku-tasks`](../src/api/queue_api.py:63) | HTTP 202、任务对象，响应头提供查询位置；幂等重放也返回 202，状态可能已是终态 |
| 查询 | [`GET /api/v2/sku-tasks/{task_id}`](../src/api/queue_api.py:75) | HTTP 200、任务对象；不存在、类型不符或已清理为 404 |
| 取消 | [`DELETE /api/v2/sku-tasks/{task_id}`](../src/api/queue_api.py:81) | 当前任务对象；执行中取消不保证立即进入终态 |

提交体仅含 [`product_url`](../src/api/sku_schemas.py:12)，与同步接口相同。无需先图搜。建议每 1～2 秒查询一次任务，终态停止轮询。

任务对象定义见 [`SkuTaskResponse`](../src/api/queue_api.py:24)：

| 字段 | 读取方式 |
|---|---|
| [`task_id / query_url`](../src/api/queue_api.py:25) | 保存任务编号和轮询地址 |
| [`status / cancel_requested`](../src/api/queue_api.py:26) | 任务状态及取消请求标记 |
| [`created_at / updated_at / started_at / finished_at`](../src/api/queue_api.py:28) | Unix 秒时间戳，开始及完成时间可为空 |
| [`result`](../src/api/queue_api.py:33) | 可空的第二版 SKU 完整业务结果，HTTP 金额沿用向上取整展示 |
| [`error`](../src/api/queue_api.py:34) | 可空错误对象，包含错误码与说明 |
| [`scheduler`](../src/api/queue_api.py:35) | 当前全局保护状态，不是任务自身状态 |

任务状态为 [`queued / running / succeeded / failed / cancelled`](../src/api/queue_store.py:22)。有效 SKU 非空且业务状态为成功或部分成功才映射任务成功；其他业务结果映射失败，仍保留结构化结果。例如数据源不适用：同步入口仍可返回 HTTP 200，但异步任务为失败。查询任务 HTTP 200 只表示查询成功。读取结果字段和两版价格契约见 [`merchant-sku-api.md`](merchant-sku-api.md)。

### 3.3 提交错误

| HTTP | 错误 | 处理 |
|---|---|---|
| 409 | [`IDEMPOTENCY_CONFLICT`](../src/api/sku_service.py:51) | 更正键或参数，不盲目重试 |
| 422 | [`INVALID_IDEMPOTENCY_KEY`](../src/api/sku_http.py:29) | 修正幂等请求头 |
| 429 | [`QUEUE_FULL`](../src/api/sku_service.py:55) | 三类共享容量已满，退避后重试；不是单独 SKU 并发忙 |
| 503 | [`COOKIE_UNAVAILABLE`](../src/api/sku_service.py:47) | 管理员检查活动 Cookie |
| 503 | [`SKU_SERVICE_STOPPING`](../src/api/sku_service.py:43) | 停机中，不接纳新任务 |

输入和鉴权错误仍沿用各接口原契约。

## 4. 幂等提交与保留期限

以下创建入口支持可选请求头 [`Idempotency-Key`](../src/api/sku_http.py:26)：图片上传、图搜商品任务、两版同步 SKU、异步 SKU。键长度为 1～255，不能全为空白；按传入原值比较，不自动去除首尾空格。

- 键在**同一数据库全局共享**，不按路由、调用方或密钥隔离。类型与规范化参数都相同才返回原任务；跨类型或参数不同返回 HTTP 409。
- SKU 两版同步与异步同属 SKU 类型，同键同规范化商品地址复用同一任务；HTTP 商品链接规范为 HTTPS，去除查询参数和片段。同步入口按所请求版本展示，异步查询统一展示第二版。
- 不传键则每次提交新任务；不按商品地址永久缓存或合并。失败、取消任务也可被同键复用；明确要重做时使用新键。
- 幂等重放仍须通过接口鉴权、输入、服务接纳和适用的 Cookie 检查，不是绕过检查的结果读取接口。
- 默认终态完成后保留 24 小时，每小时清理一次；不是到点立即删除。排队和执行中的任务不会被清理；有依赖的父任务保留到依赖解除，因此可能更久。
- 任务、业务结果和关联幂等记录一起清理。键没有独立的“提交后 24 小时”期限；清理后同键可创建新任务，旧查询返回 404。调用方应及时保存结果。

实现见 [`_enqueue()`](../src/api/queue_store.py:72)、[`cleanup_queue_tasks()`](../src/api/queue_store.py:308) 和 [`queue_idempotency`](../src/api/queue_schema.py:27)。

## 5. 状态统计、冷却与人工恢复

| 操作 | 路由 | 鉴权 |
|---|---|---|
| 队列统计 | [`GET /api/v1/queue`](../src/api/queue_api.py:91) | 搜索密钥 |
| 人工恢复 | [`POST /api/v1/queue/resume`](../src/api/queue_api.py:98) | 搜索密钥，无需请求体 |
| 详细健康 | [`GET /api/v1/health`](../src/api/app.py:150) | 搜索密钥 |
| 基础存活 | [`GET /health`](../src/api/app.py:146) | 无需鉴权；不代表队列可执行 |

队列响应包含 [`counts`](../src/api/queue_store.py:299) 五类任务计数、[`accepting / capacity`](../src/api/queue_store.py:303)、[`global_workers`](../src/api/queue_api.py:94) 及 [`scheduler`](../src/api/queue_store.py:304)。详细健康响应包含同类队列快照及全局并发；旧 [`upload_workers / product_workers`](../src/api/app.py:158) 固定为 0，不再代表独立工作池。

全局状态与原因持久化：

| 状态 | 含义 |
|---|---|
| [`normal`](../src/api/queue_schema.py:35) | 可领取任务并申请请求许可 |
| [`cooldown`](../src/api/queue_schema.py:35) | 限流冷却，默认 60 秒；可信上游等待指示更长时至少遵守该时长，再次限流可延长截止时间，到期可自动恢复 |
| [`paused`](../src/api/queue_schema.py:35) | 验证或登录异常暂停，优先于冷却，须人工显式恢复 |

[`reason / until / updated_at / cooldown_remaining`](../src/api/queue_api.py:16) 分别表示原因、冷却截止 Unix 时间、更新时间和冷却剩余秒数。暂停不是倒计时；接纳状态正常也不代表当前会执行任务。

故障任务保留失败原因，不无限自动重试；未开始任务继续排队。已在途网络请求不会被强杀，后续请求必须再次取得许可。

人工处理顺序：查看原因，在浏览器完成登录或验证，必要时上传新的有效 Cookie，再调用人工恢复接口并检查状态。**Cookie 更新和服务重启均不自动解除暂停。** 人工恢复清除保护状态，不重做终态失败任务；未解决上游原因可能再次暂停或冷却。图搜商品任务继续使用上传时绑定的 Cookie 版本，上传新 Cookie 不会改写旧依赖。

## 6. 超时、取消与关闭

四种时间不要混用：

1. 排队时间：领取前不消耗执行预算，默认没有排队到期删除。
2. 同步 HTTP 等待：入队后计时，包含排队和保护等待；到期 504 只结束本次等待。
3. 有效执行预算：领取后计时；普通间隔等待、下载、网络及处理时间计入。**仅任务实际在请求入口等待暂停或冷却解除的时间扣除**；别的任务触发全局保护时，本任务仍在途的网络或下载时间不扣除。
4. 单次网络超时：约束一次 HTTP 操作，不是整个任务或排队时长；多次请求、重试、退避可使任务耗时更长。

恢复保护后先检查取消和预算。预算耗尽不再发新请求，但不能杀死底层阻塞操作；操作实际结束前仍占执行名额，不让迟到成功覆盖超时或取消。

排队取消立即终结；执行中先设置 [`cancel_requested`](../src/api/queue_store.py:218)，阻塞操作结束后再终结和释放。重复取消终态任务只返回当前状态。

正常关闭停止接纳和领取，保留排队任务，阻止后续新请求并等待实际在途操作结束，不提前释放线程或槽位。在途任务可能以停止错误结束，不保证停机时全部成功。不要把短强杀超时当成正常排空。

## 7. 停机升级与恢复

以下是运维操作说明，不表示本次文档交付已经部署。

1. 停止旧 API 和所有旧上传、图搜、SKU 消费进程，确认不再消费；禁止滚动共跑新旧消费者。
2. 完整备份现有 SQLite 数据库及对应原加密密钥；保留 [`DATABASE_PATH`](../src/api/config.py:128)、[`COOKIE_ENCRYPTION_KEY`](../src/api/config.py:116) 与两类接口密钥。
3. 按模板设置全局配置，移除弃用并发项；不要把旧的两个图搜工作池及 SKU 并发相加作为新默认值。
4. 只启动一个新 API 进程。启动时在原 SQLite 中自动创建统一队列、幂等和保护状态表并迁移旧任务，不需另建数据库或人工重新分配任务编号。
5. 使用详细健康、队列统计及原任务查询核对状态和结果。若原有暂停仍存在，先处理原因，再人工恢复。在线查询与部署后验证由运维另行安排。

迁移保留旧上传、图搜任务 ID、结果、依赖和取消标记，重复初始化不重复导入。未结束且已请求取消的任务恢复为取消，不重新执行；未取消的执行中任务恢复排队，终态不重新执行。迁移实现见 [`initialize_queue()`](../src/api/queue_schema.py:53)。

**异常重启采用至少一次执行，不保证恰好一次。** 上游已收到请求但本地尚未保存终态时，重启恢复可能再次上传或查询；幂等键只防重复提交新任务，不能消除同一任务恢复时的上游重复请求。请求间隔计时也不是跨进程持久化协调。不得在运行中再次执行启动恢复，或启动旧消费者尝试消化已迁移队列。

## 8. 交付验证记录

历史交付（2026-10-08）：子任务全量离线 255 项通过、41.234s，API 专项 49 项通过、19.583s；父任务此前全量 255 项通过、39.766s。历史文档子任务仅修改说明和模板，这些数量不作为本次实测，也不相加。

本次独立验收（2026-10-08）：重新核对实际生命周期、路由、请求控制、存储和配置。发现并修复请求许可检查在数据库等待期间发生关闭后仍可能放行的竞态，增加 3 项回归，并标注旧两阶段计划已经被统一队列方案替代。

| 本次执行 | 结果 | 耗时 |
|---|---|---:|
| 修改前全量离线 | 255 项通过 | 40.668s |
| 关闭竞态修复前复现 | 1 项执行，1 项预期失败 | 0.014s |
| 修复后全量离线 | **258 项通过，0 失败、0 错误、0 跳过** | **44.756s** |

两次全量均使用 Windows PowerShell 强制 UTF-8、系统 Python 3.14、模拟上游和临时 SQLite，并在进程内拦截真实 HTTP 传输及环境文件加载。测试客户端依赖弃用警告和异步调试慢任务提示未影响通过，未调整依赖。

详细检查项、修复文件及未验证边界见 [实施计划本次验收记录](../plans/unified-1688-queue.md:37)。未读取秘密配置、执行在线请求、部署、重启或提交；真实生产数据库迁移、强杀/断电、真实会话、代理断连及上游可用性未验证，离线通过不能代替部署验收。
