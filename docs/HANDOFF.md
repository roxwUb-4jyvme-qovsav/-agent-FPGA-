# 2026 年 10 月 29 日开发交接

本次按用户要求收口并上传，后续由两人交替开发。当前是可继续开发的基础快照，不是完整首版完成声明。范围基线仍是开发规格书中的全部 84 项 P0，不缩减。

## 从哪里接手

1. 先读根 README，安装依赖并运行测试，确认自己的环境。
2. 阅读本文件、docs/implementation-ledger.md，以及各模块实施报告。
3. 总体设计与开发规格书已放进 docs/reference；现行运行契约在 packages/contracts。
4. 开始前 `git pull --ff-only`，独立分支开发，任务完成后写测试结果与缺项并提交。不要两人同时编辑未交接的同一任务。

## 已经落地

- FastAPI 与 SQLAlchemy 持久化底座，成熟密码哈希、一次初始化、会话/CSRF、项目 ACL 与全局管理员分离。
- 项目/资源 CRUD、ETag/If-Match、幂等冲突、版本历史、人工批准摘要、冻结、样例导入及不可变工件上传与哈希校验。
- Worker 领取、60 秒租约、attempt/fencing、心跳、晚到结果拒绝、取消未确认、任务查询和回答入口。
- 无数据库的 DAG/状态/快照、预算与 Provider、文档解析和 ACL 检索、报价 Decimal 算法、扩展策略与受控 MCP/Worker 接口。
- 管理 API、文档上传解析发布撤权、加密密钥元数据；未具备条件的外部动作明确阻断。
- capture/1.0 协议、8/16 通道 mock 采集模型与恢复快照、CSV/UART 解码、Ed25519 JCS 验签与有效性投影。
- 采集核心、UART RX/TX、协议分帧、命令缓存/寄存器与顶层 RTL。6 项公开 TB 在本机 Icarus 实际通过。
- React TypeScript 中文工作台，真实浏览器走通初始化、创建项目、16 路样例导入、批准冻结、零预算静态运行与 mock 会话创建。

## 当前验证

Python 全套 143 passed，1 项依赖弃用警告。前端已运行类型检查、测试与生产构建，最终结果详见 web-implementation.md。单元和公开测试不等于 84 项产品验收，当前不宣称完整 P0 验收通过。

最终检查、审阅修复和上传记录见 [verification.md](verification.md)。冻结参考资料 179/179 条已按原始字节核对，Git 不转换这些文件的换行符。

## 静态 Worker 接入约定

`contract.validate` 的 `ToolResult.input_sha256` 绑定 `TaskEnvelope.input_artifacts` 完整列表：使用 UTF-8 编码的 JSON（`ensure_ascii=False, sort_keys=True, separators=(',', ':')`），再计算 SHA256；数组沿用冻结 envelope 的顺序。`passed` 必须为零退出码，结果及日志工件必须来自本任务受控上传。静态成功仅完成该任务，当前 run 仍保持 BLOCKED。

## 下一位开发者优先处理

1. **设备命令持久化接线**：POST device-commands、命令结果查询、captures 列表与详情尚未注册，UI 会如实显示 404。使用 CaptureEngine.snapshot/from_snapshot 持久化；mock 必须一直标为 mock，不能标 RTL 或硬件。
2. **SSE/outbox 与健康**：events 接口尚未接通；当前 scheduler_ready=false，不能声称调度器已正常运行。持久化 outbox 需要投递/游标/重连实现。
3. **运行完整编排**：当前零费用 simulate 只排 contract.validate，尚无常驻 Worker 主动执行程序；静态任务完成后运行保持 BLOCKED，不能自动 VERIFIED。需要接入模型、EDA/CAD 与独立验收。
4. **服务身份与工件**：Worker 任务限定输入/输出上传接口、独立 verifier 接收证据等剩余路径须实现；保留验收资产不得暴露给生成侧。
5. **数据库和迁移**：当前自动建表；PostgreSQL/pgvector、Alembic 迁移和多进程并发尚未烟测。compose 是配置入口，不能把本机 SQLite 测试当作 PostgreSQL 验收。
6. **管理生态闭环**：真实模型、MCP/知识/报价来源、向量、插件进程沙箱、change apply、备份恢复等仍阻断或 NOT_RUN。报价/变更分析部分只存 internal，完整前端投影待做。
7. **RTL 工程验证**：UART 与 MMIO 尚缺共同总线仲裁；完整参数/故障矩阵、综合/CDC/时序和保留验收未跑；KiCad 参考工程、ERC 和独立连接对照未接。
8. **契约一致性与权限复核**：逐一核对原始 144 个操作实际注册状态、响应 Schema、引用项目一致性；防止通用 CRUD 绕开状态/权限/不可变规则。

接手前请看最新提交的审阅和验证记录，若本快照之后新增修复，以记录为准。没有自动发布、采购、烧录、远端强推或真实付费模型调用。

## 工作方式

- 代码可读性优先，关键事务、状态、权限和协议使用中文注释。
- 后端变更先写失败测试再实现；前端保留类型检查及关键交互测试。
- 不覆盖原始资料，不改写历史验证成绩；未执行为 NOT_RUN，工具不可用为明确阻断。
- 每次交接写下：改了什么、真实运行的命令/结果、剩余缺项、下一任务、分支/提交；提交可追踪，勿上传 .env、数据库或开发账号。

## 后续接续快照

2026-10-29 设备持久化接线已在 codex/device-persistence 分支完成当前范围，最新结果与限制见 [CONTINUATION-2026-10-29.md](CONTINUATION-2026-10-29.md)。前文保留为原交接历史；SSE 仍未实现。

2026-10-29 后续 SSE/outbox 接续见 [SSE-HANDOFF.md](SSE-HANDOFF.md)：订阅、断线游标、权限过滤及前端刷新已接线；最终回归与剩余限制以新记录为准。

2026-10-29 Worker 任务限定工件接口、常驻静态执行和 PostgreSQL16.15 并发实测见 [WORKER-HANDOFF.md](WORKER-HANDOFF.md)。

2026-10-29 独立验收服务身份、报告受控上传、签名证据持久化与有效性接续见 [VERIFICATION-HANDOFF.md](VERIFICATION-HANDOFF.md)。真实隔离验收后端与自动任务创建仍未接入。
