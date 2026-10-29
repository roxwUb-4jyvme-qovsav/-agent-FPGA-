# FPGA 多 Agent 协同开发平台开发规格书

版本 1.0　编制日期 2026 年 10 月 29 日　对应总体设计 1.0

本规格书用于两名开发者拆分任务、实现接口和组织验收。平台交付一个在用户本机运行的网页工程工作台，贯通需求澄清、软硬件契约、参考原理图、RTL、独立仿真、产品上位机、需求变更与工程交付。完整的模型、MCP、知识库、Skills 和插件管理均属于首版范围。

本文把总体设计中的建议细化为开发默认值。开发者可以据此实施；对外调用额度、真实板卡电气参数和账号权限仍须由项目负责人登记。本文及随附 JSON 是规格与测试夹具，不代表平台功能已经实现、模型已经接通或 FPGA 工程已经通过验收。

## 1 使用方法和规格效力

### 1 1 阅读与交付结构

开发者 A 负责平台、界面、数据、权限、模型、编排和管理生态；开发者 B 负责 RTL 内核适配、协议、仿真、KiCad、报价与设备数据；成员 C 负责资料、需求记录和验收组织，不按第三名编码人员计算产能。A 与 B 共同冻结接口，不以各自的临时实现替换统一契约。

正文说明行为和跨字段规则，contracts 目录保存 JSON Schema，api 目录保存 OpenAPI 3.1 及资源目录，protocol 目录保存寄存器表、错误码和完整二进制向量，examples 目录保存未批准的示例。acceptance 目录提供验收目录，planning 目录提供工作包，sdk 目录说明扩展适配规范。文档工具只用于生成和校验上述交付物。

出现冲突时，以用户已确认范围和总体设计的范围决定为先；接口字段以本版 Schema 为准；状态准入、权限、事务和协议时序以正文为准。发现冲突须提交规格变更，禁止用运行中的偶然行为定义规范。总体设计的示例字符串 ID 在本规格中统一替换为 UUID。

### 1 2 用语与版本

“必须”是阻断验收条件；“建议”允许实现者记录理由后调整；“待登记”是外部条件，没有值时阻断相应动作。schema_version 为 1.0，HTTP API 为 v1，设备线协议为 capture/1.0，插件 engine_api 为 1.0。这四种版本独立演进，不能互相代替。

正文时间默认采用北京时间展示，数据库保存带时区 UTC 时间；金额以十进制定点值计算，通过 JSON 十进制字符串传输。所有示例中的测试供应商、零哈希及 example.invalid 均为夹具，不能进入正式运行的已批准记录。

## 2 首版范围与发布条件

### 2 1 首版功能清单

| 需求组 | 必须交付的行为 | 验收标识前缀 |
|---|---|---|
| 需求与输入 | 文本、图片和文档导入，来源定位，单位校验，关键问题确认，需求冻结 | AC-REQ |
| 契约与变更 | 版本化契约、依赖图、8 到 16 通道影响分析、增量重建、证据失效、回滚 | AC-REV |
| 平台与编排 | 本机启动、认证授权、任务持久化、租约、恢复、取消、费用与审计 | AC-PLT |
| 模型与编程 | 国产模型配置、API Provider、旧内核显式适配、有限修复和真实调用证据 | AC-MDL |
| 工程案例 | 8 与 16 通道采集、独立仿真、协议、网页上位机、离线 UART 解码 | AC-RTL |
| 参考图纸 | KiCad 源工程、网络表、BOM、ERC、独立连接检查与图纸导出 | AC-CAD |
| 知识与报价 | 轻量内置检索、外部 API、多源同口径报价、来源权限与快照 | AC-KB 和 AC-QTE |
| 管理与生态 | 模型、MCP、知识源、Skills、插件、目录、凭证、预算、版本及完整生命周期 | AC-EXT |
| 协作与交付 | GitHub 差异和批准发布，工程包、恢复演练及端到端流程 | AC-GIT 和 AC-E2E |

首版以一个可配置数字采集与协议分析仪为工程案例。SPI、I²C、预触发、完整 PCB、真实板卡、制造、全云部署和通用形式证明进入后续路线。首版仍须保留 BoardProfile、厂商工具及制造工件的扩展接口。

### 2 2 完成定义

只有全部 P0 验收项通过、阻断问题清零、来源与许可证完整、示例可复现，才允许标记完整首版。验收结果逐项记录 PASS、FAIL、NOT_RUN、INDETERMINATE 或 STALE。软件 mock、历史回放、真实 RTL 仿真和硬件实测必须分开统计。

11 月 10 日是已登记的比赛提交节点，不是缩减范围的理由。比赛构建可冻结实际通过的内容，仍未通过的 P0 必须保留在缺项表。第三方适配器契约通过、账号获批与真实连接通过分别记录；报价导入可以支撑报价功能验收，不能证明供应商在线接口已接通。

## 3 运行架构与代码边界

### 3 1 本机部署

浏览器经同源入口访问前端和 API。推荐开发端口为前端 5173、API 8765、PostgreSQL 5432，默认绑定 127.0.0.1；这些是开发约定，不要求修改当前机器配置。前端开发服务器代理 /api 到后端。正式本机包使用一个同源入口，避免依赖宽泛 CORS。启动器逐项显示数据库、调度器、模型配置、EDA、CAD 的真实健康状态。

后端使用模块化单体，Worker 独立进程主动领取任务。PostgreSQL 保存业务事实、任务及 outbox；pgvector 支撑内置检索；大文件存受控工件目录。LangGraph 保存节点检查点，但不得直接决定批准、余额或版本当前指针。模型调用全局默认并发 2，每个 EDA Worker 重任务并发 1。

Windows 原生 KiCad 可作为 CAD Worker。EDA 优先选择已确认可用的隔离 Linux 环境，支持组合须经过烟测再写入 toolchain.lock.json。没有隔离环境时允许管理页、mock 和静态契约检查运行，生成代码的编译执行保持 BLOCKED；不能静默改用拥有主机权限的任意脚本。

### 3 2 模块目录与依赖方向

| 目录 | 责任 | 禁止越界 |
|---|---|---|
| apps/web | 工作台、管理页、上位机视图、SSE 客户端 | 不保存服务商 Key，不直接操作 EDA |
| apps/api | HTTP、会话、项目 ACL、事务入口 | 不在请求进程运行生成代码或加载未知插件 |
| packages/contracts | Schema、类型、协议生成器与语义校验 | 不依赖 UI 或特定 Provider |
| packages/orchestration | 节点、持久化计划、变更 DAG、上下文构建 | 不越过网关调用工具 |
| packages/model_gateway | API 适配、身份、预算、usage | 不决定工程 PASS |
| packages/tool_gateway | 能力策略、调用去重、工具与插件桥接 | 不暴露任意 Shell 或绝对路径接口 |
| packages/knowledge | 解析、索引、ACL、检索与外接 | 不从检索分数推导电气正确性 |
| workers/eda 和 workers/cad | 固定工具模板、受限执行、结果取证 | 不挂载主机密钥或保留验收资产 |
| products/analyzer | 生成产品的 RTL、协议库与上位机数据层 | 不与平台管理员权限混用 |
| acceptance_service | 独立保留测试与签发证据 | 不让生成侧读取答案或修改断言 |
| legacy | 原模板只读来源与适配测试 | 不改写历史来源锁或测试成绩 |

模块间通过有类型接口传递工件引用。不能把数据库连接、任意文件句柄或高权限凭证序列化到 TaskEnvelope。工具环境版本、Python/Node 依赖及第三方固定提交在实施烟测后锁定，本规格不虚构当前安装版本。

## 4 数据模型与持久化规则

### 4 1 公共字段与工件哈希

资源 ID 使用 UUID，更新 version 为递增整数，etag 为资源版本的强标识。时间采用 RFC3339，哈希采用小写 SHA256。JSON 工件按 UTF8、无 BOM 的最终落盘字节计算哈希，不把语言字典的内存顺序当作跨语言规范。批准引用存储后的不可变工件，不对客户端任意重排的 JSON 计算批准摘要。

ArtifactRef 只包含 id、sha256、kind。后端按 ID 解析受控路径并再次检查项目权限和哈希。原始文件名只作展示元数据，禁止用于拼接磁盘路径。数据库只提交已完整写入临时目录、校验哈希并原子提升的工件。

### 4 2 数据表与关键约束

| 数据表组 | 核心关系 | 数据库约束 |
|---|---|---|
| users teams project_members | 用户加入团队与项目，项目角色独立 | 项目与用户组合唯一，不能删除最后一个管理员 |
| projects revisions | 当前发布指针，父版本，需求和契约引用 | 父子同项目；批准版本内容不可更新 |
| artifacts artifact_dependencies | 内容、生成者、输入和工具版本 | 项目内引用检查；依赖不得成环 |
| runs tasks task_attempts | 一次运行多个任务，每次领取独立 attempt | task 与 attempt 唯一；fencing_token 单调递增 |
| tool_invocations outbox events | 外部调用、待投递事件、项目事件流 | invocation_id 唯一；事务中写 outbox |
| approvals approval_executions | 摘要、范围、有效期、决定和使用记录 | 批准对象不变；相同执行键唯一 |
| model_profile_versions bindings | 模型与角色配置快照 | 已引用版本只读；编辑产生新版本 |
| extension_versions installations registries | 包、来源、依赖、审核和启用指针 | id 加 version 不可对应两个不同哈希 |
| documents chunks index_generations | 文档版本、ACL、向量模型及索引 | 删除与撤权后所有检索路径不可见 |
| quote_snapshots budget_ledger | 报价证据、预留、结算、未知消耗 | 金额 numeric；账目只追加冲正 |
| device_sessions captures audit_logs | 会话占用、采集数据和审计 | 同设备最多一个写会话；审计不可由普通用户删除 |

主外键之外必须检查 project_id 一致，建议使用组合外键保护关键关联。管理员身份不自动获得所有项目的外发许可。数据库迁移用 Alembic，迁移编号、前置版本和备份要求进入发行记录；不把删除列当作可无损回退。

### 4 3 Schema 和语义校验

Schema 拒绝未知字段、负金额、非法 UUID、路径穿越和 channels 与 sample_bytes 不匹配。领域校验器继续检查：批准人和时间同时为空或同时存在；引用属于同项目；已批准需求没有未解关键问题；GPIO 范围合法；期限先后合理；报价阶梯数量递增；物理板卡型号、电气证据和引脚均完整。

BoardProfile.kind 为 virtual 时 programming_allowed 必须为 false，不能填真实器件或引脚。physical 也不能仅靠 programming_allowed=true 获得烧录权限，还需电气审核、工具配置和动作批准。Schema 通过只证明结构可接受，不证明其工程事实正确。

## 5 身份 权限与批准

### 5 1 角色矩阵

| 动作 | viewer | developer | reviewer | admin |
|---|---|---|---|---|
| 查看已授权样例与证据 | 是 | 是 | 是 | 按项目授权 |
| 编辑草案和创建隔离运行 | 仅受限样例运行 | 是 | 是 | 是 |
| 批准需求和发布 | 否 | 否 | 是 | 是 |
| 安装扩展和登记外部地址 | 否 | 否 | 否 | 是 |
| 管理密钥 预算和成员 | 否 | 否 | 否 | 是 |
| 发起 GitHub 发布请求 | 否 | 是 | 是 | 是 |
| 执行已批准 GitHub 写入 | 否 | 网关代执行 | 网关代执行 | 网关代执行 |

表中“是”还必须满足项目成员身份、数据策略和状态准入。UserInput.global_role 为 admin 或 user，控制全局治理；项目成员角色为 admin、developer、reviewer 或 viewer，控制该项目行为。Session 同时返回 global_role 和 project_roles。全局管理员不自动成为所有项目成员，项目管理员也不因此获得全局密钥管理权限。评委演示账号可通过专门的演示能力运行指定样例，不能获得通用 developer 权限。

OpenAPI 的 /admin 路径按 global_role 校验，/projects/{project_id} 路径按该项目有效角色校验，Worker 和 verifier 使用独立服务身份。根 /projects 列表只返回可见项目；创建要求所属团队成员身份，创建者在新项目中成为 admin。首版初始化一个本机团队，成员由全局管理员维护；不需要任意多租户团队自助注册。x-roles 不是跨项目统一的单一用户等级，不能替代逐资源 ACL 和动作策略。

登录使用 HttpOnly、SameSite=Strict 会话 Cookie。本机 HTTP 仅回环运行；部署 HTTPS 时启用 Secure。写请求要求 CSRF token，登录另检验 Origin。密码使用成熟自适应哈希算法，不提供默认通用管理员口令。初始化生成一次性凭据并强制更新。Worker 使用独立令牌，不能调用人类批准接口。

### 5 2 批准对象

Approval 必须绑定 actor、project_id、revision_id、action、target_sha256、作用域和 expires_at。默认有效期建议 30 分钟；执行前重新检查对象哈希、权限、版本、预算和批准是否已撤销。批准后 diff 或仓库 base SHA 改变，原批准立即失效。模型不得填写 approved_by 来批准自己。

只读、写隔离候选和受限仿真可按项目政策自动执行。冻结基线、发布版本、插件启用、未批准资料外发和 GitHub 发布分别记录人类决定。实际采购、烧录及改变电气条件不属于首版自动执行动作。管理员可以兼任审核者，但高风险变更要求第二名成员复核。

## 6 工作流与任务状态机

### 6 1 阶段与输入输出

| 节点 | 必备输入 | 输出与准入 |
|---|---|---|
| ingest | 原始文本和上传引用 | 可定位片段、解析诊断与内容哈希 |
| clarify | 片段与用户回答 | RequirementSpec；关键未知项解决才可冻结 |
| architect | 批准需求 | 约束计算、模块图、候选设计 |
| contract | 设计与验收条件 | SystemContract、AcceptanceSpec、寄存器和协议 |
| implement_rtl host cad | 批准契约和固定模板 | 独立候选目录中的代码或图纸 |
| select_quote | BOM 约束和授权来源 | 候选报价、缺项及同口径比较 |
| verify | 候选哈希与冻结测试 | 逐层证据，不接受模型自评 |
| diagnose_repair | 公开失败诊断和允许补丁范围 | 最小候选补丁，最多三轮 |
| change | 新要求和已发布版本 | 语义差异、失效图、重建计划 |
| deliver | 当前版本证据和批准 | 工程包、差异、已知限制和发布索引 |

TaskEnvelope 冻结模型配置版本、Skills、扩展、输入工件、输出 Schema、写目录、能力、预算和绝对 deadline。AgentResult.completed 表示节点输出齐全，不能自动把 Revision 改成 VERIFIED 或 RELEASED。生成侧没有独立验收资产的读取能力。

### 6 2 合法状态迁移

Revision 使用 DRAFT → WAITING_APPROVAL → FROZEN → BUILDING → VERIFYING → VERIFIED → RELEASED。校验失败进入 FAILED；需要用户回答进入 NEEDS_INPUT；内容被新版本替代后保留历史标记。已冻结版本不能改内容，修订创建新的 revision。VERIFIED 的必要条件是该版本所有阻断测试的有效证据均为 PASS。

Task 使用 QUEUED → LEASED → RUNNING → SUCCEEDED 或 FAILED；需要输入进入 WAITING_INPUT；取消先进入 CANCEL_REQUESTED，确认进程树结束后才进入 CANCELLED。租约丢失进入 LOST，不立即断言原进程已经停止。恢复前由调度器对账，安全可重跑的任务建立新 attempt；外部副作用不自动重试。

### 6 3 调度事务与恢复

领取任务用数据库行锁和 SKIP LOCKED。默认心跳 15 秒、租约 60 秒，服务端时间裁决。每次发放新 attempt 增加 fencing_token；心跳和完成必须携带 worker_id、attempt、token，且与当前有效租约一致。超时、旧 token 或不匹配 revision 返回 409 STALE_LEASE，保留事件而不提升工件。

写任务状态、账目和 outbox 属于同一事务。SSE 事件由 outbox 投递，允许重复到达，消费者按事件 ID 去重。模型、仿真和报价调用按 invocation_id 去重；数据库提交失败后先查服务和工件结果，不凭检查点把同一写操作再执行一次。

取消必须设置数据库标记、通知 Worker、终止所属进程树、保存已有日志并结算已知费用。超过 30 秒未确认时显示取消未确认，并阻止设备复用；不能把超时 UI 直接显示为已取消。模型超时后可能仍计费，账本保留未知消耗。

任务读取返回 TaskRecord，包含强类型状态、TaskEnvelope、attempt 历史、取消确认、等待原因和 TaskQuestion。问题由服务端分配稳定 UUID，Agent 提出的问题在持久化后才成为可回答对象。JobComplete.result 可为 AgentResult 或 ToolResult，服务端核对任务种类，不能用工具结果替代 Agent 的 needs_input。

回答通过任务 answers 接口提交 Answer。事务内核验问题属于当前 task 和 revision、问题未解决及用户权限，再写回答和审计；全部阻断问题解决后排入新 attempt，保留原结果与等待记录，不复用已经失效的租约。需求澄清接口使用同一答案校验逻辑，但更新 RequirementSpec 草案，不改变已冻结版本。

## 7 HTTP API 与事件规范

### 7 1 通用约定

API 前缀为 /api/v1。创建返回 201，异步受理返回 202，读取返回 200，无正文操作返回 204。POST 除登录外携带 Idempotency-Key；相同身份、路径、键与请求体重复返回原结果，相同键不同请求体返回 409 IDEMPOTENCY_CONFLICT。建议保存去重记录至少 24 小时，超过窗口先查询原资源再由用户选择重试。

PUT 替换完整草案 payload，PATCH 项目只允许 name 和 description，禁止更改当前版本指针。PUT、PATCH、DELETE 携带 If-Match；缺失返回 428 PRECONDITION_REQUIRED，不匹配返回 412 VERSION_CONFLICT。commands 使用 expected_version 在事务内校验，并按动作再次鉴权。正式响应返回 ETag，客户端更新成功后替换本地版本。

集合返回 items 和 next_cursor；limit 默认 20、最大 100，按 created_at 和 id 稳定分页。游标绑定过滤条件和权限范围。未知或越权项目资源统一返回 404；同项目内缺少特定动作能力可返回 403。客户端不能从错误差异枚举其他项目。

### 7 2 接口族与边界

| 接口族 | 请求类型 | 行为限制 |
|---|---|---|
| /projects 与 /revisions | ProjectInput 和 RevisionInput | 草案创建；freeze 和 release 通过命令与批准 |
| /runs 与 /changes | RunRequest 和 ChangeRequest | 预算预留、输入快照、取消、重试与影响预览 |
| /approvals | ApprovalRequest 和 ApprovalDecision | 决定接口限审核角色；校验目标哈希 |
| /uploads 与 /documents | UploadInit 和 DocumentInput | 上传后先隔离，不自动解析执行或入库 |
| /artifacts 与 /evidence | 只读资源与内容下载 | 客户端不能写入验证证据或伪造 PASS |
| /collections 与 /knowledge/search | Collection 和 SearchRequest | 发布索引代际、ACL 预过滤和外部映射 |
| /bindings 与 /device-sessions | Binding 和 DeviceSessionInput | 当前配置不修改在途快照，设备保持独占 |
| /admin/model-profiles 等 | 资源独立 payload | 草案编辑、测试、批准、启停与版本回滚 |
| /admin/secrets | SecretInput 与 SecretMeta | 写入真实值，读取只返回元数据 |
| /worker/jobs | JobClaim Heartbeat Complete | 独立令牌、租约、能力和 fencing token |

逐路径、方法、请求及响应见 api/openapi.json。resource-catalog.json 定义每类可用动作，接口不使用一个不受约束的 payload 修改所有资源。已发布配置的 PUT 创建下一草案版本，不能原地改变已引用版本。DELETE 对有历史引用的配置只做停用或返回 RESOURCE_IN_USE，不级联删除运行记录。

项目成员增删限 admin，developer 和 reviewer 不能因拥有编辑通用资源的代码路径而提权。报价写入须检查来源授权，设备 hardware 模式在缺少板卡条件时返回 HARDWARE_NOT_BOUND。Run 的 snapshot 和 tasks 只读接口提供冻结配置与任务明细；viewer 只看脱敏摘要，不能读取完整 TaskEnvelope。OpenAPI 描述接口结构，以上领域限制同样是实现要求。

Worker 工件通过 /worker/jobs/{job_id}/uploads 初始化、上传内容和完成，令牌只允许当前租约任务的输出类型和配额。完成后返回 ArtifactRef，task 完成时再次核验 attempt、token、哈希和归属；上传成功本身不提升工程状态。验收 Worker 也通过任务限定上传报告，但独立使用 verifier 凭证向 /verification/evidence 提交签发证据；普通 Worker 令牌不能使用该接口。保留测试任务只发给登记的验收 Worker 能力，不进入生成 Worker 队列。

### 7 3 错误和事件

| HTTP 状态 | 典型错误码 | 用户处理 |
|---|---|---|
| 400 和 422 | INVALID_SCHEMA INVALID_UNIT INVALID_STATE | 定位字段或阶段问题后重提 |
| 401 和 403 | SESSION_EXPIRED CAPABILITY_DENIED | 重新登录或申请相应权限 |
| 409 和 412 | STALE_LEASE RESOURCE_IN_USE VERSION_CONFLICT | 获取当前状态，不盲目覆盖 |
| 413 和 415 | FILE_TOO_LARGE UNSUPPORTED_MEDIA | 调整上传或使用专用工件通道 |
| 428 和 429 | PRECONDITION_REQUIRED RATE_LIMITED | 提供版本，按 Retry-After 重试 |
| 503 | TOOL_UNAVAILABLE PROVIDER_UNAVAILABLE | 保留任务并显示基础设施故障 |

ApiError 固定包含 code、message、request_id、retryable、field_errors。限流与服务故障不改写为 RTL 功能失败。SSE 使用 Event.id 作为 id，data 为 Event JSON；每项目序号单调递增但允许间隙，客户端按 id 去重。15 秒发送注释心跳，断线携带 Last-Event-ID；默认保留 7 天，游标过期返回 410，客户端先拉取快照再恢复流。

事件摘要只包含允许该用户查看的阶段与资源引用，不传凭证或完整文档。数据库状态是最终事实，未收到流事件不代表任务没有完成。首版批处理采集可以通过 REST 和 SSE 完成，不强制为设备数据另建 WebSocket；未来实时流应另行冻结消息、背压与重连规范。

## 8 多模态需求与附件

### 8 1 上传和解析

单文件上限 25 MiB，即 26214400 字节；每次最多 10 个，单任务总量 100 MiB；PDF 最多 200 页。该取值统一总体方案中的 25 MB 工程默认值，使用二进制字节数便于实现。波形大文件走管理员配置的工件通道，不通过聊天附件绕过限额。

上传分为 init、PUT content、complete。ticket 默认 10 分钟有效，绑定会话、项目、期望字节数和哈希；完整内容校验后才返回可用工件。按魔数、扩展名和 MIME 交叉校验。ZIP 默认最多 1000 个成员、解压总量 200 MiB、单项膨胀比 100；拒绝绝对路径、上级目录、链接和重名覆盖。

解析 Worker 无网络、宏和脚本执行权限；默认内存 1 GiB、单文件 120 秒，可按受控配置调整。PDF 保留页码，DOCX 保留段落和表格定位，XLSX 保留工作表和单元格，图片保留 OCR 框。公式无缓存时标记未知，禁止默认当作 0。解析失败保留原文件和结构化错误，允许用户重试或改用明确支持的文件。

### 8 2 需求字段与冻结

每条事实保存原始文本、规范键、标准化值和单位、来源及 confirmed/proposed/unknown 状态。RequirementSpec 的 value 是可回溯字符串，数值计算必须通过领域解析器转换成带单位定点数。先检查通道、速率、采样模式、链路和板卡要求，再问次要界面偏好。

需求冻结前，不能存在会改变架构的未解问题。仿真示例允许用户接受虚拟板与默认采样参数作为已确认事实；实板参数不能由仿真默认值隐式补齐。OCR 中的电压、引脚和小数点必须展示原图定位并复核。回答更新字段后重新检查单位、范围和冲突，不靠聊天中的“好的”改动未指明的事实。

## 9 国产模型网关与旧内核迁移

### 9 1 Provider 接口

ModelProvider 接收 request_id、不可变 ModelProfile 版本、受授权 messages、输出 Schema、max_output_tokens、deadline 和预算预留 ID。返回 ModelResponse，并保留服务端请求 ID、requested_model、actual_model、usage 与完成状态。文本生成、向量与重排是不同方法，能力不满足时拒绝调用。

初始配置沿用总体方案中的 DeepSeek Flash、Qwen Plus 和可选 Pro 角色分配。展示名、真实 API model_id、服务商端点和实际返回身份分别存储，实施时通过账号实测锁定；本规格不重新声明任何尚未测试的模型名称或价格为可用事实。自定义兼容接口仍必须经过国产来源白名单和数据外发策略检查。

重试只处理明确可重试的 429、连接建立失败和部分 5xx，建议最多 2 次、指数退避并带随机抖动；所有尝试共享绝对 deadline 和预算。401、无权限、拒绝、截断、空输出及非法结构分别返回稳定状态。产生未知计费或外部结果不明确时先对账，不无限重发。

### 9 2 预算与 usage

每个调用前按已登记价格版本和输出上限预留费用；调用后用真实 usage 结算，不能把缓存或推理子项无条件重复累加。缺失 usage 使用 null 并标 unknown，估算单列。失败、取消及重试同样入账。未知消费保守保留预留，直至对账或管理员有记录地冲正。

额度同时检查全局、项目、用户和 Run；默认真实调用预算为 0，管理员登记非零上限与凭证后才可消费。健康接口不得循环发起付费请求。配置编辑只影响新任务，在途记录持有配置版本和单价版本，不因后台换模型而漂移。

### 9 3 Coding Runner 适配

旧模型链路保留。新 Runner 通过显式构造参数注入 ModelProvider、ToolGateway、ArtifactStore 和 BudgetLedger，禁止全局 monkey patch。候选提取、端口检查和诊断可逐个包装复用；固定器件、TopModule、目录、spec 哈希探针与全局审计状态必须分别消除耦合。

旧服务 rc=0 或 HTTP 200 仅表示进程或传输状态。新链路检查候选是否完整、接口是否匹配、工具报告是否可信、验收是否通过。先验证计数器和 FIFO，再验证完整采集仪；旧部署测试中的失败保留为历史，不在本轮规格交付时改写为通过。

## 10 统一契约与变更算法

### 10 1 事实来源

RequirementSpec 记录用户目标，SystemContract 记录逻辑与接口，BoardProfile 记录板卡绑定，AcceptanceSpec 记录验证条件。协议、寄存器、RTL 参数、上位机类型、网络连接计划和测试清单均引用契约哈希。自动生成目录与手写扩展分开，生成器不得覆盖手写代码。

跨字段规则包括 sample_bytes=channels/8、capture_bytes=channels/8×depth、sample_rate=clock_hz/divider、采样窗口=depth/sample_rate。8 路 10 MHz 是 80 Mbit/s，16 路是 160 Mbit/s；115200 波特 8N1 理想负载上限 11520 字节每秒。4096 样本覆盖 409.6 微秒，上传裸样本至少约 0.356 或 0.711 秒，另计协议开销。

### 10 2 变更与复用规则

变更请求保留原话，先生成规范字段差异，再运行确定性约束计算与依赖图传递遍历。发生变化的输入节点与其所有下游工件在当前版本的有效性投影中标 STALE，保留失效原因。Agent 可以补充风险，不得删除声明式依赖或手动将失效证据改成 PASS。

批准变更后创建新 revision，不直接覆盖旧版。按拓扑重建，输入不变的节点只有在工件、测试、工具环境、生成器与配置相关哈希均相同且授权仍有效时才可复用。复用保存来源证据引用与复核记录，不改写旧报告的 revision_id。任何变更至少执行当前版本系统冒烟测试；无法可靠计算影响时执行全回归。

| 8 到 16 路受影响项 | 必须动作 |
|---|---|
| 采样和缓存 | 位宽 8→16，样本字节 1→2，总缓冲 4096→8192 字节 |
| 协议和上位机 | READ 分包容量重算，低字节在前，16 路 UI 与 CSV 列更新 |
| 图纸和 BOM | 输入连接器及通道实例扩展，网络与数量重新核对 |
| 报价 | 按新 BOM 生成新快照，旧报价保留并标历史 |
| 验证 | 新位宽、触发通道 15、掩码、边界、系统冒烟重跑 |
| 可复用模块 | UART 波特率逻辑等仅在依赖完全不变时复用；带宽检查仍重跑 |

回滚只切换已批准版本及其匹配工件索引。不得拼接旧 RTL 与新协议。旧任务迟到结果归旧 revision，不能更改新版本状态。外部 Git 提交、真实设备或数据库迁移的撤销须有对应流程，不能由指针切换宣称已经撤销。

## 11 采集仪的时钟与采样语义

### 11 1 固定逻辑参数

| 参数 | 取值 | 约束 |
|---|---|---|
| CHANNELS | 8 或 16 | 编译期参数，运行时不能从 8 改成 16 |
| CLK_HZ | 100000000 | 仅虚拟板默认值 |
| DIVIDER | 10 或 100 | 对应 10 MHz 或 1 MHz |
| DEPTH | 默认 4096 | 运行时允许 16 到 4096 的 2 次幂 |
| SAMPLE_BYTES | 1 或 2 | 每个时刻一个字，位 n 对应通道 n |
| reset | 同步高有效 | 优先级高于中止和采样 |
| UART | 115200 8N1 | LSB 先发，无流控，有限采集后读取 |

capture_depth 在 SystemContract 中表示本例默认及最大深度 4096，SET_CONFIG.depth 可选择上述较小深度。采样时钟使用系统时钟使能，不另造派生时钟。异步输入通过双触发器同步；仿真与 UI 不将同步后的采样时刻宣传为引脚上真实边沿的精确时间。

### 11 2 触发和计数

用 k 表示系统时钟上升沿。v[k] 定义为该上升沿前送到采样核心的同步向量；双触发器同步器的非阻塞更新在沿后生效，因此测试参考模型必须考虑两个寄存器的延迟，不能直接比较原始引脚输入。TB 在下降沿改变输入，避免测试竞态。

ARM 在 k0 被接受时清零样本计数和分频计数，capture_id 加一且跳过 0，并以 v[k0] 初始化触发前值。采样 tick 严格发生在 k0+D、k0+2D 等上升沿。软件触发在第一个 tick 存储样本 0；上升沿模式仅在 tick 比较所选通道的当前值与上一个 tick 值，首次由 ARM 的初始化值比较。非 tick 的短脉冲不保证捕获。

触发 tick 本身是样本 0，之后每个 tick 存储一个样本。第 DEPTH 个样本提交后进入 DONE，sample_count=DEPTH，不写入第 DEPTH+1 个。通道掩码非零，禁用通道在保存数据中清零，但样本宽度和通道编号不压缩。上升沿触发通道必须处于启用掩码。

状态码为 IDLE=0、ARMED=1、CAPTURING=2、DONE=3、ABORTED=4、ERROR=5。有效 ARM 允许从 IDLE、DONE、ABORTED 或 ERROR 开始，ARMED 和 CAPTURING 返回 BUSY。ABORT 仅在 ARMED 或 CAPTURING 时转 ABORTED，且优先于同拍采样；其他状态为无操作，保留 DONE 数据和原状态。未完成数据不允许以完整采集结果读取。复位清空配置、计数、capture_id 和重复命令缓存。

| 当前状态 | SET_CONFIG | ARM | ABORT | READ_CAPTURE |
|---|---|---|---|---|
| IDLE | 成功后保持 IDLE | 进入 ARMED | 无操作 | 无有效采集时 STALE_CAPTURE |
| ARMED 或 CAPTURING | BUSY | BUSY | 进入 ABORTED | 当前 ID 返回 NOT_READY |
| DONE | 成功后进入 IDLE 并使当前采集失效 | 新 ID 进入 ARMED | 无操作且数据保留 | 匹配 ID 与合法范围可读 |
| ABORTED | 成功后进入 IDLE | 新 ID 进入 ARMED | 无操作 | 当前 ID 返回 CAPTURE_ABORTED |
| ERROR | 成功后进入 IDLE | 配置有效时可进入 ARMED | 无操作 | 无可用结果返回 NOT_READY |

有效 SET_CONFIG 清零 sample_count、清错误并使旧采集不可读，保留 capture_id 的递增计数器；后端已保存的 CaptureRecord 不受影响。ARM 接受时建立新有效采集，直到配置、下一次 ARM 或复位使其失效。任何状态下旧 ID 优先返回 STALE_CAPTURE。GET_CAPS 和 STATUS 不改变状态。

配置只在非采集状态原子提交。SET_CONFIG 全部字段校验成功后一次更新，任何字段非法保持原配置。UART 响应只能在配置或 ARM 真正被核心接受后发出，不能先 ACK 再异步丢弃动作。

### 11 3 RTL 模块边界

| 模块 | 输入输出责任 | 必须检查 |
|---|---|---|
| input_sync | 原始通道到同步向量 | 复位值和两级同步延迟 |
| sample_tick | ARM 与分频到单周期 tick | 首 tick 时刻，无累积漂移 |
| trigger_capture | tick、向量、配置到状态与样本写入 | 触发首样本、深度终点、ABORT 优先 |
| capture_ram | 样本写入和完整采集读取 | 地址边界，未完成禁止读，8/16 位 |
| uart_rx uart_tx | 串行与字节 ready/valid | 波特误差、起止位、背压与复位 |
| frame_codec | 字节流到消息及 CRC | 有界长度、丢帧恢复和重复命令 |
| control_regs | 消息到内部寄存器和动作 | 原子配置、非法值和版本错误 |
| capture_top | 组合上述模块 | 端口与契约一致，无未经约束时钟 |

内部控制总线为 32 位、字节寻址、4 字节对齐，req_valid/req_ready 完成请求，rsp_valid/rsp_ready 返回结果。每次至多一个未完成请求，地址/写值在未握手时保持稳定。只写寄存器读返回 0，只读寄存器写和未映射地址报错；保留位必须为 0。UART SET_CONFIG 先在影子寄存器中校验后提交，不能用多次普通写产生半更新配置。

## 12 capture 设备线协议

### 12 1 帧与 CRC

| 偏移 | 长度字节 | 字段 | 编码 |
|---|---|---|---|
| 0 | 2 | sync | A5 5A |
| 2 | 1 | major | 1 |
| 3 | 1 | minor | 0 |
| 4 | 1 | type | 请求 01 到 06，成功响应加 80，错误 FF |
| 5 | 1 | flags | 本版固定 0，非零拒绝 |
| 6 | 2 | sequence | uint16 小端 |
| 8 | 2 | payload_length | uint16 小端，0 到 1024 |
| 10 | N | payload | 消息规定 |
| 10+N | 2 | crc16 | 小端，不包含 sync 或 CRC 自身 |

完整帧长为 N+12。CRC 为 CRC16 CCITT FALSE：poly=0x1021、init=0xFFFF、refin=false、refout=false、xorout=0；覆盖 major 到负载最后一字节。ASCII 123456789 的校验值为 0x29B1。不存在字节转义，负载允许出现 A5 5A；解析器一旦获得合法长度，先收完整帧再判 CRC。

长度超过 1024 时立即丢弃该帧候选，从候选起点后一字节继续寻找同步；CRC 错误、半帧超时和串口帧错不发送 ERROR，因为序号与消息不可信。建议字节间超时 100 毫秒，过时清除半帧。CRC 通过后才执行语义检查。主次版本不是 1.0 返回 UNSUPPORTED_VERSION，本版不猜测未来次版本的语义。

### 12 2 消息负载

| 类型 | 请求负载 | 成功响应负载 |
|---|---|---|
| 01 GET_CAPS | 空 | channels:u8 sample_bytes:u8 clock_hz:u32 max_depth:u16 max_payload_bytes:u16 feature_bits:u32，共 14 字节 |
| 02 SET_CONFIG | trigger_mode:u8 trigger_channel:u8 divider:u16 channel_mask:u16 depth:u32，共 10 字节 | 空 |
| 03 ARM | 空 | capture_id:u32 |
| 04 READ_CAPTURE | capture_id:u32 offset_samples:u32 count_samples:u16，共 10 字节 | capture_id:u32 offset_samples:u32 count_samples:u16 sample_bytes:u8 reserved:u8，随后样本 |
| 05 STATUS | 空 | state:u8 channels:u8 trigger_mode:u8 overflow:u8 capture_id:u32 sample_count:u32 error_code:u16 reserved:u16，共 16 字节 |
| 06 ABORT | 空 | 当前 capture_id:u32 |
| FF ERROR | 设备只发响应 | original_type:u8 error_code:u16，共 3 字节 |

所有多字节整数小端，无符号。feature_bits bit0 为软件触发、bit1 为上升沿触发，其余为 0。GET_CAPS 和 STATUS 可在任意状态读取。SET_CONFIG 的非法掩码、触发模式、分频、深度和通道返回 INVALID_CONFIG，保持配置。保留字段必须为 0。

READ_CAPTURE 只允许读取 DONE 的当前有效 capture_id。count_samples 必须大于 0，offset+count 不得越过 sample_count；不自动截短。响应前缀为 12 字节，单帧样本容量为 floor((1024-12)/sample_bytes)：8 路最多 1012 个，16 路最多 506 个。旧或无效 capture_id 返回 STALE_CAPTURE，未完成返回 NOT_READY，中止结果返回 CAPTURE_ABORTED。

样本按采样时刻递增，16 路每样本低字节先行。通道 0 是字节 0 bit0，通道 15 是字节 1 bit7。设备完成后冻结数据，直到有效 SET_CONFIG、下一次 ARM 或复位；开始新采集立即使旧采集 ID 失效。连接重建先 GET_CAPS 和 STATUS，不假定 RAM 仍是先前采集。

### 12 3 重发 错误与超时

主机仅允许一个未完成请求。未收到响应时在相同 sequence 和完全相同请求字节下重发，默认超时 1 秒、最多 2 次；传输超时与采集等待不同，上升沿长期不来由主机显式 ABORT。设备缓存最近一个已完成请求及其完整响应，重复请求返回相同响应，ARM 不得再次启动。

处理顺序固定为帧边界和 CRC、最近请求精确重放或序号冲突、版本、flags、消息类型与长度、参数范围、状态准入、原子动作与响应缓存。重放先于 BUSY 检查，避免重复 ARM 被误拒。语义错误更新 ERROR_CODE 但不改变采集状态；ERROR 状态保留给内部不可恢复故障。

同 sequence 与上一请求不同的 type 或 payload 返回 SEQUENCE_CONFLICT，且不覆盖原请求缓存。sequence 在每个新请求后递增，65535 后回到 0；只比较最近一条，不假定序号全局永不重复。新请求可覆盖缓存，主机不能在发送其他请求后再重试旧 ARM。除序号冲突外，可信新请求的 ERROR 响应也进入缓存。复位清除缓存，主机重新握手且不自动重发复位前 ARM。此协议只在同一未复位会话内保证重复命令去重；跨设备掉电不保证恰好执行一次，重连后先对账状态，不盲重发 ARM。

| 错误码 | 含义 | 行为 |
|---|---|---|
| 1 BAD_LENGTH | 可信 CRC 帧的负载长度与消息不符 | 不执行动作 |
| 2 UNSUPPORTED_VERSION | 不是 1.0 | 重新协商或停止 |
| 3 UNKNOWN_TYPE | 未定义消息或 flags 非零 | 保留原始错误信息 |
| 4 INVALID_CONFIG | 配置不满足约束 | 原配置保持 |
| 5 STALE_CAPTURE | 采集 ID 已失效 | 查询当前状态 |
| 6 BUSY | 当前采集中不可配置或重启 | 等待或中止 |
| 7 NOT_READY | 当前结果尚未完成 | 查询 STATUS |
| 8 RANGE | 读取范围或容量非法 | 主机重新分块 |
| 9 SEQUENCE_CONFLICT | 重复序号但内容不同 | 使用新序号并对账 |
| 10 CAPTURE_ABORTED | 读取已中止采集 | 重新配置和采集 |

protocol/golden-vectors.json 提供完整十六进制帧。实现必须额外覆盖 CRC 翻位、短帧、超长帧、同步字出现在负载、重复 ARM、序号冲突和复位后的恢复。向量成功是编码一致性证据，不能替代独立 RTL 功能测试。

## 13 上位机与数据导出

上位机依次创建会话、读取能力、检查契约、配置、ARM、轮询状态、分块读取、校验完整性并展示。模式始终显示 rtl_simulation、replay、mock 或 hardware；回放显示原运行时间和来源。hardware 模式首版缺少板卡资料时不可启用，不用 mock 数据填补。

device-commands 返回 202，ActionResult.resource_id 是 command_id。客户端通过会话 commands/{command_id} 读取 DeviceCommandResult，或在 SSE 通知后拉取；其中 reply 按动作区分 DeviceCapabilities、DeviceStatus、CaptureChunk 或动作确认，同时保留原始响应帧与稳定错误。completed 必须已经验证序号、CRC、回复类型和请求动作，不能只凭串口写入成功。

采集组装完成后创建不可变 CaptureRecord，通过会话 captures 列表及详情获取 metadata 和 samples 工件引用，再走统一授权下载。CaptureRecord.id 是平台 UUID，capture_id 是设备会话内 uint32，两者不能混用。分块样本数、样本宽度和十六进制长度交叉检查，16 路 count 不得大于 506；记录模式、契约、时钟、分频及原始运行时间，支持导出和回放。

波形时间以样本 0 为 t=0，t[n]=n×divider/clock_hz。没有硬件时间戳时不显示绝对测量时间。前端一次采集最大 4096×16 个逻辑值，视口按像素聚合保留变化；不通过逐点 DOM 元素绘图。无效区间、缺帧与中止必须可见，不能以 0 补齐后当作有效数据。

CSV 使用 UTF8，第一列 time_s，随后按通道从 ch0 到 ch7 或 ch15，时间由整数样本号计算。二进制导出包含 metadata.json、samples.bin 和 SHA256SUMS；metadata 记录协议、契约哈希、revision、模式、channels、divider、clock_hz、sample_count、capture_id、sample_bytes、数据哈希和原始运行时间。

离线 UART 首版只支持 8N1、可配置波特率、默认空闲高、LSB 先行。采用起始下降沿后各位中心采样，验证停止位；采样率至少为波特率 8 倍，否则返回采样不足。解码输出字节、起止样本索引、帧错与不确定标记，使用独立已知波形校验。未提供温度、电压或电流传感时相关指标显示不可用。

## 14 独立验证与有限修复

### 14 1 验证服务边界

开发测试可反馈给编码 Agent；保留验收由独立服务维护，生成容器不挂载源代码、答案、种子表或签发密钥。独立 Python 参考模型按采样定义编写，不能仅与同一生成器产出的编解码库互测。保留测试泄漏或反复被针对性调整后另建最终保留集。

VerificationEvidence 记录 test_id、revision、输入哈希、测试集哈希、环境哈希、工具版本、种子、状态、模式、退出码、报告及签发者。签名固定使用 Ed25519；移除整个 auth_tag 后按 RFC8785 JCS 生成 UTF8 字节，直接对这些字节签名。auth_tag 包含 algorithm=Ed25519、key_id 和无填充 base64url 的 64 字节签名。seed 限 uint32，避免跨实现数值精度差异。

私钥只在验证服务，网关按 issuer_id 与 key_id 查已批准只读公钥。轮换新增 key_id，旧证据保留对应公钥；撤销后不能用于新证据。入库同时检查 task、revision、suite、input 和 report 哈希，证据 ID 去重，同 ID 不同内容拒绝。签发不证明测试充分，但防止生成侧伪造通过。

已签 VerificationEvidence 全字段永不更新，其 status 只记录原运行的 NOT_RUN、PASS、FAIL 或 INDETERMINATE。EvidenceValidity 单独保存 evidence_id、当前 revision、effective_status、stale_reason、signature_valid 和 evaluated_at；变更只更新有效性投影，不能把已签 PASS 原地改成 STALE。证据 validity 接口返回当前版本投影。发布门禁同时要求原始签名有效、依赖匹配、适用当前版本且 effective_status=PASS，复用也保存独立的当前版本映射。

### 14 2 发布门禁

Schema、端口、语法、独立功能、CAD 网络和 ERC 分层显示；综合、布局布线和实板未测各自显示 NOT_RUN。Verilator 对 X/Z 和厂商原语的限制必须写入报告；不支持的检查是 INDETERMINATE，不是 PASS。无目标器件时不能伪造 LUT、时序裕量或综合已通过。

故障诊断先区分代码与环境错误。默认最多三轮修复，修改范围局限当前候选，不能删断言、替换保留测试或关闭错误。计数偏一、打包字节序与 CRC 覆盖三个注入场景分别冻结注入补丁及预期失败；修复后运行原失败测试和关联回归。

## 15 KiCad 参考原理图

图纸链路必须使用经审核模板与结构化 ConnectionPlan。模板版本、符号库、位号规则和生成器哈希固定，8/16 路各生成一套源工程。具体电阻值、电平转换和连接器不能由语言模型凭印象补齐；资料不足时使用已审查的参考假设并标明未绑定。

ConnectionPlan 保存每条逻辑网络、端点 refdes/pin、位号及对应通道。生成后用固定版本 KiCad 打开或 CLI 读取，导出实际网络表，与批准计划按端点集合和连通分量比对；不能只比较网络名字。检测断线、误短接、缺引脚、重复位号和孤立关键网络。

ERC 原始报告完整保留。豁免绑定具体规则、端点、理由、模板和批准人，模板或网络改变后重新审核；不允许 blanket 忽略全部警告。BOM 由最终原理图导出，与数量、MPN、封装及 DNP 对照。标题栏、预览和下载说明都必须显示参考原理图、未绑定板卡、不可直接接线。

产物包含 kicad_pro、kicad_sch、必要库、BOM.csv、netlist、ERC 报告、network_check.json 和 PDF/SVG。第三方 MCP 兼容测试失败时改用受控模板与官方 CLI 适配，保持同一语义工具；不能删去 KiCad 功能。未取得模板审核条件时该项状态保持 BLOCKED，不能用示意图片充数。

## 16 报价与器件来源

### 16 1 数据与计算

每条 QuoteOffer 记录制造商、完整 MPN、封装、等级或商品 SKU、状态、数量阶梯、MOQ、包装倍数、库存、交期、币种、税费、来源、观察与到期时间。fixture 永远显示测试报价；authorized_import 显示导入报价；live 必须来自有授权的真实请求。

采购需求 need=N×qty_per_unit+spares，采购量 buy=multiple×ceil(max(need,MOQ)/multiple)。按 buy 选择最高适用 min_qty 阶梯；若未达到任何档位，该报价不可用。例：10 台×3 个+2 个备料=32，MOQ10、倍数10 得 buy40；单价1.5时器件60元，同供应商一次运费10元，订单70元。该例只测算法。

全流程使用 Decimal。运费按供应商订单合并，税和汇率规则明确登记，未知不填 0。库存未知不推定有货。毛利率 g 使用 price=cost/(1-g)，g 必须在 [0,1)；成本加成另列。完整重购成本与已有器件的增量现金成本分开，缺价时只给已知小计。

### 16 2 国内外比较

首版支持至少两个国内来源和一个海外对照来源的配置、导入及比较；真实在线连接按授权单列。只有完整商品键、采购量、时间、税、汇率和运费可比时才排序总价。PCB 和 SMT 缺少尺寸、层叠等条件时显示待设计或情景估算。

刷新生成新快照，原快照不可变。默认缓存不超过 24 小时，且不能超过供应商有效期及许可期限。来源不允许保存或展示时按政策隐藏并改用有权使用的证据，不以“需要审计”为由复制受限原始数据。报价模块不下单、不支付、不创建合同。

## 17 知识库与外部检索

内置文档经历 UPLOADED、PARSED、REVIEWED、INDEXING、PUBLISHED，失败可重试，撤销后立即从可见查询范围移除。切分建议 400 到 800 token，表格保留表头、单位和原页定位。向量索引绑定 embedding 模型版本及维度，换模型建立新 generation，不能把新旧向量混用。

检索先按成员、项目、文档 ACL、版本和型号过滤，再执行型号精确匹配、关键词及向量召回、去重和重排。缓存键包含 ACL 版本和索引 generation；删除和撤权使所有缓存与外部映射失效。删除后立刻逻辑不可见，后台清除文件和向量；审计保留策略与备份中数据到期分别说明。

KnowledgeProvider.search 输入为项目授权上下文、query、collection_ids、document_version_ids 和 top_k，输出 Evidence 数组、partial 与 warnings。外部源必须返回 source_id、source_uri、locator、content_sha256、retrieved_at、license、acl_scope 和 text。外部 score 可作排序辅助，不能作为统一事实概率。

外部地址、鉴权引用、字段映射、超时和数据政策独立管理。映射失败或缺少定位时不进入可信引用路径。RAGFlow 是可选适配器，不是本机首版必装依赖；至少一个受控外部检索实例必须完成真实接入测试，才能声称外接能力验收完成。

检索黄金集不少于 30 条，涵盖近似型号、版本冲突、数字表格、中英文、无答案、删除与权限。测试先冻结样本和基线，再设检索质量阈值；跨项目泄露与撤权后可读必须为零。缺乏证据时返回未找到，不用流畅回答掩盖缺口。

## 18 完整管理中心

### 18 1 通用管理交互

各管理页使用列表、详情、版本、测试、权限、绑定、审计六个区域。新增或编辑只创建草案；测试成功后可申请批准启用；升级先展示权限和配置差异。健康失败、被撤信任、依赖缺失和版本不兼容必须阻止新调用。禁用与卸载是不同操作，历史运行索引默认保留。

| 管理对象 | 必须完整支持的操作 |
|---|---|
| 模型 | Provider 与模型登记、能力测试、参数校验、角色绑定、预算、备用路由、凭证轮换、版本、导入导出 |
| MCP | stdio 与兼容 HTTP、认证、工具发现、Schema 变化检测、能力授权、测试、日志、本机进程启停与回退 |
| 知识源 | 多库、文件与版本、ACL、解析重试、重建索引、检索调试、删除、外部字段映射、绑定与密钥 |
| Skills | 本地包和固定 Git 版本、编辑、校验、依赖、测试、发布、绑定、升级回滚、禁用与卸载 |
| 插件和目录 | 自定义目录、搜索导入、完整性与许可、信任、依赖锁、配置向导、启停、回滚、数据保留与撤销 |
| 治理 | 用户与项目角色、费用、审计、告警、密钥脱敏、备份和恢复 |

导出包只保留 secret_ref 占位与凭证用途，不导出密钥值；导入必须重绑定本机凭证。远程 MCP 只能回滚本平台连接配置和受支持版本选择，不能宣称回滚了第三方服务器。受管本机 MCP 才能启动、停止和切换已锁定包。

### 18 2 生命周期

统一包状态为 DISCOVERED → QUARANTINED → VALIDATED → APPROVED → ENABLED；校验或测试失败进入 FAILED；禁用进入 DISABLED，卸载进入 REMOVED。批准和启用不跳过依赖与兼容检测。包版本被历史运行引用后内容只读。

安装不得自动运行 setup、postinstall 或未知脚本。依赖只能从已批准来源解析成精确版本及哈希，依赖循环、同版本不同内容、哈希不符直接拒绝。未签名包显示未签名并要求管理员显式信任；签名只证明来源，不替代审核。UI 插件放入无同源权限的 sandbox iframe，通过类型化消息调用有限宿主能力，不读取 Cookie、主 DOM 或本地存储。

## 19 扩展 SDK 与工具网关

PluginManifest 是本项目格式，不宣称属于 MCP 通用标准。清单列出 engine_api、入口、transport、配置 Schema、权限、精确依赖、健康方法、数据政策和迁移步骤。SHA256 覆盖独立 payload 包，manifest 本身不放入被其自身哈希覆盖的内容；禁止循环自哈希。

核心通过独立进程或受控 HTTP 调用适配器，不将未知 Python import 或 npm 包载入 API 主进程。统一调用信封包含 invocation_id、project_id、revision_id、capability、输入引用、deadline、批准引用和幂等键；返回带状态、类型化 payload、诊断与脱敏 request_id。身份和权限来自网关，不采信插件自行声明的 admin。

适配类型包括 ModelProvider、KnowledgeProvider、QuoteProvider、ToolProvider 和 Skill 包。工具名限定为 project.read_artifact、rtl.lint、rtl.simulate、rtl.synthesize、cad.instantiate_template、cad.export_netlist、cad.validate、quote.lookup、report.read、job.cancel 等登记能力。输入接受工件 ID、受控模板 ID 和有类型配置，不接受任意 Shell 或主机绝对路径。

SDK 合规套件必须覆盖正常结果、非法输出、超时、限流、权限拒绝、撤销、版本不兼容、依赖循环、内容损坏、卸载后调用和恢复。另一成员应能仅依据 sdk/README.md 和示例声明编写适配器，不修改核心业务模块即可登记。随附示例是契约夹具，本轮不安装或运行第三方插件。

## 20 GitHub 协作与工程交付

GitHub 插件首先只读授权仓库，列分支、base SHA、文件和检查状态。写入计划包含仓库、分支、base SHA、文件增删改、完整 diff 哈希、提交说明和批准引用。执行前重新取 base SHA；变化后返回 BASE_CHANGED，重新准备差异，不 force push。

发布 invocation_id 与目标分支绑定，断线后先查分支、提交和既有 PR，避免重复。默认不授予仓库管理、Secrets、组织或工作流修改，不默认合并主分支。大波形留在工件库，Git 保存源码、契约、必要图纸、测试定义和工件索引。导出工程包默认去除主机用户名路径、账号、令牌和未经允许资料。

交付包必须包含冻结需求、契约、源文件、必要图纸、BOM、报价快照、生成器及工具锁、公开测试、独立验收报告索引、原始日志引用、复现说明、已知限制和第三方声明。package_manifest 逐文件列 SHA256 与来源，并标 hardware_test_status=NOT_RUN（未实测时）。报告不能把未测项目折叠成总体通过。

## 21 前端页面与异常状态

### 21 1 页面和主要操作

| 页面 | 主要展示 | 必须处理的异常 |
|---|---|---|
| 项目首页 | 当前版本、阶段、关键未知项、最近运行 | 无项目、无权限、旧数据与服务离线 |
| 工程工作台 | 工件树、需求交互、计划、差异、图纸和波形 | 上传失败、等待回答、工具阻断、取消未确认 |
| 需求与验收 | 参数、单位、证据、问题与验收映射 | 数值冲突、来源缺失、冻结失败 |
| 变更审查 | 语义 diff、影响图、费用、重建与复用 | 基线变化、预算不足、旧批准失效 |
| 运行和证据 | 任务、日志、版本、工具与独立结论 | LOST、未知费用、STALE、未测 |
| 模型与扩展各页 | 配置、版本、测试、权限、绑定和审计 | 缺密钥、Schema 变化、损坏包与撤信任 |
| 产品上位机 | 能力、配置、模式、状态、波形与解码 | CRC、超时、旧采集、通道不符和缺帧 |

所有页面具备 loading、empty、error、forbidden 和 stale 状态。运行进度显示真实阶段和耗时，不虚构百分比。小屏按标签切换代码、图纸、BOM 和波形；批准按钮旁明确目标版本、动作及有效期。日志和文档预览按纯文本或安全渲染处理，不能执行其中的 HTML 或脚本。

### 21 2 性能与可访问性目标

在登记的本机基线上，非重计算 API 的 P95 目标为 500 毫秒以内，事件可见延迟目标 2 秒以内；不包含模型、上传和 EDA 时长。4096×16 波形的首次视图目标 1 秒以内，平移缩放应保持可交互。目标需通过本机实测后冻结，不在规格交付时写成已达到成绩。

表单字段有标签、键盘焦点和错误关联；状态同时使用文字与颜色。密钥只显示已配置及轮换时间，不支持“查看明文”。超时支持重新获取资源状态，不能用前端自动重试触发重复付费生成。

## 22 本机安全 运维与恢复

对外 HTTP 地址校验协议、允许列表、DNS 解析与每次重定向，拒绝元数据地址和未批准内网；管理员需要的本地 MCP 经独立受控连接登记。EDA 无模型密钥、无默认网络、独立工作目录、进程限制和磁盘配额。HDL 也可能执行系统任务，静态限制和操作系统隔离必须同时存在。

普通日志保留 invocation_id、request_id、阶段、耗时和费用状态，不记录 Key、密码、Cookie 或完整私有 Prompt。审计保存谁在何时对哪个版本执行何种动作及结果。常规业务 Event 带 project_id；系统级审计使用独立 AuditEvent，audit_scope=system 时 project_id=null，不能为无项目事件伪造项目归属。项目审计则必须带有效项目 ID，两种条件由领域校验器强制。

启动器执行配置检查、数据库连接与迁移、工件目录检查、调度器启动、Worker 注册和健康检查；遇到依赖失败明确降级，不自动安装 WSL、商业工具或额外服务。停止先禁止新任务，再请求取消或排空，最后停服务，不直接杀掉用户已有应用。

备份包含数据库一致性快照、工件索引、必需工件和配置版本；密钥独立加密备份，恢复时重新授权。恢复在维护模式和空目标目录执行，验证哈希、迁移版本、索引及权限后才开放使用。建议本机演示 RPO 为 24 小时、RTO 为 60 分钟，须以演练实测确认。

删除立即撤销在线访问，物理清除与备份到期按项目策略执行。恢复备份必须重放删除及撤权标记，不能让已删除私有文档再次可检索。数据默认保留 30 天是待管理员登记的初值，不自动永久保存用户所有上传。

## 23 验收目录与证据要求

acceptance/catalog.json 是逐项验收清单，包含 ID、优先级、负责者、前提、操作、预期结果、证据和正文映射。验收执行记录另存 run_id、构建 SHA、环境、实际结果及缺陷；清单中的 expected 不得作为已经通过的记录。

重点端到端场景为：8 路需求到工程包；预置错误失败并有限修复；8 到 16 路影响分析和重建；失败回退；自定义扩展完整生命周期；Worker 重启与旧租约迟到；跨项目越权；报价缺价和知识无答案。证据必须能回溯到同一版本及工具环境。

API 联调使用 OpenAPI 和正反例，协议联调使用 golden vectors 与独立 CRC。CAD 至少两种通道数，检索至少 30 条人工样本，Provider 覆盖正常、空输出、截断、拒绝、401、429、5xx、中断及 usage 缺失。恢复演练验证数据与权限，不只确认备份文件存在。

对照实验沿用三组：单模型、单 Agent 加相同工具、完整多 Agent。固定模型、任务、成本上限、修复轮数和工具版本；记录失败与未知费用。开发集 12 个小任务与 6 个变更任务，最终保留集另设。尚未取得真实成绩前不填写效率提升或通过率数字。

## 24 工作包 顺序与交接

### 24 1 实施顺序

先冻结契约、工具烟测和测试边界；再建设身份、工件、队列和预算；随后完成 8 路纵向闭环，接入图纸、上位机、检索与报价；再实现变更和全部管理生命周期；最后开展真实连接、独立回归、恢复演练与提交。公共管理框架可以复用，各类对象仍须独立验收。

planning/backlog.json 给出可领取工作包，每项含 owner、依赖、输入输出、完成条件和估算。开发者 A 与 B 在契约、设备协议和网关返回值三个边界交叉审查；任何一方更改 Schema 都必须同时更新调用方、夹具、正文和兼容说明。

### 24 2 关键检查点

| 北京时间 | 检查点 | 未通过时处理 |
|---|---|---|
| 10 月 29 至 30 日 | 环境、模型、EDA、KiCad 烟测和契约冻结 | 登记阻断条件，使用契约测试推进独立工作 |
| 10 月 31 至 11 月 2 日 | 8 路纵向闭环，管理公共底座 | 报告实际剩余工作，不把界面完成当工程闭环 |
| 11 月 3 至 5 日 | 16 路变更、图纸与上位机、生命周期 | 逐项保留 P0 缺项与重新预测 |
| 11 月 6 至 8 日 | 扩展接入、报价对照、独立测试和恢复 | 阻断问题优先，记录第三方真实连接状态 |
| 11 月 9 至 10 日 | 冻结构建、演示与比赛材料 | 只声明实际验收状态，完整首版范围不变 |

总体设计估算为 29 至 49 个编码人日，当前两名编码人员到提交节点的理论容量较紧。backlog 的小项估算只用于分配和依赖，不把并行视为减少总人日。外部账号等待、板卡审核和未知插件兼容不包含在乐观工期里。

## 25 待登记条件与规格维护

| 条件 | 未满足时阻断什么 | 可继续的工作 |
|---|---|---|
| 本机隔离环境和固定工具版本 | 生成代码执行及真实 EDA 证据 | Schema、页面、mock、静态工具适配 |
| 国产模型凭证 区域和预算 | 真实付费调用 | Provider 录制夹具、错误映射和预算逻辑 |
| GitHub 仓库和授权方式 | 真实读取私库与对外发布 | diff、批准和冲突状态测试 |
| 国内外报价来源许可 | 相应在线查询及展示 | 合法导入、测试报价和确定性计算 |
| 参考模板和审核责任人 | CAD 工程验收 | ConnectionPlan、网络检查和受控模板接口 |
| 真实板卡电气资料 | 绑定、烧录和实板指标 | 虚拟板与未绑定参考原理图 |
| 参赛远程访问要求 | 对公网的部署决定 | 本机演示、录屏和工程包 |

上述项目是实施条件，不影响本轮开发规格书和契约包的交付。实施时在 decision-log 中填写值、负责人、证据和生效时间；不能把空白项悄悄换成猜测。更改本规格须记录原因、受影响需求和测试、兼容性及批准记录。

本文依据本地总体设计 1.0、源码审查、API 迁移建议和原有契约草稿细化。第三方能力与模型标识沿用总体设计的候选资料，实施烟测重新核对；本轮没有重新做网站、账号、工具或硬件能力认证。
