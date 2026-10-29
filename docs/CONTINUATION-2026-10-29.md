# 接续开发交接：设备持久化闭环

基线：`00bfb58`，分支：`codex/device-persistence`。本轮接续 HANDOFF 第一优先项；不是完整 84 项 P0 完成声明。

## 仓库扫描及队友进度

对基线全部 414 个 Git 文件做清点、字节哈希与文本结构扫描：119 个 Python 文件可解析，194 个 JSON 文件可解析，5 个二进制资料。阅读开发规格、总体方案目录、交接及实施记录，并重点核对当前设备纵向路径、事务、权限、工件、协议和前端调用。结构扫描不等于逐行安全审计。参考来源锁 179/179 文件哈希一致；legacy 和冻结规格保持原样。

队友已实现 FastAPI/SQLAlchemy 基础、认证/ACL、工件、任务租约、领域包、React 工作台、采集模型与部分 RTL。真实模型、完整编排、SSE、独立验收部署及工程工具仍未完成。基线本机测试为 137 passed、6 skipped、1 warning；6 个跳过为 Icarus/vvp 缺失，不能沿用队友机器的 143 passed 记录。

## 本轮新增

- POST 会话 `device-commands` 返回冻结契约的 202 ActionResult，GET `commands/{command_id}` 返回 DeviceCommandResult；保留原始 CRC 帧与稳定错误。
- 会话创建绑定不可变 SystemContract，持久化 CaptureEngine JSON 快照，支持重启续采、最近帧重放和序号冲突。
- 新增显式 `POST /api/v1/projects/{project_id}/device-sessions/{resource_id}/mock-inputs`，请求为 `{"segments":[{"value":0,"cycles":20},{"value":65535,"cycles":180}]}`。最多 256 段、总计 500000 个系统时钟，位宽来自会话契约。仅 OPEN mock 会话且 ARMED/CAPTURING 可调用。没有后台自动生成测量数据。
- DONE 时保存不可变 CaptureRecord、samples.bin 与 metadata.json；列表、详情、项目授权下载与 SHA256 校验沿用现有工件层。
- 前端按受理 ID 查询最终命令结果，增加明确标记的低→高 mock 测试输入按钮。

## 操作与验证

沿用 README 启动方法。导入 8/16 路样例，创建 mock 会话，读取能力，提交配置，ARM，点击“注入低→高测试信号（mock）”，读取分片。已保存采集可查看工件引用。旧版创建且没有快照的会话返回 SESSION_SNAPSHOT_REQUIRED，需新建会话。

实际运行：

- Python 全套：142 passed、6 skipped、1 依赖弃用警告。
- 新增设备 API：5 项先因 404 失败，再 5 passed；覆盖 8/16 路重启续采、字节结果与工件哈希、重复 ARM、序号冲突、中止、关闭、跨项目和撤权后重放。
- 前端：17 passed；TypeScript 检查和生产构建通过。新增命令提交/查询与显式刺激测试先因缺少模块失败，再通过。
- Ruff 全部应用、包、产品、验收、Worker 与测试通过。
- 本机 Python 依赖按 requirements.lock 安装；前端因无 npm 使用 pnpm 11.25.0 安装 package.json 依赖（未改 package-lock），实际 Vite 6.4.5 / Vitest 3.2.7 / TypeScript 5.9.3。尚未复核 npm ci 的锁文件环境。
- 冻结 API 144 操作当前注册 136 个；这是路由存在性统计，不是行为验收。

## 尚未完成与接手顺序

1. SSE/outbox：事件持久化表已存在，订阅、Last-Event-ID、7 天过期 410、15 秒心跳、撤权后断流尚未实现。scheduler_ready 仍 false。
2. 设备：真实浏览器端到端检查、并发 PostgreSQL、长会话配额、异步命令网络失败后的恢复 UI 待补。当前每次 mock-inputs 有界，但每会话输入事件历史没有总配额。
3. 未注册的 8 个冻结操作：clarifications/answers、events、Worker 上传 init/content/complete、Worker 输入工件读取、verification/evidence、evidence validity。
4. 常驻 Worker 与完整编排、Alembic/PostgreSQL、真实 Provider/EDA/CAD、KiCad、保留验收和 RTL 总线/故障矩阵，沿用 HANDOFF 缺项。
5. 独立审阅代理因账号额度限制启动失败，不能声称独立审阅通过；下一位开发者需审阅本分支再合并。当前仅作者检查和上述自动测试。

## 实施裁定

- Ruling: AGENTS 中历史目录名 fpga_platform 指该仓库工程根，当前在用户已关联的工作区分支接续；不修改参考资料及 legacy。风险：若团队另有目录布局要求需迁移路径。
- Ruling: mock-inputs 是显式开发扩展，不更改冻结 DeviceCommand Schema。风险：未来异步适配需保持独立路由与 mock 标识。
- Ruling: 旧会话缺少建立时快照时拒绝隐式恢复，要求重建。风险：旧会话不能继续操作，但不伪造历史契约。
- Ruling: 用户已授权分阶段上传，所以上传独立分支和交接包，不自动合并 main。

所有结果为软件 mock 集成；真实 RTL、综合、时序、板卡测试均未在本轮执行。没有付费模型调用、烧录、采购或部署。
