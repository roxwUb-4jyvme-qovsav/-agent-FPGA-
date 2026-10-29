# Web 工作台实施与交接

验证日期：2026-10-29。范围：`apps/web/**`。这是可连接真实 API 的 React 工作台，不代表完整首版 84 项 P0 已验收。

## 启动与构建

```powershell
cd apps/web
npm ci
npm run dev
npm run typecheck
npm test
npm run build
```

开发入口为 `http://127.0.0.1:5173/`，Vite 将 `/api` 代理到 `http://127.0.0.1:8765`。生产文件输出到 `apps/web/dist`，可由 FastAPI 同源挂载。前端不保存服务商 Key，不执行 EDA，也不自动重试写入或付费任务。

实际环境：Node.js 24.14.1、npm 11.19.1、React 19、TypeScript strict、Vite 6.4.5、Vitest 3.2.7。Vite 6 与最初 Vitest 2 的类型冲突已通过统一到 Vitest 3 解决。

## 页面与操作

| 页面 | 当前交互 |
| --- | --- |
| 初始化 / 登录 | 读取 setup/status；首次管理员密码至少 12 字符并二次确认；登录与退出真实会话；401 返回登录 |
| 工作台 / 项目 | 项目创建、项目切换、真实版本与运行数；预览并显式导入 8/16 通道规范为 DRAFT |
| 工程版本 | 版本创建、详情与 ETag；通用版本命令；展示摘要、版本、有效期与原因后批准并冻结 |
| 运行与任务 | 选择版本、模式与零默认预算创建运行；任务状态、问题回答、尝试、结果、输入快照；运行命令；SSE 连接状态 |
| 工件 / 证据 | 资源列表、哈希内容详情、工件下载；独立证据当前有效性与签名状态 |
| 知识库 | 集合、文档管理；显式选择集合检索；原文、定位与来源元数据 |
| 报价 | 供应商、器件、阶梯价、时效、币种与来源证据的 Schema 表单及详情 |
| 模型与扩展 | 模型、MCP、Skills、插件、知识/报价连接、扩展源、预算与账号管理；精确版本与操作命令 |
| 项目配置 | Agent 绑定、项目成员、变更、批准记录 |
| 产品上位机 | 创建并选择会话；读取能力/状态；配置分频、深度、触发通道与掩码；ARM/ABORT；命令查询；分片读取与保存采集列表 |
| 健康状态 | health 的数据库/调度器/EDA/CAD 状态，以及 system/status 的实际开发诊断 |

复杂资源提供契约字段表单与高级 JSON 编辑，均调用实际后端；不在浏览器内合成成功资源。引用 UUID、来源哈希与外部配置由操作者提供。密钥管理和文件上传入口本轮未实现；不能仅凭前端配置表单宣称模型或工具已就绪。

## API 与关键边界

- 固定前缀 `/api/v1`，资源包装遵循 `id/project_id/version/etag/state/payload/created_at/updated_at`；列表读取 `items/next_cursor`。资源表支持下一页，工作台项目/版本/运行概览读取首批最多 100 条。
- Session 使用 `user_id/global_role/project_roles/csrf_token`；创建项目所需 `team_id` 从 setup/status 获取。CSRF 仅驻留内存；请求采用同源 Cookie。
- POST 使用 `Idempotency-Key`，写请求使用 CSRF；更新与删除使用 GET 返回的 ETag。客户端拒绝没有 ETag 的覆盖写入，退出会话和规范明确例外的操作单独处理。
- 错误优先读取冻结契约的嵌套 `error.code/message/request_id/field_errors`，兼容诊断扩展的平铺错误。权限不足、412 冲突、网络离线与字段诊断均可见。
- 创建 freeze 批准会推进版本到 WAITING_APPROVAL 并增加版本号。因此提交审阅决定后重新读取 revision 和 approval-target，摘要未变化才用最新 `If-Match/expected_version` 冻结；摘要改变则拒绝继续。
- 状态徽标同时显示文字和颜色，刷新失败保留上次数据并标明；空、加载、失败与权限状态可见。文本与 JSON 通过 React 纯文本渲染。
- 上位机使用规范 `DeviceSessionInput/DeviceCommand`。SET_CONFIG 和 READ_CAPTURE 为小端 10 字节，序号为 16 位；不以字节偏移冒充采样偏移。
- 波形使用 Canvas，每像素保留高/低变化，缺帧为 null 且单独标色，拒绝混入旧 capture_id。只有取得本页确认配置后才显示相对采样时间，否则显示采样序号。未显示绝对测量时间。

来源：`packages/contracts/api/openapi.json`。`src/schemas.json` 与 `src/operations.json` 是前端所用契约快照；契约更新时需同步生成。主要代码分为 api、types、hooks、catalog、device、workflow、可复用组件与各业务页面。

## 实际验证

最终执行：

- `npm run typecheck`：通过。
- `npm test`：4 个测试文件，15 项通过。
- `npm run build`：通过；生产 JS 约 384 kB（gzip 108 kB），CSS 约 30 kB（gzip 7.4 kB）。

测试覆盖 CSRF/同源会话/幂等键、ETag 与删除保护、嵌套错误及断网不重试、16 路配置与分片编码、缺帧/旧采集/窄脉冲、登录失败与初始化确认，以及批准推进版本后的冻结并发保护。删除 ETag、嵌套错误与批准版本回归均观察到失败后修复并重新通过。

浏览器通过 Vite 代理连接真实 FastAPI，使用主代理指定的隔离 `.local/browser-qa.db` 与 `browser-data`；账号口令未记录到本文件，测试数据不应上传 GitHub。

1. 首次初始化管理员成功进入工作台。
2. 创建“逻辑分析仪 · 浏览器联调”项目成功，项目列表与选择器显示服务端资源。
3. 明确预览、导入 16 路规范成功，返回 DRAFT、3 个工件引用及基线摘要。
4. 首次冻结因批准推进版本产生 VERSION_CONFLICT；修复后同一已批准记录真实冻结到 FROZEN v3，GET ETag 显示版本 3。
5. 创建 simulate、费用上限 0 的运行成功，界面显示真实 QUEUED 运行与 contract_validation 任务，未声明任务完成。
6. 创建 mock 采集会话成功到 OPEN，界面标明“虚拟模式 · 非硬件实测”。
7. 发送设备能力命令、读取保存采集时，当前服务返回 HTTP 404；界面展示错误，没有生成或绘制虚假样本。SSE 连接失败同样可见。

本机截图位于 `apps/web/.local/qa/`：`workspace-overview.png`、`revision-frozen.png`、`run-queued.png`、`device-unavailable.png`。截图是隔离 QA 记录，未作为真实工程验收证据上传。

## 已知缺项与下一步

- 后端设备命令、命令查询、采集列表/详情和 SSE 尚未接线。上位机波形算法和交互已实现，但本轮没有通过真实设备 API 获取波形；端到端虚拟采集与硬件实测均记 NOT_RUN。
- 外部模型、MCP、Skills 和插件、知识解析、报价与验证工具的完整调用流程未逐项完成浏览器验收；未实现端点的错误会显示。配置登记不代表真实连接测试通过。
- 本轮浏览器流程使用隔离 SQLite 开发库，不宣称满足 PostgreSQL 并发验收。
- 未执行 4096×16 波形首屏/交互性能实测、P95、完整小屏浏览器回归、跨项目角色矩阵与自动化浏览器回归。
- 文件上传、密钥管理、专门的源码/图纸预览、语义差异图等仍需继续建设；复杂资源当前以契约表单和 JSON 详情管理。
- 当前会话中断后的审批恢复可在批准记录和版本命令中手动核对；后续应增加直接继续既有批准的友好入口。

交接时已停止新增功能，等待主代理集成和 GitHub 发布；本子任务未运行 Git 提交或推送操作。
