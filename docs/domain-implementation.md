# 领域服务与管理接线交接记录

日期：2026-10-29。范围为实施计划 Task 3，以及随后分配的 management 服务、domain 路由和 API 集成测试。没有执行 Git 提交、推送、第三方插件安装、真实付费模型请求或真实设备操作。

## 可调用接口

领域包不持有全局数据库连接或全局业务单例。输入为字典、Decimal 或 dataclass，持久化和并发事务由 apps/api 调用方承担。

| 模块 | 接口与行为 |
|---|---|
| orchestration.dag | `DependencyGraph(dependencies)`；`topological_order()` 稳定字典序 Kahn 排序；`invalidate(changed)` 返回所有下游及来源原因；`rebuild_plan(changed)` 返回拓扑重建顺序；未知依赖和环抛 `GraphError`。`can_reuse(previous,current)` 要求工件、测试、工具、生成器、配置、输入六类哈希齐全相同且授权有效。 |
| orchestration.state | `transition_revision(current,target,approved=False,blocking_evidence=(),release_approved=False)`；`transition_task(current,target,process_stopped=False,reconciled=False,safe_to_retry=False,blocking_questions_resolved=False)`。验证必须有有效独立 PASS；取消必须确认进程停止；LOST 重排必须先对账。调用方必须提供当前版本全部阻断证据。 |
| orchestration.snapshots | `freeze_snapshot(payload)->ContextSnapshot`；快照保存分离的确定性 JSON 与 SHA256，`to_dict()` 返回新对象。运行快照不是独立验收签名格式。 |
| model_gateway.budget | `BudgetLedger(limits=None,reservations=None)`；`reserve(invocation_id,scope_keys,estimate,price_version_id)`；`settle(invocation_id,actual_cost)`；`reservation_valid(id)`；`to_dict()`。四层额度 global/project/user/run 同时检查，默认 0，拒绝 float；相同 invocation 的参数冲突拒绝。未知消费保留全部预留；真实对账才释放差额。调用方须在数据库锁事务内加载和保存。 |
| model_gateway.provider | `OpenAICompatibleProvider(profile,api_key=None,allowed_hosts=(),external_send_allowed=False,transport=None,reservation_validator=None,resolver=None,output_validator=None,max_retries=2,sleeper=...)`；`generate(request)->ModelResponse dict`。显式检查 CN 来源、模型版本、能力、预留、外发和地址；默认不能调用。httpx Transport 可注入。保留 requested/actual model、服务请求 ID、真实 usage；缺失 usage 为 null/unknown，缓存与推理子项不重复累加。401/403/429/5xx、空结果、拒绝、截断、非法结构、超时、绝对 deadline 有稳定状态。`embed/rerank` 当前明确 blocked。 |
| knowledge.parsing | `parse_document(content:bytes,media_type,source_uri=...)->dict`；UTF-8 文本、PDF 文本层、DOCX 段落/表格、XLSX 表格提取；保留行、页、段落、表格或工作表定位，表格逐行带表头。无 OCR、扫描页、缺依赖或不支持格式返回 BLOCKED；解析异常返回 FAILED。 |
| knowledge.index | `KnowledgeIndex(documents,generation='1',acl_version='1',scorer=None)`；`search(context,query,collection_ids,document_version_ids=(),top_k=5)`；`revoke(document_id)`；`cache_key(...)`。项目/成员/文档/版本/型号过滤在 scorer 之前；撤销立即不可见；Evidence 保留来源、定位、许可、ACL 和内容哈希。`transition_document` 限制上传、解析、审阅、索引、发布及撤销。 |
| knowledge.index | `PgvectorSearch(connection,generation,model_version,dimensions).search(vector,project_id,authorized_document_ids,top_k=5)`；参数化 SQL 在召回前限定项目、授权文档、generation、模型、维度和 revoked；不建立连接、不自动迁移。 |
| quotes.pricing | `price_quote(offer,units,qty_per_unit,spares=0,fx_rates=None,now=None,live_authorized=False,owned_qty=0)`；`price_order(lines,supplier_shipping=None)`；`selling_price(cost,gross_margin)`。全程 Decimal；MOQ/包装倍数/阶梯价、税、汇率、供应商一次运费；未知项不补零，不可比项不排序。完整重购与库存抵扣后的增量现金分别计算。fixture 永远标“测试报价”。 |
| tool_gateway.extensions | `validate_package(manifest,payload,dependencies,trusted=False,approved_sources=(),signature_verified=False)`；精确版本/哈希锁、来源、显式信任、依赖当前批准和环检测；只得到 VALIDATED，不安装或执行。dependencies 应提供本包解析出的依赖清单。`permission_diff(old,new)`；`transition_extension(...)`。 |
| tool_gateway.policy | `ToolPolicy.authorize(request,context)`；验证项目、revision、登记能力、当前启用/健康/信任、批准和 deadline，拒绝任意 shell 与主机路径。`validate_endpoint(endpoint,allowed_hosts,resolver=None,approved_local=False)`；协议、主机、DNS、元数据地址检查。 |
| tool_gateway.mcp | `ControlledMCPClient(config,capability_tools,allowed_hosts=(),approved_local=False,resolver=None,authorization=None,transport=None)`；`connect(context)`、`call(request,context)`、`disconnect()`。MCP 2025-03-26 HTTP JSON 初始化、通知、发现、Schema 哈希锁和受控调用；禁重定向、远程 Schema 引用、未登记工具和非法输入；工具 isError 不是成功。 |
| tool_gateway.sdk | `validate_adapter_response(response,expected_invocation_id,expected_output_schema,output_validator)`；冻结信封、调用 ID、输出 Schema、completed 的非空输出校验。说明在 `packages/tool_gateway/README.md`。 |
| workers.health | `detect_tools()` 只检测本地工具与版本，不付费调用，不运行工程；AVAILABLE 不是验收 PASS，硬件始终 NOT_RUN。 |
| workers.runner | `TemplateRunner(backend=None).execute(template_id,artifact_dir,config)`；只接受固定模板，缺少可信隔离后端返回 blocked/ISOLATION_UNAVAILABLE；后端必须声明无网络、无密钥、进程/磁盘限制、进程树取消。rc=0 只表示执行，verification_status 仍 NOT_RUN。当前没有具体隔离后端。 |

## API 接线

`apps/api/services/management.py` 提供：

```python
management_command(db, user, request, record, body) -> (dict, int, dict)
```

由现有 `workflow.execute_command` 在 Schema 与 expected_version 校验后调用。资源历史、事实和审计在同一事务。已实现本地文档 parse/retry/publish/revoke；集合本地关键词索引代际；报价 QuoteLookup 工件与完整商品键核对后的 Decimal 比较；变更需求字段 diff、失效图和保守全回归计划；配置禁用、导出、历史回滚至草案；模型导入需重新绑定凭证；扩展显式信任、撤信任、payload 校验和禁用后卸载；预算与用户启停。配置回滚仅作用于平台配置，不宣称回滚远程 MCP 服务。

需要真实外部环境的模型能力测试、MCP 探测/进程启停、外部知识和报价源测试、插件测试等会持久化 BLOCKED 及原因。未通过当前配置的真实健康测试不能 enable/approve。change apply 明确拒绝，避免创建缺少新契约与验收的伪完整版本。

`apps/api/routers/domain.py` 需由入口 `include_router(domain.router)` 注册，包含：

- 原始 OpenAPI 的 `POST /api/v1/projects/{project_id}/knowledge/search`：身份、CSRF、项目及 collection ACL 预过滤；空 ACL 是拒绝访问。当前为关键词检索，明确提示向量未配置。搜索每次重查 ACL，不缓存包含私有片段的幂等响应；相同请求键在文档撤销后也不能读旧结果。
- 原始 OpenAPI 的 `/api/v1/admin/secrets` 创建及单项 GET/PUT/DELETE：数据库仅存 Fernet 密文，响应仅元数据；主密钥要求进程环境 `FPGA_SECRET_KEY`，未登记明确拒绝；轮换/撤销禁用相关 Provider 连接，不返回明文。主密钥备份和轮换流程仍需运维设计。
- 原始 OpenAPI 的 `GET /api/v1/admin/audit`：管理员权限、分页和不含密钥的元数据。旧事件未记录 request_id 时返回全零 UUID，不能据此追溯原请求；新密钥事件记录真实 request_id。
- 原始 OpenAPI 的 backup/restore：未配置真实协调备份、删除/撤权重放和恢复演练，稳定返回 BACKUP_NOT_CONFIGURED / RESTORE_NOT_CONFIGURED。
- 诊断扩展 `GET /api/v1/system/resources/{resource_id}/versions`：按资源的项目 ACL 或全局管理员权限返回已保存版本。此路径不是冻结 OpenAPI 的原始操作，归入计划允许的 system 扩展。

报价比较结果与变更预览当前持久化于 ResourceRecord.internal；面向工作台的完整结果投影、下载和可视化读取仍需后续接线。通用 GET 仍返回冻结资源 payload，不混入临时分析字段。

## 实际红绿与检查

1. 首轮 `pytest tests/domain -q`：4 个 collection error，领域模块不存在。
2. 实现基础行为后：25 passed；补 MCP/SDK、表格和 pgvector 扩展点后：34 passed。
3. 边界红测明确发现五项缺口：Schema 校验异常传播、迟到 Provider 结果仍成功、依赖批准/信任遗漏、文档发布准入缺失、Worker completed 与退出码矛盾。逐项修复并分别运行红绿用例。
4. 增量现金与 MCP 远程 Schema 引用：2 failed 后修复；领域套件最终 42 passed。
5. API 初始集成 4 failed（接线模块不存在），实现后 4 passed；再补用户启停、密钥主密钥缺失/备份阻断、真实 fixture 报价比较。用户启用曾错误要求 Provider 健康测试，红测后修正并核对 User.enabled 事实。
6. 最后增加同一 search 幂等键在文档 revoke 后重放的真实集成边界，最初泄露缓存中的旧片段；改成每次重新授权和检索后通过。
7. 最终命令：`.venv/Scripts/python.exe -m pytest tests/domain tests/api/test_management.py -q`：**49 passed, 1 warning，2.15s**。warning 来自当前 Starlette TestClient 对 httpx 的弃用提示。
8. 最终命令：`.venv/Scripts/python.exe -m ruff check packages/orchestration packages/model_gateway packages/knowledge packages/tool_gateway packages/quotes workers tests/domain apps/api/services/management.py apps/api/routers/domain.py tests/api/test_management.py --output-format concise`：**All checks passed!**

这是领域与 SQLite 测试模式的 API 证据，不是 PostgreSQL 并发、完整 P0 或真实工具端到端验收。

## 未完成与下一步

- 实际国产 Provider 身份、价格、向量/重排能力和外发政策登记未做；真实预算默认零。本轮 transport 夹具不计作真实接入验收。
- PostgreSQL/pgvector SQL 扩展点未连接真实数据库执行；数据库迁移、真实向量 generation、30 条人工检索黄金集、阈值和外接知识实例仍 NOT_RUN。
- PDF 文本层已提供解析路径，但本轮没有对生产 PDF/扫描件做质量验收；OCR 未配置。全文定位和表格复杂版式仍需人工核验。
- MCP 的 SSE、分页工具发现和受管 stdio 进程后端明确阻断；未测试真实服务器。DNS 校验与实际连接之间仍需部署层的固定出口/地址绑定防护，不能据此声明已完成完整 SSRF 防护。
- 扩展生命周期的真实健康测试、签名验证器、精确依赖解析、配置升级的权限差异 UI/批准联动及包实际安装尚缺；当前不可凭静态 VALIDATED 启用执行。
- Worker 尚无真实隔离后端，不执行任何生成代码；模板、资源限制的真实落实和取消进程树验收仍 NOT_RUN。
- 报价仅计算 fixture/授权导入数据；国内两源与海外一源的授权在线接入、完整税汇运政策版本、缓存许可和原始数据展示政策未验收。没有下单或支付能力。
- change apply、自动生成新契约/新验收、8→16 完整下游重建、独立系统冒烟与工作台分析结果投影仍需后续实施。
- 主密钥管理、完整审计 request_id、加密备份与恢复演练未完成。备份与恢复 API 如实拒绝，不创建假成功记录。

按最新交接要求，本记录写入后子代理停止文件写入；主代理负责 router 注册、整套回归、交付状态登记和 GitHub 冻结上传。
