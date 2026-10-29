# SSE 接续实施与交接（2026-10-29）

接续基线 `73d501b`，沿用 `codex/device-persistence` 与草稿 PR #1。本轮处理原交接清单的 SSE/outbox 项。

## 已实现

- `GET /api/v1/projects/{project_id}/events` 返回 SSE；`id` 是 Event.id，普通 `data` 通过冻结 Event Schema。每批最多 100 行，短事务查询已提交 outbox，默认每 0.5 秒查询、每 15 秒发注释心跳。
- 业务事件创建时冻结资源版本、请求标识和摘要；请求 ContextVar 穿过线程池。Worker 的任务事件引用所属 run，消息或完整 Prompt 不进入 SSE。
- 每批重新验证 Cookie、有效用户和项目成员，资源继续经过知识/工件 ACL。撤权/过期后发送 `access_denied` 控制帧并断流；头发送前失败仍返回实际 HTTP 401/404。
- `Last-Event-ID` 用于重连，拒绝非法、未来或其他项目游标；七天前的游标返回 410。首连不带游标时从项目当前高水位订阅，连接建立后客户端刷新 REST 状态；闲置超过七天的项目也可保持新订阅在线。
- 前端使用 Fetch 读取流，自动重连时携带游标、去重，并处理 410；按当前项目批量刷新 GET 资源，切换项目/退出时取消旧流。运行页显示最近 30 条摘要和连接状态。
- `scripts/package_handoff.py` 从指定 Git 提交生成源码包、逐文件 SHA256 清单及外部归档哈希，排除历史 deliveries，避免交付包递归膨胀。

## 兼容与裁定

- Ruling: 首连采用实时高水位加 REST 刷新；显式 `Last-Event-ID: 0` 用于重放可用历史。风险：首连不展示连接之前的旧动态，但业务状态由 REST 读取。
- Ruling: 老 outbox 行缺少发生时版本/请求快照时仅推进 id-only 检查点，不用当前版本填造历史。风险：升级前事件不显示在动态面板。
- Ruling: `?follow=false` 是有限批次的诊断扩展，保留 SSE 格式，用于客户端与接口测试；默认 follow=true 为长连接。
- Ruling: 不物理删除七天前事件，以保留现有审计和 mock 输入溯源；SSE 投递窗口为七天，物理清理需另行冻结审计策略。风险：数据库事件会继续增长。
- Ruling: 沿用 outbox 表直接投递已提交行；PostgreSQL 同项目事件写入使用事务级建议锁保持序号提交顺序。Worker claim 在任务行选择后、首条事件前按统一顺序预取全部涉及项目锁，避免跨项目反序。当前仅 SQLite 行为经过实测，PostgreSQL 多事务/多进程尚未烟测。

控制帧 `resync`、`access_denied`、`unavailable` 与普通业务 Event 区分；id-only 检查点不包含资源、正文或凭据。允许项目序号存在间隙。

## 实际验证记录

后端首轮 5 项红测因未注册端点返回 404；实现后通过。另加入真实 TCP/uvicorn 流测试，验证提交后接收、加速心跳和撤权断流。闲置八天项目曾触发错误 resync，已观察失败并修复。新增应用重启重放及 Worker 消息脱敏回归。

前端新增测试先因缺少模块失败，随后通过；覆盖跨块 CRLF、多行 data、注释/id-only、重连 Last-Event-ID、重复去重、410 重同步、跨项目数据拒绝、权限终止。最终回归：Python **151 passed、6 skipped、1 warning**；6 项为 Icarus/vvp 未安装的 RTL 测试，不能计作通过。前端 **24 passed（7 文件）**；TypeScript 检查及 Vite 生产构建通过；Ruff 检查和 pip check 通过。实际工具链沿用本机 requirements.lock 与前轮 pnpm 环境。

独立只读审阅发现一个 Important（多项目事件锁反序）及两个 Minor。锁顺序问题已用模拟锁顺序约束观察失败，再预取整个事务锁集合修复；该测试不是实际 PostgreSQL 并发。旧订阅取消后401仍触发全局退出，按对当前会话的影响升级为 Important，已观察失败并加取消检查修复。另补红绿回归：撤权后 GET 返回401/403/404时清空旧数据和ETag。

Final: minor (deferred): 持久化 `_stream` 被外部损坏时的 ContractError 仍会中断流并导致重试；正常写入路径没有发现可生成此数据。下一阶段需要损坏事件隔离和诊断策略，不能补造历史事件。

Final: Ruling: PostgreSQL 实测、真实浏览器断网/登录竞争、84 项完整验收和打包脚本不纳入本次独立审阅结论；分别保留 NOT_RUN 或本地归档完整性校验，不能把自动测试或源码审阅当成这些验收。风险：生产部署前仍可能遇到环境、压力或浏览器特有问题。

## 下一步

1. PostgreSQL 多进程事件提交顺序、项目锁争用与死锁重试实测；大量连接、慢消费者和恢复性能。
2. 真实浏览器完整流程与更多角色/会话过期矩阵；事件接口不是完整调度器，scheduler_ready 仍 false。
3. Worker 任务限定的输入下载与上传路径、常驻零预算静态 Worker；接续原 HANDOFF 第三、四项。
4. Alembic、完整编排、独立 verifier 入库、Provider/EDA/CAD 和完整验收仍沿用既有缺项。未运行项保持 NOT_RUN。

所有 mock/软件结果沿用原标签；没有硬件实测或真实付费调用。

打包实际烟测发现 Windows 中文仓库路径被 subprocess 默认 GBK 解码而失败；已显式以 UTF-8 读取 Git 路径和提交输出，再次真实打包成功。最终源码包按其 source_commit 固定内容，包内 package_manifest.json 逐文件记录 SHA256，下载后可复核。
